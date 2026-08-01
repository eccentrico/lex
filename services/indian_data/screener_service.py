"""
Screener.in data service for Indian stock fundamentals.

Fetches P&L, balance sheet, cash flow, and ratios from Screener.in.
Uses BSE token for symbol resolution. Falls back to yfinance on failure.
"""

import os
import re
import time
import random
import logging
import threading
from datetime import datetime, timedelta
from typing import Any, Optional

import requests
from bs4 import BeautifulSoup

from services.indian_data.symbol_mapping import get_bse_token

logger = logging.getLogger(__name__)

# Cache
_CACHE: dict[str, dict[str, Any]] = {}
_CACHE_MAX_SIZE = 500
_CACHE_TTL_HOURS = int(os.getenv("SCREENER_CACHE_TTL_HOURS", "24"))
_BSE_DELAY_SEC = float(os.getenv("BSE_API_DELAY_SEC", "2.5"))
# Only one stock's 11-request Screener.in sequence runs at a time, even when
# the fundamental analyst spawns 5 parallel workers.  Without this, bursts of
# 55 concurrent requests trigger immediate 429 rate-limiting.
_SCREENER_GATE = threading.Semaphore(1)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}
BASE_URL = "https://www.screener.in/"


def _cache_put(key: str, value: dict) -> None:
    """Insert into cache, evicting oldest half if size limit exceeded."""
    if len(_CACHE) >= _CACHE_MAX_SIZE:
        # Sort by timestamp, remove oldest half
        sorted_keys = sorted(
            _CACHE.keys(),
            key=lambda k: _CACHE[k].get("_ts", datetime.min),
        )
        for k in sorted_keys[: len(sorted_keys) // 2]:
            del _CACHE[k]
        logger.info(f"Cache pruned from {_CACHE_MAX_SIZE}+ to {len(_CACHE)} entries")
    _CACHE[key] = value


def _request_with_429_retry(url: str, *, timeout: int = 30, max_retries: int = 3) -> requests.Response:
    """Make a GET request with 429 rate-limit handling and exponential backoff."""
    time.sleep(_BSE_DELAY_SEC)
    backoff = 5.0
    for attempt in range(max_retries + 1):
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        if resp.status_code == 429:
            if attempt >= max_retries:
                resp.raise_for_status()
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    wait = float(retry_after)
                except ValueError:
                    wait = backoff
            else:
                wait = backoff
            # Add jitter (0-25% of wait time) to avoid thundering herd
            wait += random.uniform(0, wait * 0.25)
            wait = min(wait, 60.0)
            logger.warning(f"429 rate-limited on {url}, retrying in {wait:.1f}s (attempt {attempt + 1}/{max_retries})")
            time.sleep(wait)
            backoff = min(backoff * 2, 60.0)
            continue
        resp.raise_for_status()
        return resp
    # Should not reach here, but just in case
    resp.raise_for_status()
    return resp


def _get_screener_id(token: str, consolidated: bool = True) -> Optional[str]:
    """Load Screener page and extract company ID from HTML."""
    path = f"company/{token}/" + ("consolidated" if consolidated else "")
    url = BASE_URL + path
    try:
        resp = _request_with_429_retry(url)
        match = re.search(r'data-row-company-id="(\d+)"', resp.text)
        return match.group(1) if match else None
    except Exception as e:
        logger.warning(f"Failed to get Screener ID for token {token}: {e}")
        return None


def _request_schedule(screener_id: str, parent: str, section: str, consolidated: bool = True) -> Optional[dict]:
    """Fetch schedule data from Screener API."""
    cons = "&consolidated=" if consolidated else ""
    url = f"{BASE_URL}api/company/{screener_id}/schedules/?parent={parent}&section={section}{cons}"
    try:
        resp = _request_with_429_retry(url)
        return resp.json()
    except Exception as e:
        logger.warning(f"Screener schedule failed {parent}/{section}: {e}")
        return None


def _parse_fcf_from_cashflow(cashflow_data: dict) -> list[dict]:
    """Extract FCF-relevant data from cash flow section."""
    result = []
    for date_str, items in cashflow_data.items():
        row = {"date": date_str}
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict):
                for k, v in item.items():
                    row[k] = v
        result.append(row)
    return result


def _fetch_from_screener(symbol: str) -> Optional[dict]:
    """Fetch financials from Screener.in. Returns None on failure."""
    token = get_bse_token(symbol)
    if not token:
        return None

    screener_id = _get_screener_id(token)
    if not screener_id:
        return None

    result = {}

    # Cash flow
    cf_operating = _request_schedule(screener_id, "Cash+from+Operating+Activity", "cash-flow")
    cf_investing = _request_schedule(screener_id, "Cash+from+Investing+Activity", "cash-flow")
    cf_financing = _request_schedule(screener_id, "Cash+from+Financing+Activity", "cash-flow")
    if cf_operating or cf_investing:
        result["cash_flow"] = {
            "operating": cf_operating,
            "investing": cf_investing,
            "financing": cf_financing,
        }

    # Balance sheet
    bs_borrowings = _request_schedule(screener_id, "Borrowings", "balance-sheet")
    bs_assets = _request_schedule(screener_id, "Fixed+Assets", "balance-sheet")
    bs_other_liab = _request_schedule(screener_id, "Other+Liabilities", "balance-sheet")
    bs_other_assets = _request_schedule(screener_id, "Other+Assets", "balance-sheet")
    if bs_borrowings or bs_assets:
        result["balance_sheet"] = {
            "borrowings": bs_borrowings,
            "fixed_assets": bs_assets,
            "other_liabilities": bs_other_liab,
            "other_assets": bs_other_assets,
        }

    # P&L
    pnl_sales = _request_schedule(screener_id, "Sales", "profit-loss")
    pnl_netprofit = _request_schedule(screener_id, "Net+Profit", "profit-loss")
    if pnl_sales or pnl_netprofit:
        result["pnl"] = {"sales": pnl_sales, "net_profit": pnl_netprofit}

    # Key metrics from the company summary (top of page).
    # These live in <ul class="company-ratios"> (or "top-ratios"), NOT in
    # the #ratios table which only has operational ratios like Debtor Days.
    try:
        path = f"company/{token}/consolidated/"
        url = BASE_URL + path
        resp = _request_with_429_retry(url)
        soup = BeautifulSoup(resp.text, "html.parser")

        summary_ul = soup.find(class_="top-ratios") or soup.find(class_="company-ratios")
        if summary_ul:
            ratios = {}
            for li in summary_ul.find_all("li"):
                name_el = li.find(class_="name")
                number_el = li.find(class_="number")
                if name_el and number_el:
                    key = name_el.get_text(strip=True)
                    raw = number_el.get_text(strip=True).replace(",", "")
                    try:
                        ratios[key] = float(raw)
                    except ValueError:
                        pass
            if ratios:
                result["ratios"] = ratios
    except Exception as e:
        logger.warning(f"Could not parse ratios for {symbol}: {e}")

    if not result:
        return None

    result["source"] = "screener"
    return result


def _extract_metrics_from_screener(screener_data: dict, symbol: str = "") -> dict[str, Any]:
    """Convert Screener top-summary data to metrics dict compatible with yfinance format.

    The ratios dict now comes from the company-ratios summary section, with keys
    like "Stock P/E", "ROE", "Book Value", "Market Cap", "ROCE", "Dividend Yield".
    """
    metrics: dict[str, Any] = {}
    ratios = screener_data.get("ratios", {})
    if not ratios:
        logger.warning(f"No ratios in Screener data for {symbol}")
        return {}

    def _safe_float(key: str) -> Optional[float]:
        v = ratios.get(key)
        if v is None:
            return None
        try:
            return float(v)
        except (ValueError, TypeError):
            return None

    pe = _safe_float("Stock P/E")
    if pe is not None:
        metrics["trailingPE"] = pe

    book_value = _safe_float("Book Value")
    current_price = _safe_float("Current Price")
    if book_value and current_price and book_value > 0:
        metrics["priceToBook"] = round(current_price / book_value, 2)
        metrics["bookValue"] = book_value
    if current_price:
        metrics["currentPrice"] = current_price

    # Screener.in always displays percentage metrics in percent form
    # (e.g. ROE = 8.40 means 8.40%, Dividend Yield = 0.39 means 0.39%).
    # We store them as fractions to match yfinance convention.
    roe = _safe_float("ROE")
    if roe is not None:
        metrics["returnOnEquity"] = roe / 100

    roce = _safe_float("ROCE")
    if roce is not None:
        metrics["roce"] = roce / 100

    div_yield = _safe_float("Dividend Yield")
    if div_yield is not None:
        metrics["dividendYield"] = div_yield / 100

    market_cap = _safe_float("Market Cap")
    if market_cap is not None:
        metrics["marketCap"] = market_cap * 1e7  # Cr → ₹

    if len(metrics) < 3:
        logger.warning(
            f"Screener metrics degraded for {symbol}: only {len(metrics)} keys "
            f"extracted (keys: {list(ratios.keys())}) — rejecting to trigger yfinance fallback"
        )
        return {}

    logger.info(f"Screener extracted {len(metrics)} metrics for {symbol}: {list(metrics.keys())}")
    return metrics


def _extract_cash_flow_from_screener(screener_data: dict) -> Optional[dict]:
    """Extract FCF-relevant data from Screener cash flow."""
    cf = screener_data.get("cash_flow", {})
    if not cf:
        return None
    operating = cf.get("operating") or {}
    investing = cf.get("investing") or {}
    # FCF ≈ Operating CF - CapEx (from Investing)
    # Screener format: {date: {metric: value}}
    result = {}
    for date_str, items in operating.items():
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    result.update(item)
    return result if result else None


def _fmp_fallback(symbol: str) -> dict[str, Any]:
    """Fallback to FMP for metrics when Screener.in is unavailable."""
    try:
        from services.indian_data.fmp_service import get_fundamental_metrics, get_balance_sheet
    except ImportError:
        # lex: not vendored (fmp) — degrade gracefully. fmp_service.py is out of
        # v1 scope, so when Screener.in fails there is no secondary source; return
        # the same "no data" shape callers already handle when FMP has no coverage.
        logger.warning(f"fmp_service not vendored; no fallback data for {symbol}")
        return {
            "metrics": None,
            "cash_flow": None,
            "balance_sheet": None,
            "source": "unavailable",
        }

    metrics = get_fundamental_metrics(symbol)

    # FMP balance sheet → normalised dict for DCF compatibility
    balance_sheet = None
    try:
        bs_list = get_balance_sheet(symbol, limit=4) or []
        if bs_list:
            balance_sheet = {
                row.get("date", str(i)): row for i, row in enumerate(bs_list)
            }
    except Exception:
        pass

    return {
        "metrics": metrics,
        "cash_flow": None,   # Screener handles cash flow; FMP balance sheet above suffices for DCF
        "balance_sheet": balance_sheet,
        "source": "fmp",
    }


def get_financials(symbol: str, use_cache: bool = True) -> Optional[dict]:
    """
    Get P&L, balance sheet, cash flow, ratios for a symbol.

    Tries Screener first if USE_SCREENER_PRIMARY=true, else yfinance.
    Falls back to yfinance on Screener failure.
    """
    cache_key = f"financials:{symbol}"
    if use_cache and cache_key in _CACHE:
        entry = _CACHE[cache_key]
        if datetime.now() - entry.get("_ts", datetime.min) < timedelta(hours=_CACHE_TTL_HOURS):
            return {k: v for k, v in entry.items() if k != "_ts"}

    use_screener = os.getenv("USE_SCREENER_PRIMARY", "true").lower() == "true"
    result = None

    if use_screener:
        with _SCREENER_GATE:
            for attempt in range(3):
                try:
                    result = _fetch_from_screener(symbol)
                    if result:
                        break
                except Exception as e:
                    logger.warning(f"Screener attempt {attempt + 1} for {symbol}: {e}")
                    time.sleep((attempt + 1) * 1)
            if result:
                metrics = _extract_metrics_from_screener(result, symbol)
                result["metrics"] = metrics
                result["cash_flow"] = result.get("cash_flow", {})
                result["balance_sheet"] = result.get("balance_sheet", {})

    if result is None or not result.get("metrics"):
        result = _fmp_fallback(symbol)

    if use_cache and result:
        _cache_put(cache_key, {**result, "_ts": datetime.now()})

    return result


def get_cash_flow(symbol: str, use_cache: bool = True) -> Optional[dict]:
    """Get cash flow data for symbol."""
    data = get_financials(symbol, use_cache)
    return data.get("cash_flow") if data else None


def get_balance_sheet(symbol: str, use_cache: bool = True) -> Optional[dict]:
    """Get balance sheet data for symbol."""
    data = get_financials(symbol, use_cache)
    return data.get("balance_sheet") if data else None


def get_ratios(symbol: str, use_cache: bool = True) -> Optional[dict]:
    """Get financial ratios for symbol."""
    data = get_financials(symbol, use_cache)
    return (data.get("ratios") or data.get("metrics")) if data else None
