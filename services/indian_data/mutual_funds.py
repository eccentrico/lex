"""Mutual fund NAV history from AMFI's official history report.

Kite's mf_instruments gives only the latest NAV (see services/kite_data.py's
get_mf_quote); a time series has to come from AMFI directly. AMFI has no
per-scheme endpoint — the history report returns every scheme for a date
range, so this fetches once and filters client-side.

A historical NAV never changes once published, so a fetched range is cached
on disk with no TTL. The cache records which date RANGES were successfully
fetched, not which dates have rows: NAVs only exist on trading days, so
"every calendar day in the range has a row" is never true for any range
spanning a weekend and would make the cache never hit.
"""
import json
import logging
from datetime import datetime, timedelta

import requests

from services.paths import lex_home

logger = logging.getLogger(__name__)

_HISTORY_URL = "https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx"
_CACHE_FILE = "mf_nav_cache.json"

# ponytail: chunk size / range cap / timeout are measured, not guessed —
# live AMFI, 2026-08-05, whole-market report (no AMC filter):
#   7 calendar days  -> 4.9 MB, 30 s   (already blows the old flat 30 s timeout)
#   15 calendar days -> 10.7 MB, 32 s
#   30 calendar days -> 22.3 MB, 75 s  (a second, unseen 30-day window: 46 s)
#   92 calendar days -> did not complete in 180 s (~66 MB expected)
# Response time is variable (the server is slow and sometimes warm), but size
# is steady at ~0.65-0.75 MB per calendar day of whole-market data, and size is
# what binds: one unchunked 5y call is ~1.2 GB and does not finish. 30-day
# windows are the largest that reliably complete, hence _CHUNK_DAYS; _TIMEOUT
# is 120 s to leave headroom over the slowest window observed.
#
# _MAX_DAYS caps a single call at ~14 windows (~17 min, ~290 MB worst case,
# cold). That is deliberately set just past one year so the common "1y NAV
# trend" ask works and then caches forever, while the 3y/5y ranges
# FUND_FACTS_PASS also mentions fail loudly instead of grinding for over an
# hour — an honest "unknown" beats a request that never returns.
#
# Upgrade path (verified same day): DownloadNAVHistoryReport_Po.aspx also
# accepts an `mf=<AMFI AMC id>` param that filters server-side. mf=53 for
# 01-Jul..31-Jul-2026 returned 974 KB in 6 s versus 22.3 MB in 75 s for the
# whole market — a ~23x cut, with byte-identical rows for scheme 120503.
# Wiring a scheme_code -> AMC id mapping would let _CHUNK_DAYS and _MAX_DAYS
# widen by roughly that factor and make real 5y history practical. Until that
# mapping exists, keep these conservative.
_CHUNK_DAYS = 30
_MAX_DAYS = 400
_TIMEOUT = 120


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


def _entry(cache: dict, scheme_code: str) -> dict:
    """Cache entry for one scheme, tolerating the older bare-list format.

    The old format stored just the rows, with no record of what was fetched.
    Keep the rows, start coverage empty: a refetch is cheap next to serving
    a silently incomplete range.
    """
    raw = cache.get(scheme_code)
    if isinstance(raw, list):
        return {"ranges": [], "rows": raw}
    if not isinstance(raw, dict):
        return {"ranges": [], "rows": []}
    return {"ranges": [list(r) for r in raw.get("ranges", [])],
            "rows": list(raw.get("rows", []))}


def get_nav_history(scheme_code: str, from_date: str, to_date: str) -> list[dict]:
    """NAV history for one scheme between two dates (YYYY-MM-DD), oldest first.

    Raises if AMFI is unreachable and the cache has nothing for the range —
    an empty return must mean "this scheme genuinely has no NAV here", never
    "the endpoint was down", matching how technicals treats an empty index
    history. handle_fund_history turns the raise into _err, which the facts
    pass is required to write up as "unknown".
    """
    scheme_code = str(scheme_code)
    span = (_d(to_date) - _d(from_date)).days
    if span < 0:
        raise ValueError(f"from_date {from_date} is after to_date {to_date}")
    if span + 1 > _MAX_DAYS:
        raise ValueError(
            f"{from_date}..{to_date} is {span + 1} days; AMFI's whole-market history "
            f"report cannot serve more than {_MAX_DAYS} days in one call — "
            f"ask for a narrower range")

    cache = _load_cache()
    entry = _entry(cache, scheme_code)
    if _covered(entry["ranges"], from_date, to_date):
        return _slice(entry["rows"], from_date, to_date)

    rows_by_date = {r["date"]: r for r in entry["rows"]}
    ranges = entry["ranges"]
    fetched = failed = 0
    for chunk_from, chunk_to in _chunks(from_date, to_date):
        if _covered(ranges, chunk_from, chunk_to):
            continue
        try:
            resp = requests.get(_HISTORY_URL, params={
                "frmdt": _amfi_date(chunk_from), "todt": _amfi_date(chunk_to)},
                timeout=_TIMEOUT)
            resp.raise_for_status()
        except Exception as e:
            # Keep going: one bad window must not cost us the windows that
            # did land. Its interval is deliberately NOT recorded as covered,
            # so a later call refetches it instead of reporting a cache hit
            # over a hole that would never heal (this cache has no TTL).
            logger.warning(f"AMFI NAV history fetch failed for {chunk_from}..{chunk_to}: {e}")
            failed += 1
            continue
        for r in _parse_nav_history(resp.text, scheme_code):
            rows_by_date[r["date"]] = r
        ranges = _merge_ranges(ranges + [[chunk_from, chunk_to]])
        fetched += 1
        # Persist per window, not once at the end: a long range is many
        # minutes of fetching, and if the caller times out or interrupts
        # mid-loop an end-of-loop save would throw away every window that
        # already landed and make the next attempt start from zero.
        cache[scheme_code] = {"ranges": ranges,
                              "rows": sorted(rows_by_date.values(), key=lambda r: r["date"])}
        _save_cache(cache)

    out = _slice(rows_by_date.values(), from_date, to_date)
    if failed and not _covered(ranges, from_date, to_date):
        if not out:
            raise RuntimeError(
                f"AMFI NAV history unavailable for scheme {scheme_code} "
                f"{from_date}..{to_date}: {failed} window(s) failed and nothing "
                f"cached covers this range")
        # Partially covered: some windows landed. Returning the rows we do have
        # beats discarding them, but say so — the range is not complete.
        logger.warning(
            f"AMFI NAV history for scheme {scheme_code} {from_date}..{to_date} is "
            f"partial: {failed} of {fetched + failed} window(s) failed")
    return out


def _d(iso_date: str) -> datetime:
    return datetime.strptime(iso_date, "%Y-%m-%d")


def _chunks(from_date: str, to_date: str):
    """Split a range into closed _CHUNK_DAYS-wide windows, oldest first."""
    start, end = _d(from_date), _d(to_date)
    while start <= end:
        stop = min(start + timedelta(days=_CHUNK_DAYS - 1), end)
        yield start.strftime("%Y-%m-%d"), stop.strftime("%Y-%m-%d")
        start = stop + timedelta(days=1)


def _merge_ranges(ranges: list) -> list:
    """Merge closed [from, to] intervals, joining adjacent ones too.

    Adjacent matters: sequential chunks are back-to-back, not overlapping, so
    without this a year of coverage would stay fragmented into 13 intervals
    and no single one would contain the caller's range.
    """
    out: list = []
    for lo, hi in sorted(ranges):
        if out and _d(lo) <= _d(out[-1][1]) + timedelta(days=1):
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi])
    return out


def _covered(ranges: list, from_date: str, to_date: str) -> bool:
    """True if some already-fetched interval contains the whole range.

    ISO dates sort chronologically as strings, and _merge_ranges leaves no two
    intervals touching, so containment by a single interval is the whole test.
    """
    return any(lo <= from_date and to_date <= hi for lo, hi in ranges)


def _slice(rows, from_date: str, to_date: str) -> list[dict]:
    return sorted((r for r in rows if from_date <= r["date"] <= to_date),
                  key=lambda r: r["date"])


def _amfi_date(iso_date: str) -> str:
    return _d(iso_date).strftime("%d-%b-%Y")


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
