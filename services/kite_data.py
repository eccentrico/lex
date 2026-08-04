"""
Kite Data Service Module

A clean, reusable service for fetching market data from Zerodha Kite Connect API v4.
This module is read-only and designed for use in LangGraph nodes (price fetcher, technical analyst).

Authentication Options:
    1. Auto-authenticate using auth/kite_auth.py module:
       kite_data.authenticate()  # Uses refresh token or performs TOTP login
    
    2. Auto-load from saved tokens (kite_tokens.json):
       # Token is automatically loaded during initialization if file exists
    
    3. Manually set access token:
       kite_data.set_access_token("your_access_token_here")

Usage:
    from services.kite_data import kite_data
    
    # Authenticate (uses auth/kite_auth.py module)
    kite_data.authenticate()
    
    # Or token is auto-loaded from kite_tokens.json if available
    
    # Get last traded price
    price = kite_data.get_ltp("RELIANCE")
    
    # Get quotes for multiple symbols
    quotes = kite_data.get_quotes(["RELIANCE", "TCS", "INFY"])
    
    # Get historical data
    df = kite_data.get_historical_data("RELIANCE", "2024-01-01", "2024-12-31", "day")
    
    # Get holdings
    holdings = kite_data.get_holdings()

Integration with LangGraph:
    In your price_fetcher_node:
        from services.kite_data import kite_data
        
        def price_fetcher_node(state: AnalysisState) -> AnalysisState:
            # Ensure authenticated (auto-loads from kite_tokens.json or calls authenticate())
            if not kite_data._access_token:
                kite_data.authenticate()
            
            prices = {}
            for symbol in state["stocks"]:
                quote = kite_data.get_quotes([symbol])
                if quote:
                    prices[symbol] = {
                        "ohlcv": kite_data.get_historical_data(symbol, ...),
                        "current_price": quote[symbol]["last_price"],
                        ...
                    }
            return {"prices": prices}
"""

import os
import json
import logging
import threading
from typing import Dict, List, Optional, Union, Any
from datetime import datetime, timedelta
import pandas as pd
from dotenv import load_dotenv
from kiteconnect import KiteConnect

from services.paths import lex_home

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _token_file() -> str:
    """Resolve the Kite token file path under the Lex home directory.

    Duplicated from services/kite_auth.py (rather than imported) so this
    read-only data service doesn't pull in kite_auth's login-flow deps
    (requests, pyotp) or its module-level KiteConnect side effects. Both
    modules must read/write the same file — see services/kite_auth.py's
    _token_file() for the paired implementation.
    """
    return os.environ.get(
        "KITE_TOKENS_PATH", str(lex_home() / "kite_tokens.json")
    )


class KiteDataService:
    """
    Singleton service for fetching market data from Kite Connect API.
    
    This service provides read-only access to market data including:
    - Real-time quotes and LTP
    - Historical OHLCV data
    - Portfolio holdings
    
    Authentication must be set via set_access_token() before use.
    """
    
    _instance: Optional['KiteDataService'] = None
    _initialized: bool = False
    
    def __new__(cls):
        """Singleton pattern: return existing instance or create new one."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        """Initialize the service (only once)."""
        if self._initialized:
            return
        
        self._kite: Optional[KiteConnect] = None
        self._api_key: Optional[str] = None
        self._access_token: Optional[str] = None
        self._token_saved_at: Optional[datetime] = None  # when the current token was obtained
        self._instruments_cache: Optional[pd.DataFrame] = None
        self._instruments_cache_time: Optional[datetime] = None
        self._mf_instruments_cache: Optional[pd.DataFrame] = None
        self._mf_instruments_cache_time: Optional[datetime] = None
        self._cache_ttl: timedelta = timedelta(hours=24)  # Cache instruments for 24 hours
        self._token_max_age: timedelta = timedelta(hours=6)  # Proactively refresh tokens older than 6h
        # Reentrant lock: serialises set_access_token + the first API call that follows,
        # preventing concurrent auth-retry races when parallel LangGraph nodes (e.g.
        # technical_analyst + fundamental_analyst) hit an expired token simultaneously.
        self._auth_lock: threading.RLock = threading.RLock()
        # Resolved under the Lex home directory (KITE_TOKENS_PATH env override
        # supported) — see module-level _token_file() above.
        self._token_file: str = _token_file()
        
        # Load API key from environment
        self._api_key = os.getenv("KITE_API_KEY")
        if not self._api_key:
            logger.warning("KITE_API_KEY not found in environment variables")
        else:
            self._kite = KiteConnect(api_key=self._api_key)
            
            # Try to auto-load access token from saved tokens file
            self._try_load_access_token()
        
        self._initialized = True
        logger.info("KiteDataService initialized")
    
    def _try_load_access_token(self) -> None:
        """
        Try to load access token from saved tokens file (kite_tokens.json).
        This integrates with the auth/kite_auth.py module's token persistence.
        Rejects tokens saved before midnight IST today — Kite invalidates all
        tokens at midnight IST regardless of how recently they were issued.
        """
        if os.path.exists(self._token_file):
            try:
                with open(self._token_file, "r") as f:
                    tokens = json.load(f)
                    access_token = tokens.get("access_token")

                    # Reject tokens from a previous calendar day (IST).
                    # Kite invalidates all tokens at midnight IST — a token saved
                    # at 11:55 PM is dead by 12:05 AM regardless of its age in hours.
                    saved_at = tokens.get("saved_at")
                    if saved_at:
                        try:
                            from datetime import timezone
                            IST = timezone(timedelta(hours=5, minutes=30))
                            now_ist = datetime.now(IST)
                            midnight_ist = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
                            saved_dt = datetime.fromisoformat(saved_at)
                            if saved_dt.tzinfo is None:
                                saved_dt = saved_dt.replace(tzinfo=IST)
                            if saved_dt < midnight_ist:
                                logger.info(f"Saved token is from a previous day (saved_at={saved_at}) — skipping stale token")
                                return
                        except (ValueError, TypeError) as e:
                            logger.warning(f"Could not parse saved_at timestamp '{saved_at}': {e} — skipping stale token check")

                    if access_token and self._kite:
                        self._access_token = access_token
                        self._kite.set_access_token(access_token)
                        # Record when this token was saved so we can proactively refresh later.
                        # Strip timezone info — _is_token_stale uses naive datetime.now()
                        # and mixing aware/naive datetimes raises TypeError on subtraction.
                        try:
                            saved_naive = datetime.fromisoformat(saved_at) if saved_at else datetime.now()
                            self._token_saved_at = saved_naive.replace(tzinfo=None)
                        except (ValueError, TypeError):
                            self._token_saved_at = datetime.now()
                        logger.info("Auto-loaded access token from saved tokens file")
            except Exception as e:
                logger.warning(f"Could not load access token from file: {e}")
    
    def authenticate(self) -> str:
        """
        Authenticate using the auth/kite_auth.py module's HTTP login flow.
        Performs a full credentials + TOTP login (Zerodha does not support
        refresh tokens for standard API accounts).

        Returns:
            str: Access token

        Raises:
            RuntimeError: If authentication fails
            ImportError: If auth module is not available
        """
        try:
            # Import auth module (optional dependency)
            from auth.kite_auth import get_or_renew_access_token
        except ImportError:
            raise ImportError(
                "auth.kite_auth module not found. "
                "Install dependencies or use set_access_token() manually."
            )
        
        try:
            access_token = get_or_renew_access_token()
            self.set_access_token(access_token)
            return access_token
        except Exception as e:
            raise RuntimeError(f"Authentication failed: {e}")
    
    def set_access_token(self, access_token: str) -> None:
        """
        Set the access token for authenticated API calls.
        
        This should be called after TOTP authentication succeeds.
        
        Args:
            access_token: Access token obtained from Kite Connect authentication
        
        Raises:
            RuntimeError: If KiteConnect instance is not initialized
        """
        if not self._kite:
            raise RuntimeError(
                "KiteConnect not initialized. Ensure KITE_API_KEY is set in environment."
            )
        
        with self._auth_lock:
            self._access_token = access_token
            self._token_saved_at = datetime.now()
            self._kite.set_access_token(access_token)
            logger.info("Access token set successfully")
            # Clear instruments cache to force refresh
            self._instruments_cache = None
            self._instruments_cache_time = None
    
    def _is_token_stale(self) -> bool:
        """Return True if the current token is older than _token_max_age (6h) or was never set."""
        if self._token_saved_at is None:
            return True
        return datetime.now() - self._token_saved_at > self._token_max_age

    def _is_auth_error(self, error: Exception) -> bool:
        """
        Check if an exception is an authentication error.
        
        Args:
            error: Exception to check
            
        Returns:
            True if error is an authentication error
        """
        error_str = str(error).lower()
        auth_keywords = [
            "incorrect `api_key` or `access_token`",
            "invalid access token",
            "authentication failed",
            "unauthorized",
            "access_token",
            "api_key"
        ]
        return any(keyword in error_str for keyword in auth_keywords)
    
    def _retry_with_auth(self, func, *args, **kwargs):
        """
        Execute a function and retry with re-authentication if auth error occurs.
        
        Args:
            func: Function to execute
            *args: Positional arguments for func
            **kwargs: Keyword arguments for func
            
        Returns:
            Result of func execution
        """
        # Proactively refresh the token before attempting the call if it is stale,
        # rather than waiting for the API to return an auth error.
        if self._access_token and self._is_token_stale():
            logger.info("Access token is older than 6h — proactively refreshing before API call")
            with self._auth_lock:
                if self._is_token_stale():  # re-check under lock to avoid double-refresh
                    try:
                        self.authenticate()
                        logger.info("Proactive token refresh successful")
                    except Exception as refresh_err:
                        logger.warning(f"Proactive token refresh failed: {refresh_err} — proceeding with existing token")

        try:
            return func(*args, **kwargs)
        except Exception as e:
            if self._is_auth_error(e):
                logger.warning(f"Authentication error detected: {e}. Attempting re-authentication...")
                # Hold the auth lock for the full authenticate → retry sequence so that
                # parallel agents (e.g. technical + fundamental analysts) don't race to
                # call set_access_token simultaneously and corrupt the token state.
                with self._auth_lock:
                    # If another thread already refreshed the token while we were
                    # waiting on the lock, the retry below will succeed immediately.
                    try:
                        self.authenticate()
                        logger.info("Re-authentication successful. Retrying operation...")
                        return func(*args, **kwargs)
                    except Exception as auth_error:
                        logger.error(f"Re-authentication failed: {auth_error}")
                        raise
            else:
                # Not an auth error, re-raise
                raise
    
    def _get_instruments(self) -> pd.DataFrame:
        """
        Get and cache instruments list from Kite Connect.
        
        Returns:
            DataFrame with columns: instrument_token, tradingsymbol, exchange, etc.
        
        Raises:
            RuntimeError: If not authenticated
        """
        if not self._access_token:
            raise RuntimeError(
                "Not authenticated. Call set_access_token() first."
            )
        
        # Return cached instruments if still valid
        if (self._instruments_cache is not None and 
            self._instruments_cache_time is not None and
            datetime.now() - self._instruments_cache_time < self._cache_ttl):
            return self._instruments_cache
        
        try:
            # Fetch instruments for NSE equity segment
            instruments = self._kite.instruments("NSE")
            df = pd.DataFrame(instruments)
            
            # Cache the result
            self._instruments_cache = df
            self._instruments_cache_time = datetime.now()
            
            logger.info(f"Fetched and cached {len(df)} NSE instruments")
            return df
            
        except Exception as e:
            logger.error(f"Error fetching instruments: {e}")
            # Return cached data if available, even if expired
            if self._instruments_cache is not None:
                logger.warning("Using expired instruments cache")
                return self._instruments_cache
            raise

    def _get_mf_instruments(self) -> pd.DataFrame:
        """
        Get and cache the mutual fund scheme master + latest NAV from Kite Connect.

        Returns:
            DataFrame with columns: tradingsymbol (the AMFI scheme code), name,
            amc, plan, scheme_type, last_price, last_price_date, etc.
        """
        if not self._access_token:
            raise RuntimeError(
                "Not authenticated. Call set_access_token() first."
            )

        if (self._mf_instruments_cache is not None and
            self._mf_instruments_cache_time is not None and
            datetime.now() - self._mf_instruments_cache_time < self._cache_ttl):
            return self._mf_instruments_cache

        try:
            instruments = self._retry_with_auth(self._kite.mf_instruments)
            df = pd.DataFrame(instruments)
            self._mf_instruments_cache = df
            self._mf_instruments_cache_time = datetime.now()
            logger.info(f"Fetched and cached {len(df)} MF instruments")
            return df
        except Exception as e:
            logger.error(f"Error fetching MF instruments: {e}")
            if self._mf_instruments_cache is not None:
                logger.warning("Using expired MF instruments cache")
                return self._mf_instruments_cache
            raise

    def get_mf_quote(self, scheme_codes: List[str]) -> Dict[str, Dict[str, Any]]:
        """
        Latest NAV + scheme metadata for a list of AMFI scheme codes.

        Kite's MF `tradingsymbol` field is the AMFI scheme code; this is the
        canonical identifier used across every mutual-fund tool.

        Args:
            scheme_codes: AMFI scheme codes (as returned by fund_search).

        Returns:
            Dict mapping scheme_code -> {scheme_code, name, amc, plan,
            scheme_type, nav, nav_date}. A code with no matching instrument is
            simply absent from the result.
        """
        if not scheme_codes:
            return {}
        try:
            df = self._get_mf_instruments()
        except Exception as e:
            logger.error(f"get_mf_quote: could not load MF instruments: {e}")
            return {}
        result = {}
        for code in scheme_codes:
            match = df[df["tradingsymbol"].astype(str) == str(code)]
            if len(match) == 0:
                continue
            row = match.iloc[0]
            result[str(code)] = {
                "scheme_code": str(code),
                "name": row.get("name", ""),
                "amc": row.get("amc", ""),
                "plan": row.get("plan", ""),
                "scheme_type": row.get("scheme_type", ""),
                "nav": float(row.get("last_price", 0) or 0),
                "nav_date": str(row.get("last_price_date", "")),
            }
        return result

    def _symbol_to_token(self, symbol: str) -> Optional[int]:
        """
        Convert trading symbol to instrument token.
        
        Args:
            symbol: Trading symbol (e.g., "RELIANCE", "TCS")
        
        Returns:
            Instrument token or None if not found
        """
        try:
            instruments = self._get_instruments()
            # Remove NSE: prefix if present
            clean_symbol = symbol.replace("NSE:", "").strip().upper()
            
            # Find matching instrument
            match = instruments[
                (instruments["tradingsymbol"] == clean_symbol) &
                (instruments["exchange"] == "NSE") &
                (instruments["instrument_type"] == "EQ")
            ]
            
            if len(match) > 0:
                token = int(match.iloc[0]["instrument_token"])
                return token
            
            logger.warning(f"Instrument token not found for symbol: {symbol}")
            return None
            
        except Exception as e:
            logger.error(f"Error converting symbol to token for {symbol}: {e}")
            return None
    
    def _symbols_to_tokens(self, symbols: List[str]) -> Dict[str, int]:
        """
        Convert multiple trading symbols to instrument tokens.
        
        Args:
            symbols: List of trading symbols
        
        Returns:
            Dictionary mapping symbol -> instrument_token
        """
        token_map = {}
        for symbol in symbols:
            token = self._symbol_to_token(symbol)
            if token:
                token_map[symbol] = token
        return token_map
    
    def get_ltp(self, symbol: str) -> Optional[float]:
        """
        Get last traded price (LTP) for a symbol.
        
        Args:
            symbol: Trading symbol (e.g., "RELIANCE", "TCS")
        
        Returns:
            Last traded price as float, or None on error
        
        Example:
            >>> price = kite_data.get_ltp("RELIANCE")
            >>> print(price)
            2456.75
        """
        if not self._access_token:
            # Try to authenticate if no token
            try:
                logger.info("No access token found. Attempting authentication...")
                self.authenticate()
            except Exception as e:
                logger.error(f"Authentication failed: {e}")
                return None
        
        def _fetch_ltp():
            """Inner function to fetch LTP."""
            token = self._symbol_to_token(symbol)
            if not token:
                return None
            
            # Format: "NSE:RELIANCE" or just token
            ltp_data = self._kite.ltp(f"NSE:{symbol.replace('NSE:', '').strip()}")
            
            if isinstance(ltp_data, dict):
                # Extract price from response
                price_key = f"NSE:{symbol.replace('NSE:', '').strip()}"
                if price_key in ltp_data:
                    price = float(ltp_data[price_key].get("last_price", 0))
                    if price <= 0:
                        logger.warning(f"LTP for {symbol} is {price} — treating as unavailable")
                        return None
                    return price
                # Fallback: try first value
                if ltp_data:
                    first_value = next(iter(ltp_data.values()))
                    price = float(first_value.get("last_price", 0))
                    if price <= 0:
                        logger.warning(f"LTP fallback for {symbol} is {price} — treating as unavailable")
                        return None
                    return price
            
            return None
        
        try:
            return self._retry_with_auth(_fetch_ltp)
        except Exception as e:
            logger.error(f"Error fetching LTP for {symbol}: {e}")
            return None
    
    def get_quotes(self, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
        """
        Batch fetch quotes for multiple symbols.
        
        Args:
            symbols: List of trading symbols
        
        Returns:
            Dictionary mapping symbol -> quote data with keys:
            - last_price: float
            - open: float
            - high: float
            - low: float
            - close: float (previous close)
            - volume: int
            - change_pct: float
            - oi: int (open interest, if applicable)
            - timestamp: datetime
        
        Example:
            >>> quotes = kite_data.get_quotes(["RELIANCE", "TCS"])
            >>> print(quotes["RELIANCE"]["last_price"])
            2456.75
        """
        if not self._access_token:
            # Try to authenticate if no token
            try:
                logger.info("No access token found. Attempting authentication...")
                self.authenticate()
            except Exception as e:
                logger.error(f"Authentication failed: {e}")
                return {}
        
        if not symbols:
            return {}
        
        def _fetch_quotes():
            """Inner function to fetch quotes."""
            # Format symbols with NSE: prefix
            formatted_symbols = [
                f"NSE:{s.replace('NSE:', '').strip()}" 
                for s in symbols
            ]
            
            # Batch fetch quotes
            quotes_data = self._kite.quote(formatted_symbols)
            
            # Parse and structure the response
            result = {}
            for symbol in symbols:
                clean_symbol = symbol.replace("NSE:", "").strip()
                quote_key = f"NSE:{clean_symbol}"
                
                if quote_key in quotes_data:
                    quote = quotes_data[quote_key]
                    depth = quote.get("depth", {})
                    ohlc = quote.get("ohlc", {})
                    
                    last_price = quote.get("last_price", 0)
                    prev_close = ohlc.get("close", last_price)
                    change = last_price - prev_close if prev_close else 0
                    change_pct = (change / prev_close * 100) if prev_close else 0
                    
                    result[symbol] = {
                        "last_price": float(last_price),
                        "open": float(ohlc.get("open", 0)),
                        "high": float(ohlc.get("high", 0)),
                        "low": float(ohlc.get("low", 0)),
                        "close": float(prev_close),
                        "volume": int(quote.get("volume", 0)),
                        "change": float(change),
                        "change_pct": float(change_pct),
                        "oi": int(quote.get("oi", 0)),
                        "timestamp": quote.get("timestamp"),
                    }
                else:
                    logger.warning(f"Quote not found for symbol: {symbol}")
            
            return result
        
        try:
            return self._retry_with_auth(_fetch_quotes)
        except Exception as e:
            logger.error(f"Error fetching quotes for symbols {symbols}: {e}")
            return {}
    
    def get_historical_data(
        self,
        symbol: str,
        from_date: Union[str, datetime],
        to_date: Union[str, datetime],
        interval: str = "day"
    ) -> pd.DataFrame:
        """
        Get historical OHLCV data for a symbol.
        
        Args:
            symbol: Trading symbol (e.g., "RELIANCE", "TCS")
            from_date: Start date (str in YYYY-MM-DD or datetime)
            to_date: End date (str in YYYY-MM-DD or datetime)
            interval: Data interval - "minute", "3minute", "5minute", "15minute",
                     "30minute", "hour", "day", "week", "month"
        
        Returns:
            DataFrame with columns: timestamp (as index), open, high, low, close, volume
            Empty DataFrame on error
        
        Example:
            >>> df = kite_data.get_historical_data(
            ...     "RELIANCE", 
            ...     "2024-01-01", 
            ...     "2024-12-31", 
            ...     "day"
            ... )
            >>> print(df.head())
        """
        if not self._access_token:
            # Try to authenticate if no token
            try:
                logger.info("No access token found. Attempting authentication...")
                self.authenticate()
            except Exception as e:
                logger.error(f"Authentication failed: {e}")
                return pd.DataFrame()
        
        # Convert dates to datetime if strings (do this before API call)
        if isinstance(from_date, str):
            from_date = datetime.strptime(from_date, "%Y-%m-%d")
        if isinstance(to_date, str):
            to_date = datetime.strptime(to_date, "%Y-%m-%d")
        
        def _fetch_historical_data():
            """Inner function to fetch historical data."""
            token = self._symbol_to_token(symbol)
            if not token:
                logger.warning(f"Instrument token not found for {symbol}")
                return pd.DataFrame()
            
            # Fetch historical data
            historical_data = self._kite.historical_data(
                instrument_token=token,
                from_date=from_date,
                to_date=to_date,
                interval=interval,
                continuous=False,
                oi=False
            )
            
            if not historical_data:
                logger.warning(f"No historical data returned for {symbol}")
                return pd.DataFrame()
            
            # Convert to DataFrame
            df = pd.DataFrame(historical_data)
            
            # Rename and standardize columns
            column_mapping = {
                "date": "timestamp",
                "open": "open",
                "high": "high",
                "low": "low",
                "close": "close",
                "volume": "volume",
            }
            
            # Rename columns
            df = df.rename(columns=column_mapping)
            
            # Ensure timestamp is datetime
            if "timestamp" in df.columns:
                df["timestamp"] = pd.to_datetime(df["timestamp"])
                df = df.set_index("timestamp")
            
            # Select and order columns
            required_cols = ["open", "high", "low", "close", "volume"]
            available_cols = [col for col in required_cols if col in df.columns]
            df = df[available_cols]
            
            # Sort by timestamp
            df = df.sort_index()
            
            logger.info(
                f"Fetched {len(df)} records for {symbol} "
                f"from {from_date.date()} to {to_date.date()} ({interval})"
            )
            
            return df
        
        try:
            return self._retry_with_auth(_fetch_historical_data)
        except Exception as e:
            logger.error(f"Error fetching historical data for {symbol}: {e}")
            return pd.DataFrame()
    
    def get_holdings(self) -> List[Dict[str, Any]]:
        """
        Get current portfolio holdings.
        
        Returns:
            List of dictionaries with holding information:
            - tradingsymbol: str
            - exchange: str
            - quantity: int
            - average_price: float
            - last_price: float
            - pnl: float
            - etc.
        
        Example:
            >>> holdings = kite_data.get_holdings()
            >>> for holding in holdings:
            ...     print(f"{holding['tradingsymbol']}: {holding['quantity']} shares")
        """
        if not self._access_token:
            # Try to authenticate if no token
            try:
                logger.info("No access token found. Attempting authentication...")
                self.authenticate()
            except Exception as e:
                logger.error(f"Authentication failed: {e}")
                return []
        
        def _fetch_holdings():
            """Inner function to fetch holdings."""
            holdings = self._kite.holdings()
            
            # Convert to list of dicts (already in correct format)
            result = []
            for holding in holdings:
                settled_qty = int(holding.get("quantity", 0))
                t1_qty = int(holding.get("t1_quantity", 0))
                effective_qty = settled_qty + t1_qty
                if effective_qty <= 0:
                    continue
                result.append({
                    "tradingsymbol": holding.get("tradingsymbol", ""),
                    "exchange": holding.get("exchange", ""),
                    "quantity": effective_qty,
                    "average_price": float(holding.get("average_price", 0)),
                    "last_price": float(holding.get("last_price", 0)),
                    "pnl": float(holding.get("pnl", 0)),
                    "product": holding.get("product", ""),
                    "collateral_quantity": int(holding.get("collateral_quantity", 0)),
                    "collateral_type": holding.get("collateral_type", ""),
                })
            
            logger.debug(f"Fetched {len(result)} holdings")
            return result
        
        try:
            return self._retry_with_auth(_fetch_holdings)
        except Exception as e:
            logger.error(f"Error fetching holdings: {e}")
            return []


    def get_nifty50_closes(
        self,
        from_date: Union[str, datetime],
        to_date: Union[str, datetime],
    ) -> List[float]:
        """
        Fetch NIFTY 50 index daily closing prices for market regime detection.

        Uses the well-known Kite Connect instrument token for NIFTY 50 (256265)
        as a reliable fallback when the instruments list lookup is unavailable.
        The index is fetched from the NSE exchange.

        Args:
            from_date: Start date (str YYYY-MM-DD or datetime).
            to_date:   End date   (str YYYY-MM-DD or datetime).

        Returns:
            List of daily closing prices (oldest first), or [] on failure.
        """
        # NIFTY 50 index instrument token on Kite Connect (NSE, permanent)
        NIFTY50_TOKEN = 256265

        if not self._access_token:
            try:
                self.authenticate()
            except Exception as e:
                logger.error(f"get_nifty50_closes: auth failed: {e}")
                return []

        if isinstance(from_date, str):
            from_date = datetime.strptime(from_date, "%Y-%m-%d")
        if isinstance(to_date, str):
            to_date = datetime.strptime(to_date, "%Y-%m-%d")

        def _fetch():
            data = self._kite.historical_data(
                instrument_token=NIFTY50_TOKEN,
                from_date=from_date,
                to_date=to_date,
                interval="day",
            )
            return [float(d["close"]) for d in data if d.get("close")]

        try:
            closes = self._retry_with_auth(_fetch)
            logger.info(f"Fetched {len(closes)} NIFTY 50 closes for regime detection")
            return closes
        except Exception as e:
            logger.warning(f"get_nifty50_closes failed: {e}")
            return []

    _INDEX_TOKENS: Dict[str, int] = {
        "nifty50":   256265,
        "banknifty": 260105,
        "midcap100": 288009,
    }

    def get_index_closes(
        self,
        index_key: str,
        from_date: Union[str, datetime],
        to_date: Union[str, datetime],
    ) -> Dict[str, float]:
        """
        Fetch daily closing prices for a named NSE index, keyed by date string.

        Args:
            index_key: One of 'nifty50', 'banknifty', 'midcap100'.
            from_date: Start date (str YYYY-MM-DD or datetime).
            to_date:   End date   (str YYYY-MM-DD or datetime).

        Returns:
            Dict mapping 'YYYY-MM-DD' -> close price, oldest-to-newest.
        """
        token = self._INDEX_TOKENS.get(index_key)
        if token is None:
            logger.warning(f"get_index_closes: unknown index key '{index_key}'")
            return {}

        if not self._access_token:
            try:
                self.authenticate()
            except Exception as e:
                logger.error(f"get_index_closes: auth failed: {e}")
                return {}

        if isinstance(from_date, str):
            from_date = datetime.strptime(from_date, "%Y-%m-%d")
        if isinstance(to_date, str):
            to_date = datetime.strptime(to_date, "%Y-%m-%d")

        def _fetch():
            data = self._kite.historical_data(
                instrument_token=token,
                from_date=from_date,
                to_date=to_date,
                interval="day",
            )
            result = {}
            for d in data:
                if not d.get("close") or not d.get("date"):
                    continue
                dt = d["date"]
                date_str = dt.strftime("%Y-%m-%d") if hasattr(dt, "strftime") else str(dt)[:10]
                result[date_str] = float(d["close"])
            return result

        try:
            closes = self._retry_with_auth(_fetch)
            logger.info(f"Fetched {len(closes)} {index_key} closes")
            return closes
        except Exception as e:
            logger.warning(f"get_index_closes({index_key}) failed: {e}")
            return {}


# Singleton instance
kite_data = KiteDataService()


# Example usage (commented out)
"""
if __name__ == "__main__":
    # Option 1: Auto-authenticate using auth/kite_auth.py module
    # This will use refresh token if available, or perform full TOTP login
    try:
        kite_data.authenticate()
    except Exception as e:
        print(f"Auto-authentication failed: {e}")
        # Fallback: manually set token if you have it
        # kite_data.set_access_token("your_access_token_here")
    
    # Option 2: Token is auto-loaded from kite_tokens.json if available
    # (set_access_token() is called automatically during initialization)
    
    # Option 3: Manually set access token
    # access_token = os.getenv("KITE_ACCESS_TOKEN")
    # if access_token:
    #     kite_data.set_access_token(access_token)
    
    # Check if authenticated
    if not kite_data._access_token:
        print("Not authenticated. Call authenticate() or set_access_token() first.")
        exit(1)
    
    # Get LTP
    price = kite_data.get_ltp("RELIANCE")
    print(f"RELIANCE LTP: {price}")
    
    # Get quotes
    quotes = kite_data.get_quotes(["RELIANCE", "TCS", "INFY"])
    for symbol, quote in quotes.items():
        print(f"{symbol}: ₹{quote['last_price']} ({quote['change_pct']:+.2f}%)")
    
    # Get historical data
    df = kite_data.get_historical_data(
        "RELIANCE",
        datetime.now() - timedelta(days=30),
        datetime.now(),
        "day"
    )
    print(f"\nHistorical data shape: {df.shape}")
    print(df.head())
    
    # Get holdings
    holdings = kite_data.get_holdings()
    print(f"\nHoldings: {len(holdings)}")
    for holding in holdings:
        print(f"{holding['tradingsymbol']}: {holding['quantity']} shares")
"""
