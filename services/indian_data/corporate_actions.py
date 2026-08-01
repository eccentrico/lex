"""NSE corporate actions and board meetings, normalised per symbol.

Dividends, splits, bonuses, rights and buybacks were the one company-event
pillar Lex had no source for — `market_events` was serving insider (PIT)
trades under a "corporate_actions" key, which is a different thing entirely.

Primary source is NSE's corporate-actions API. When that is unavailable (the
endpoint is unauthenticated and periodically moves), the fallback filters BSE
announcement subjects for corporate-action keywords: noisier, but a filed
announcement about a dividend is better than a silent gap.
"""
import logging
from datetime import date, timedelta
from typing import Any, Optional

from services.indian_data.nse_session import nse_get

logger = logging.getLogger(__name__)

# subject keyword -> action type. Order matters: the first hit wins, so the
# more specific patterns ("bonus issue") must precede the looser ones ("issue").
_TYPES = (
    ("buy back", "buyback"), ("buyback", "buyback"),
    ("bonus", "bonus"),
    ("split", "split"), ("sub-division", "split"), ("subdivision", "split"),
    ("consolidation", "consolidation"),
    ("rights", "rights"),
    ("dividend", "dividend"),
    ("amalgamation", "restructuring"), ("merger", "restructuring"),
    ("demerger", "restructuring"), ("scheme of arrangement", "restructuring"),
    ("annual general meeting", "agm"), ("agm", "agm"),
    ("extra ordinary general meeting", "egm"), ("egm", "egm"),
)

_BOARD_PURPOSES = ("results", "dividend", "buyback", "bonus", "split",
                   "fund raising", "fund-raising", "preferential")


def classify(subject: str) -> str:
    s = (subject or "").lower()
    for needle, kind in _TYPES:
        if needle in s:
            return kind
    return "other"


def get_corporate_actions(symbol: str, days_back: int = 90,
                          days_ahead: int = 90) -> list[dict[str, Any]]:
    """Corporate actions for `symbol` in the window around today, newest first."""
    symbol = symbol.strip().upper()
    today = date.today()
    params = {
        "index": "equities",
        "symbol": symbol,
        "from_date": _fmt(today - timedelta(days=days_back)),
        "to_date": _fmt(today + timedelta(days=days_ahead)),
    }
    rows = _rows(nse_get("/api/corporates-corporateActions", params=params))
    if rows is None:
        logger.warning("NSE corporate actions unavailable for %s — using BSE fallback",
                       symbol)
        return _bse_fallback(symbol, days_back)

    actions = []
    for row in rows:
        subject = str(row.get("subject") or row.get("purpose") or "").strip()
        if not subject:
            continue
        actions.append({
            "type": classify(subject),
            "subject": subject,
            "ex_date": row.get("exDate") or row.get("exdate"),
            "record_date": row.get("recDate") or row.get("recordDate"),
            "company": row.get("comp") or row.get("company"),
            "series": row.get("series"),
            "source": "nse",
        })
    return sorted(actions, key=lambda a: _sort_key(a["ex_date"]), reverse=True)


def get_board_meetings(symbol: str, days_back: int = 30,
                       days_ahead: int = 60) -> list[dict[str, Any]]:
    """Scheduled/held board meetings — the earliest warning of a results date."""
    symbol = symbol.strip().upper()
    today = date.today()
    params = {
        "index": "equities",
        "symbol": symbol,
        "from_date": _fmt(today - timedelta(days=days_back)),
        "to_date": _fmt(today + timedelta(days=days_ahead)),
    }
    rows = _rows(nse_get("/api/corporate-board-meetings", params=params))
    if rows is None:
        return []

    meetings = []
    for row in rows:
        purpose = str(row.get("bm_purpose") or row.get("purpose") or "").strip()
        meetings.append({
            "date": row.get("bm_date") or row.get("meetingDate"),
            "purpose": purpose,
            "details": str(row.get("bm_desc") or "").strip() or None,
            "is_results": any(p in purpose.lower() for p in _BOARD_PURPOSES),
            "source": "nse",
        })
    return sorted(meetings, key=lambda m: _sort_key(m["date"]), reverse=True)


def _bse_fallback(symbol: str, days_back: int) -> list[dict[str, Any]]:
    """BSE announcements whose subject looks like a corporate action."""
    try:
        from services.indian_data import bse_announcements
        rows = bse_announcements.get_recent_announcements(symbol, days=days_back) or []
    except Exception as e:
        logger.warning("BSE corporate-action fallback failed for %s: %s", symbol, e)
        return []
    out = []
    for row in rows:
        subject = str(row.get("subject") or row.get("headline") or "")
        kind = classify(subject)
        if kind == "other":
            continue
        out.append({
            "type": kind,
            "subject": subject.strip(),
            "ex_date": None,
            "record_date": None,
            "company": row.get("company"),
            "announced_at": row.get("date") or row.get("timestamp"),
            "source": "bse_announcement",
        })
    return out


def _rows(data: Any) -> Optional[list]:
    """NSE payloads arrive as a bare list or wrapped; None means 'no data at all'."""
    if data is None:
        return None
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "rows", "corporateActions"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return None


def _fmt(d: date) -> str:
    return d.strftime("%d-%m-%Y")


def _sort_key(value) -> str:
    """DD-MM-YYYY -> sortable YYYY-MM-DD; anything unparseable sorts last."""
    text = str(value or "").strip()
    parts = text.split("-")
    if len(parts) == 3 and len(parts[0]) == 2:
        return f"{parts[2]}-{parts[1]}-{parts[0]}"
    return text
