"""
Groww Trade API Data Service

Read-only market data via Groww's Trade API, as an explicit secondary source
alongside Kite (services/kite_data.py) — never used automatically, only when
a caller specifically wants Groww's numbers (see lex/tools/groww.py).

Quotes and historical data require Groww's paid API plan (~Rs 499/month); a
free-tier key can resolve instruments but gets "Access forbidden" on
get_quote/get_historical_candles — see docs/superpowers/plans/
2026-08-06-groww-secondary-data-source.md for the verified live-call results.
That 403 is a Groww account permission, not a bug in this module.
"""
import logging
from datetime import datetime
from typing import Dict, List, Optional, Union

from growwapi import GrowwAPI

from services.groww_auth import get_or_renew_access_token

logger = logging.getLogger(__name__)

# Groww's candle_interval string for daily candles is confirmed by the SDK's
# own docstring example ("1day"). "week" has no confirmed equivalent — historical
# data requires the paid plan, so it can't be verified against a live call yet.
# ponytail: rejecting it explicitly beats guessing a candle_interval string that
# might silently return the wrong granularity. Add it once confirmed.
_CANDLE_INTERVALS = {"day": "1day"}


class GrowwDataService:
    """Singleton service for fetching read-only market data from Groww."""

    _instance: Optional["GrowwDataService"] = None
    _initialized: bool = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._client: Optional[GrowwAPI] = None
        self._initialized = True

    def _get_client(self) -> GrowwAPI:
        """Authenticate lazily — only on first real call, so an unconfigured
        Groww key never raises at import/instantiation time; it surfaces as a
        normal exception from the first method call instead."""
        if self._client is None:
            token = get_or_renew_access_token()
            self._client = GrowwAPI(token)
        return self._client

    def _resolve_instrument(self, symbol: str) -> dict:
        """NSE tradingsymbol -> Groww's instrument dict (groww_symbol, exchange_token, ...).

        No TTL cache needed here: GrowwAPI caches its instrument DataFrame
        in-memory on the client instance after the first download (see
        growwapi's _load_instruments), and this service holds one client for
        its lifetime.
        """
        client = self._get_client()
        clean = symbol.replace("NSE:", "").strip().upper()
        return client.get_instrument_by_exchange_and_trading_symbol(
            exchange=GrowwAPI.EXCHANGE_NSE, trading_symbol=clean)

    def get_quotes(self, symbols: List[str]) -> Dict[str, dict]:
        """Latest quote per symbol, raw as Groww returns it.

        Not reshaped to match kite_data.get_quotes' field names (last_price,
        ohlc, etc.) — quotes require the paid plan and the exact response
        shape is unverified (see module docstring). A symbol Groww can't
        quote is simply absent from the result, same as kite_data.get_quotes'
        behaviour for an unmatched symbol.
        """
        client = self._get_client()
        result = {}
        for symbol in symbols:
            clean = symbol.replace("NSE:", "").strip().upper()
            try:
                result[symbol] = client.get_quote(
                    trading_symbol=clean, exchange=GrowwAPI.EXCHANGE_NSE,
                    segment=GrowwAPI.SEGMENT_CASH)
            except Exception as e:
                logger.warning(f"groww get_quote failed for {symbol}: {e}")
        return result

    def get_historical_data(
        self, symbol: str, from_date: Union[str, datetime],
        to_date: Union[str, datetime], interval: str = "day",
    ) -> dict:
        """Raw candle payload for one symbol between two dates (inclusive).

        Returns Groww's raw response dict rather than a DataFrame like
        kite_data.get_historical_data — see this file's module docstring and
        the implementation plan for why the exact candle schema can't be
        verified yet.

        Raises:
            ValueError: for an interval other than "day" (no other
                candle_interval mapping is confirmed yet).
        """
        candle_interval = _CANDLE_INTERVALS.get(interval)
        if candle_interval is None:
            raise ValueError(
                f"Unsupported interval {interval!r} for Groww historical data — "
                f"only {sorted(_CANDLE_INTERVALS)} are confirmed.")
        if isinstance(from_date, str):
            from_date = datetime.strptime(from_date, "%Y-%m-%d")
        if isinstance(to_date, str):
            to_date = datetime.strptime(to_date, "%Y-%m-%d")

        instrument = self._resolve_instrument(symbol)
        client = self._get_client()
        return client.get_historical_candles(
            exchange=GrowwAPI.EXCHANGE_NSE, segment=GrowwAPI.SEGMENT_CASH,
            groww_symbol=instrument["groww_symbol"],
            start_time=from_date.strftime("%Y-%m-%d 00:00:00"),
            end_time=to_date.strftime("%Y-%m-%d 23:59:59"),
            candle_interval=candle_interval)


groww_data = GrowwDataService()
