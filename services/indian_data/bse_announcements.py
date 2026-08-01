"""
BSE corporate announcements for Indian stocks.

Fetches recent corporate announcements from BSE API.
Uses BSE token for symbol resolution. Caches results for 6 hours.

Point-in-time support: preload_announcements_for_period() fetches a full date
range in one call, used by the backtest to avoid per-day API calls and ensure
no look-ahead bias (only announcements up to the simulation date are returned).
"""

import os
import time
import logging
from datetime import datetime, timedelta, date as date_type
from typing import Any, List, Optional

import requests

from services.indian_data.symbol_mapping import get_bse_token

logger = logging.getLogger(__name__)

_CACHE: dict[str, tuple[list[dict], datetime]] = {}
_CACHE_TTL_HOURS = 6
_BSE_DELAY_SEC = float(os.getenv("BSE_API_DELAY_SEC", "1.5"))

BSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.bseindia.com",
    "Referer": "https://www.bseindia.com/",
}
BSE_BASE = "https://api.bseindia.com"


def _fetch_announcements_page(token: str, from_date: str, to_date: str, page: int = 1) -> Optional[dict]:
    """Fetch one page of announcements from BSE API."""
    url = (
        f"{BSE_BASE}/BseIndiaAPI/api/AnnSubCategoryGetData/w"
        f"?pageno={page}&strCat=-1&strPrevDate={from_date}&strScrip={token}"
        f"&strSearch=P&strToDate={to_date}&strType=C&subcategory=-1"
    )
    try:
        time.sleep(_BSE_DELAY_SEC)
        resp = requests.get(url, headers=BSE_HEADERS, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.debug(f"BSE announcements fetch failed: {e}")
        return None


def _parse_date(date_str: str) -> Optional[datetime]:
    """Parse BSE date strings into datetime. Handles multiple formats."""
    if not date_str:
        return None
    try:
        # "2025-09-18 00:00:00" or "2025-09-18T00:00:00"
        return datetime.fromisoformat(date_str.replace("T", " ").split(" ")[0])
    except ValueError:
        pass
    try:
        # "20250918"
        if len(date_str) == 8 and date_str.isdigit():
            return datetime.strptime(date_str, "%Y%m%d")
    except ValueError:
        pass
    return None


def _rows_to_announcements(rows: list) -> list[dict[str, Any]]:
    """Convert raw BSE API rows to normalized announcement dicts."""
    result = []
    for row in rows:
        summary_raw = row.get("newstext") or row.get("News_text") or ""
        result.append({
            "date": row.get("dt_dt", row.get("NewsDate", "")),
            "title": row.get("newssub", row.get("News_sub", "")),
            "summary": summary_raw[:500],
            "url": row.get("attchment", row.get("Attachment", "")),
            "category": row.get("category", ""),
        })
    return result


def _fetch_range(
    symbol: str,
    from_dt: date_type,
    to_dt: date_type,
) -> list[dict[str, Any]]:
    """
    Internal helper: fetch all announcement pages for a date range.
    Used by both get_recent_announcements and preload_announcements_for_period.
    """
    token = get_bse_token(symbol)
    if not token:
        return []

    from_str = from_dt.strftime("%Y%m%d")
    to_str = to_dt.strftime("%Y%m%d")

    all_rows: list = []
    page = 1
    while True:
        data = _fetch_announcements_page(token, from_str, to_str, page)
        if not data:
            break
        table = data.get("Table", [])
        if not table:
            break
        all_rows.extend(table)
        table1 = data.get("Table1", [])
        row_cnt = int(table1[0].get("ROWCNT", 0)) if table1 else 0
        if row_cnt < 50 or len(all_rows) >= row_cnt:
            break
        page += 1
        if page > 20:
            break

    return _rows_to_announcements(all_rows)


def get_recent_announcements(symbol: str, days: int = 30, use_cache: bool = True) -> list[dict[str, Any]]:
    """
    Get recent corporate announcements for a symbol (live mode).

    Args:
        symbol: NSE symbol (e.g. RELIANCE, TCS)
        days: Number of days to look back
        use_cache: Whether to use in-memory cache

    Returns:
        List of dicts with date, title, summary, url, category
    """
    cache_key = f"announcements:{symbol}:{days}"
    if use_cache and cache_key in _CACHE:
        cached_list, cached_ts = _CACHE[cache_key]
        if datetime.now() - cached_ts < timedelta(hours=_CACHE_TTL_HOURS):
            return cached_list

    to_dt = datetime.now().date()
    from_dt = to_dt - timedelta(days=min(days, 365))
    result = _fetch_range(symbol, from_dt, to_dt)

    if use_cache and result:
        _CACHE[cache_key] = (result, datetime.now())

    return result


def preload_announcements_for_period(
    symbol: str,
    start_date,
    end_date,
) -> list[dict[str, Any]]:
    """
    Bulk-fetch all announcements for a symbol over an explicit date range.

    Used by the backtest engine to pre-load once per symbol (avoiding per-day
    API calls) and then filter in memory for each simulation day.
    Dates can be datetime objects or YYYY-MM-DD strings.

    Returns:
        All announcements between start_date and end_date (inclusive), sorted
        ascending by date.
    """
    def _to_date(d) -> date_type:
        if isinstance(d, datetime):
            return d.date()
        if isinstance(d, date_type):
            return d
        return datetime.strptime(str(d)[:10], "%Y-%m-%d").date()

    from_dt = _to_date(start_date)
    to_dt = _to_date(end_date)

    announcements = _fetch_range(symbol, from_dt, to_dt)

    # Sort ascending by parsed date so per-day filtering is straightforward
    def _sort_key(a: dict) -> datetime:
        parsed = _parse_date(a.get("date", ""))
        return parsed if parsed else datetime.min

    announcements.sort(key=_sort_key)
    return announcements


def filter_announcements_for_day(
    all_announcements: list[dict[str, Any]],
    as_of_date: datetime,
    lookback_days: int = 30,
) -> list[dict[str, Any]]:
    """
    Filter a pre-loaded announcement list to [as_of_date - lookback_days, as_of_date].

    Args:
        all_announcements: Full pre-loaded list from preload_announcements_for_period
        as_of_date: Simulation date (upper bound, inclusive)
        lookback_days: How many days back to look

    Returns:
        Announcements within the window, sorted ascending.
    """
    if not all_announcements:
        return []

    cutoff = as_of_date - timedelta(days=lookback_days)
    result = []
    for ann in all_announcements:
        ann_dt = _parse_date(ann.get("date", ""))
        if ann_dt is None:
            continue
        if cutoff <= ann_dt < as_of_date:  # exclude day-T filings (filed post market-open)
            result.append(ann)
    return result
