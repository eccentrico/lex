"""
Earnings Calendar Service.

Fetches upcoming and recent earnings dates from FMP so the system can:

1. Pre-earnings caution (within DAYS_BEFORE_WARNING trading days of results):
   - Reduce suggested position sizes by 30%
   - Lower confidence cap to 0.70
   - Add "pre_earnings_caution" flag to pm_decisions reasoning

2. Post-earnings surprise (results released today or yesterday):
   - Compute surprise % = (actual EPS - estimated EPS) / abs(estimated EPS)
   - Positive surprise > +10%  → boost research confidence +0.10
   - Negative surprise < -10%  → add "earnings_miss" red flag
   These signals feed the researcher and portfolio_manager.

FMP endpoint used: /v3/earning_calendar?from=YYYY-MM-DD&to=YYYY-MM-DD
"""

import logging
from datetime import date, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)

# How many trading days ahead to flag pre-earnings caution
DAYS_BEFORE_WARNING = 5
# Position size penalty for upcoming earnings
PRE_EARNINGS_SIZE_FACTOR = 0.70    # reduce to 70% of normal
PRE_EARNINGS_CONFIDENCE_CAP = 0.70

# EPS surprise thresholds
STRONG_BEAT_PCT = 10.0    # > +10% surprise = strong beat
STRONG_MISS_PCT = -10.0   # < -10% surprise = strong miss

# Same-day in-memory cache
_CACHE: dict[str, Any] = {}
_CACHE_DATE: Optional[date] = None


def get_earnings_calendar(from_date: Optional[date] = None,
                          to_date: Optional[date] = None) -> list[dict[str, Any]]:
    """
    Fetch earnings calendar from FMP for the given date range.

    Defaults to today ± 14 days if not specified.

    Returns:
        List of dicts: [{symbol, date, eps_estimated, eps_actual, revenue_estimated,
                         revenue_actual, surprise_pct}, ...]
    """
    global _CACHE, _CACHE_DATE
    today = date.today()

    if _CACHE_DATE == today and _CACHE.get("calendar"):
        return _CACHE["calendar"]

    from_d = from_date or (today - timedelta(days=7))
    to_d   = to_date   or (today + timedelta(days=14))

    try:
        try:
            from services.indian_data.fmp_service import _get, _f as _ff
        except ImportError:
            # lex: not vendored (fmp) — degrade gracefully. FMP is the only
            # earnings-calendar source, so the calendar is empty in v1; callers
            # (get_earnings_flags) already handle an empty list.
            logger.debug("fmp_service not vendored; earnings calendar unavailable")
            return []

        data = _get("/v3/earning_calendar", params={
            "from": from_d.strftime("%Y-%m-%d"),
            "to":   to_d.strftime("%Y-%m-%d"),
        })
        if not data or not isinstance(data, list):
            return []

        rows = []
        for item in data:
            sym_raw = str(item.get("symbol") or "")
            # Only include .NS (Indian) symbols
            if not sym_raw.upper().endswith(".NS"):
                continue

            symbol = sym_raw.upper().replace(".NS", "")
            report_date_str = str(item.get("date") or "")[:10]
            try:
                report_date = date.fromisoformat(report_date_str)
            except ValueError:
                continue

            eps_est    = _ff(item.get("epsEstimated"))
            eps_actual = _ff(item.get("eps"))
            rev_est    = _ff(item.get("revenueEstimated"))
            rev_actual = _ff(item.get("revenue"))

            surprise_pct = None
            if eps_est and eps_actual and abs(eps_est) > 0:
                surprise_pct = round((eps_actual - eps_est) / abs(eps_est) * 100, 2)

            rows.append({
                "symbol":             symbol,
                "date":               report_date,
                "date_str":           report_date_str,
                "eps_estimated":      eps_est,
                "eps_actual":         eps_actual,
                "revenue_estimated":  rev_est,
                "revenue_actual":     rev_actual,
                "surprise_pct":       surprise_pct,
            })

        rows.sort(key=lambda r: r["date"])
        _CACHE["calendar"] = rows
        _CACHE_DATE = today
        logger.info(f"Earnings calendar: {len(rows)} Indian stock events in range")
        return rows

    except Exception as e:
        logger.warning(f"Earnings calendar fetch failed: {e}")
        return []


def get_earnings_flags(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """
    For each symbol, return any earnings-related flag.

    Returns:
        {
          "RELIANCE": {
            "flag":          "pre_earnings",      # or "post_earnings_beat" / "post_earnings_miss" / None
            "earnings_date": "2026-04-10",
            "days_to_earnings": 3,
            "size_factor":   0.70,                 # multiply position size
            "confidence_cap": 0.70,
            "note":          "Earnings in 3 days — reducing position size to manage event risk"
          },
          "TCS": {
            "flag":          "post_earnings_beat",
            "surprise_pct":  +14.5,
            "note":          "Beat EPS estimate by +14.5% — strong signal"
          },
          ...
        }
    """
    today = date.today()
    calendar = get_earnings_calendar()
    if not calendar:
        return {}

    symbol_set = {s.upper() for s in symbols}
    flags: dict[str, dict[str, Any]] = {}

    for ev in calendar:
        sym = ev["symbol"].upper()
        if sym not in symbol_set:
            continue

        report_date = ev["date"]
        days_diff = (report_date - today).days   # +ve = future, -ve = past

        # Pre-earnings caution: upcoming within DAYS_BEFORE_WARNING
        if 0 <= days_diff <= DAYS_BEFORE_WARNING:
            flags[sym] = {
                "flag":             "pre_earnings",
                "earnings_date":    ev["date_str"],
                "days_to_earnings": days_diff,
                "size_factor":      PRE_EARNINGS_SIZE_FACTOR,
                "confidence_cap":   PRE_EARNINGS_CONFIDENCE_CAP,
                "note": (
                    f"Earnings in {days_diff} day{'s' if days_diff != 1 else ''} "
                    f"({ev['date_str']}) — reducing position size to manage event risk"
                ),
            }

        # Post-earnings: reported in last 2 days
        elif -2 <= days_diff < 0 and ev.get("eps_actual") is not None:
            surprise = ev.get("surprise_pct")
            if surprise is not None and surprise >= STRONG_BEAT_PCT:
                flags[sym] = {
                    "flag":         "post_earnings_beat",
                    "earnings_date": ev["date_str"],
                    "surprise_pct": surprise,
                    "size_factor":  1.0,        # no penalty for beat
                    "confidence_cap": 1.0,
                    "note": f"Beat EPS estimate by +{surprise:.1f}% — positive earnings catalyst",
                }
            elif surprise is not None and surprise <= STRONG_MISS_PCT:
                flags[sym] = {
                    "flag":         "post_earnings_miss",
                    "earnings_date": ev["date_str"],
                    "surprise_pct": surprise,
                    "size_factor":  0.50,       # halve size after miss
                    "confidence_cap": 0.55,
                    "note": f"Missed EPS estimate by {surprise:.1f}% — earnings risk flag",
                }
            else:
                flags[sym] = {
                    "flag":         "post_earnings_inline",
                    "earnings_date": ev["date_str"],
                    "surprise_pct": surprise,
                    "size_factor":  1.0,
                    "confidence_cap": 1.0,
                    "note": f"Earnings in line with estimates ({surprise:+.1f}%)",
                }

    return flags
