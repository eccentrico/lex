"""Mutual fund NAV history from AMFI's official history report.

Kite's mf_instruments gives only the latest NAV (see services/kite_data.py's
get_mf_quote); a time series has to come from AMFI directly. AMFI has no
per-scheme endpoint — the history report returns every scheme for a date
range, so this fetches once and filters client-side. A historical NAV never
changes once published, so a fetched range is cached on disk with no TTL;
only genuinely new dates trigger another fetch.
"""
import json
import logging
from datetime import datetime, timedelta

import requests

from services.paths import lex_home

logger = logging.getLogger(__name__)

_HISTORY_URL = "https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx"
_CACHE_FILE = "mf_nav_cache.json"


def _cache_path():
    return lex_home() / _CACHE_FILE


def _load_cache() -> dict:
    p = _cache_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(cache: dict) -> None:
    _cache_path().write_text(json.dumps(cache), encoding="utf-8")


def get_nav_history(scheme_code: str, from_date: str, to_date: str) -> list[dict]:
    """NAV history for one scheme between two dates (YYYY-MM-DD), oldest first."""
    scheme_code = str(scheme_code)
    cache = _load_cache()
    cached_rows = cache.get(scheme_code, [])
    have_dates = {r["date"] for r in cached_rows}
    if _date_range(from_date, to_date) <= have_dates:
        return [r for r in cached_rows if from_date <= r["date"] <= to_date]

    try:
        resp = requests.get(_HISTORY_URL, params={
            "frmdt": _amfi_date(from_date), "todt": _amfi_date(to_date)},
            timeout=30)
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"AMFI NAV history fetch failed: {e}")
        return [r for r in cached_rows if from_date <= r["date"] <= to_date]

    rows = _parse_nav_history(resp.text, scheme_code)
    merged = {r["date"]: r for r in cached_rows}
    merged.update({r["date"]: r for r in rows})
    cache[scheme_code] = sorted(merged.values(), key=lambda r: r["date"])
    _save_cache(cache)
    return [r for r in cache[scheme_code] if from_date <= r["date"] <= to_date]


def _date_range(from_date: str, to_date: str) -> set:
    start = datetime.strptime(from_date, "%Y-%m-%d")
    end = datetime.strptime(to_date, "%Y-%m-%d")
    days = (end - start).days
    return {(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days + 1)}


def _amfi_date(iso_date: str) -> str:
    return datetime.strptime(iso_date, "%Y-%m-%d").strftime("%d-%b-%Y")


def _parse_nav_history(text: str, scheme_code: str) -> list[dict]:
    """Parse AMFI's semicolon-delimited report, filtered to one scheme code.

    Real column layout (verified against live AMFI endpoint, 2026-08-02):
    0=Scheme Code, 1=Scheme Name, 2=ISIN Div Payout/ISIN Growth,
    3=ISIN Div Reinvestment, 4=Net Asset Value, 5=Repurchase Price,
    6=Sale Price, 7=Date
    """
    rows = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(";")]
        if len(parts) < 8 or parts[0] != scheme_code:
            continue
        try:
            nav = float(parts[4])
            date = datetime.strptime(parts[7], "%d-%b-%Y").strftime("%Y-%m-%d")
        except (ValueError, IndexError):
            continue
        rows.append({"date": date, "nav": nav})
    return rows
