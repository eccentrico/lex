"""
NSE corporate announcements feed.

Mirrors the interface of bse_announcements.py so both feeds can be used
interchangeably (or together) by the news pipeline and backtest engine.

API used:
  GET https://www.nseindia.com/api/corporate-announcements
      ?index=equities&symbol=RELIANCE&from_date=01-01-2026&to_date=02-04-2026

Public API:
  get_recent_announcements(symbol, days, use_cache) → list[dict]
  preload_announcements_for_period(symbol, start_date, end_date) → list[dict]
  filter_announcements_for_day(all_ann, as_of_date, lookback_days) → list[dict]

Return dict keys (same as BSE service for drop-in compatibility):
  date, title, summary, url, category
"""

import logging
from datetime import datetime, timedelta, date as date_type
from typing import Any, Optional

from services.indian_data.nse_session import nse_get

logger = logging.getLogger(__name__)

_CACHE: dict[str, tuple[list[dict], datetime]] = {}
_CACHE_TTL_HOURS = 6


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fmt_nse_date(d: date_type) -> str:
    """NSE API expects DD-MM-YYYY."""
    return d.strftime("%d-%m-%Y")


def _parse_nse_date(date_str: str) -> Optional[datetime]:
    """
    Parse NSE broadcast datetime strings.
    Common formats:
      "02-Apr-2026 10:30:00"  (most announcement endpoints)
      "2026-04-02T10:30:00"   (some newer endpoints)
      "02-04-2026"
    """
    if not date_str:
        return None
    for fmt in (
        "%d-%b-%Y %H:%M:%S",   # "02-Apr-2026 10:30:00"
        "%d-%b-%Y",             # "02-Apr-2026"
        "%Y-%m-%dT%H:%M:%S",   # ISO
        "%d-%m-%Y",             # "02-04-2026"
        "%Y-%m-%d",             # "2026-04-02"
    ):
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    logger.debug(f"NSE: could not parse date string '{date_str}'")
    return None


def _rows_to_announcements(rows: list) -> list[dict[str, Any]]:
    """
    Normalise raw NSE API rows to the same dict shape used by BSE service.
    NSE field names vary slightly across endpoints; we try multiple keys.
    """
    result = []
    for row in rows:
        # Title / subject
        title = (
            row.get("desc")
            or row.get("subject")
            or row.get("smIndustry")
            or ""
        )
        # Date
        date_raw = (
            row.get("bcastdttm")
            or row.get("sort_date")
            or row.get("an_dt")
            or ""
        )
        # Summary / body text
        summary = (
            row.get("attchmntText")
            or row.get("body")
            or row.get("desc")
            or ""
        )[:500]

        # Attachment URL — NSE stores filename; compose full URL if relative
        att_file = row.get("attchmntFile") or row.get("fileDesc") or ""
        if att_file and not att_file.startswith("http"):
            url = f"https://nsearchives.nseindia.com/corporate/{att_file}"
        else:
            url = att_file

        category = row.get("categoryDesc") or row.get("an_type") or ""

        if title:  # Skip empty rows
            result.append({
                "date": date_raw,
                "title": title.strip(),
                "summary": summary.strip(),
                "url": url,
                "category": category.strip(),
            })
    return result


def _fetch_range(
    symbol: str,
    from_dt: date_type,
    to_dt: date_type,
) -> list[dict[str, Any]]:
    """
    Fetch all NSE corporate announcements for a symbol in a date range.
    NSE's endpoint returns all results in one page (no pagination needed).
    """
    params = {
        "index": "equities",
        "symbol": symbol.upper(),
        "from_date": _fmt_nse_date(from_dt),
        "to_date": _fmt_nse_date(to_dt),
    }

    data = nse_get("/api/corporate-announcements", params=params)
    if not data:
        return []

    # Response is a list directly
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        # Some endpoints wrap in {"data": [...]}
        rows = data.get("data") or data.get("announcements") or []
    else:
        rows = []

    return _rows_to_announcements(rows)


# ---------------------------------------------------------------------------
# Public API (mirrors bse_announcements.py)
# ---------------------------------------------------------------------------

def get_recent_announcements(
    symbol: str,
    days: int = 30,
    use_cache: bool = True,
) -> list[dict[str, Any]]:
    """
    Get recent NSE corporate announcements for a symbol (live mode).

    Args:
        symbol:    NSE symbol, e.g. RELIANCE, TCS
        days:      Look-back window in calendar days (max 365)
        use_cache: Use in-memory 6-hour cache

    Returns:
        List of dicts: {date, title, summary, url, category}
    """
    cache_key = f"nse_ann:{symbol}:{days}"
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
    Bulk-fetch all NSE announcements for a symbol over an explicit date range.

    Intended for the backtest engine: fetch once per symbol, filter in memory
    per simulation day to avoid look-ahead bias.

    Args:
        symbol:     NSE symbol
        start_date: datetime, date, or "YYYY-MM-DD" string (inclusive)
        end_date:   datetime, date, or "YYYY-MM-DD" string (inclusive)

    Returns:
        Announcements sorted ascending by date.
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

    def _sort_key(a: dict) -> datetime:
        parsed = _parse_nse_date(a.get("date", ""))
        return parsed if parsed else datetime.min

    announcements.sort(key=_sort_key)
    return announcements


def filter_announcements_for_day(
    all_announcements: list[dict[str, Any]],
    as_of_date: datetime,
    lookback_days: int = 30,
) -> list[dict[str, Any]]:
    """
    Filter a pre-loaded NSE announcement list to the window
    [as_of_date - lookback_days, as_of_date].

    Drop-in replacement for the BSE equivalent used in backtest.

    Args:
        all_announcements: Full pre-loaded list
        as_of_date:        Simulation date (upper bound, inclusive)
        lookback_days:     How many calendar days back to look

    Returns:
        Announcements within the window, sorted ascending.
    """
    if not all_announcements:
        return []

    cutoff = as_of_date - timedelta(days=lookback_days)
    result = []
    for ann in all_announcements:
        ann_dt = _parse_nse_date(ann.get("date", ""))
        if ann_dt is None:
            continue
        if cutoff <= ann_dt < as_of_date:  # exclude day-T filings (filed post market-open)
            result.append(ann)
    return result
