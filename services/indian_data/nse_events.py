"""
NSE high-signal market event feeds.

Fetches three categories of events that are among the strongest short-term
price signals available in Indian markets:

  1. Bulk Deals  — large single-day trades (>0.5% of equity) by any entity
  2. Block Deals — pre-open negotiated trades between institutions
  3. Insider / PIT Trades — promoter and designated-person transactions

All three feeds are market-wide (not per-symbol) and are filtered per symbol
by the polling service. Each returns a list of dicts ready to be stored in
the news_entries table via insert_batch().

Public API:
    get_bulk_deals(trade_date=None)   → list[dict]
    get_block_deals(trade_date=None)  → list[dict]
    get_insider_trades(period="lastday", symbol=None) → list[dict]

    format_as_news_entries(events, event_type) → list[news_entry dicts]

Sentiment logic:
    Bulk/block BUY  → +0.65 / +0.50
    Bulk/block SELL → -0.65 / -0.50
    Insider BUY     → +0.75 (promoter conviction signal)
    Insider SELL    → -0.55 (insider reducing stake)
"""

import logging
from datetime import datetime, date as date_type
from typing import Any, Optional

from services.indian_data.nse_session import nse_get

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _fmt_nse_date(d: date_type) -> str:
    return d.strftime("%d-%m-%Y")


def _parse_nse_date(s: str) -> Optional[datetime]:
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d", "%d-%b-%Y %H:%M:%S"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    return None


def _today_str() -> str:
    return _fmt_nse_date(datetime.now().date())


# ---------------------------------------------------------------------------
# Bulk Deals
# ---------------------------------------------------------------------------

# NSE bulk-deal threshold: trade > 0.5% of listed equity on a single day
# Source: https://www.nseindia.com/api/bulk-deals
#
# NSE field names (as observed):
#   symbol, clientName, dealType (BUY/SELL), quantity, price, tradeDate
#   or: BD_SYMBOL, BD_CLIENT_NAME, BD_BUY_SELL, BD_QTY_TRD, BD_TP_WATP, BD_DT_DATE

def get_bulk_deals(trade_date: Optional[str] = None) -> list[dict[str, Any]]:
    """
    Fetch bulk deals for a given date (DD-MM-YYYY). Defaults to today.

    NOTE: NSE removed the /api/bulk-deals JSON endpoint (returns 404 as of Apr 2026).
    The endpoint is kept here so the polling service continues to compile; it will
    simply return an empty list until NSE restores the API or an alternative is found.

    Returns:
        List of normalised bulk deal dicts (raw_type="bulk_deal").
    """
    today = trade_date or _today_str()
    data = nse_get("/api/bulk-deals", params={"date": today})
    if not data:
        return []

    rows: list = []
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = data.get("data") or data.get("bulk") or []

    result = []
    for row in rows:
        symbol = (
            row.get("symbol") or row.get("BD_SYMBOL") or ""
        ).strip().upper()
        client = (
            row.get("clientName") or row.get("BD_CLIENT_NAME") or "Unknown"
        ).strip()
        deal_type = (
            row.get("dealType") or row.get("BD_BUY_SELL") or ""
        ).strip().upper()
        qty = row.get("quantity") or row.get("BD_QTY_TRD") or 0
        price = row.get("price") or row.get("BD_TP_WATP") or 0
        trade_dt = (
            row.get("tradeDate") or row.get("BD_DT_DATE") or _today_str()
        )

        # Normalise buy/sell flag
        if deal_type in ("B", "BUY", "BUY/SUBSCRIBE"):
            direction = "BUY"
        elif deal_type in ("S", "SELL", "SELL/TRANSFER"):
            direction = "SELL"
        else:
            direction = "UNKNOWN"

        if symbol:
            result.append({
                "raw_type": "bulk_deal",
                "symbol": symbol,
                "client": client,
                "direction": direction,
                "quantity": _safe_int(qty),
                "price": _safe_float(price),
                "trade_date": trade_dt,
            })

    logger.info(f"Fetched {len(result)} bulk deals")
    return result


# ---------------------------------------------------------------------------
# Block Deals
# ---------------------------------------------------------------------------

# NSE block-deal window: 08:45–09:00 IST (pre-open negotiated trades)
# Source: https://www.nseindia.com/api/block-deal
#
# NSE field names (as observed):
#   symbol, clientName, buySell (B/S), noOfSharesBought/noOfSharesSold,
#   tradePrice, tradeDate
#   or: BD_SYMBOL, BD_CLIENT_NAME, BD_BUY_SELL, BD_QTY_TRD, BD_TP_WATP, BD_DT_DATE

def get_block_deals(trade_date: Optional[str] = None) -> list[dict[str, Any]]:
    """
    Fetch block deals for a given date (DD-MM-YYYY). Defaults to today.

    Returns:
        List of normalised block deal dicts (raw_type="block_deal").
    """
    params = {}
    if trade_date:
        params["date"] = trade_date

    data = nse_get("/api/block-deal", params=params or None)
    if not data:
        return []

    rows: list = []
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = data.get("data") or data.get("block") or []

    result = []
    for row in rows:
        symbol = (
            row.get("symbol") or row.get("BD_SYMBOL") or ""
        ).strip().upper()
        client = (
            row.get("clientName") or row.get("BD_CLIENT_NAME") or "Unknown"
        ).strip()
        buy_sell = (
            row.get("buySell") or row.get("BD_BUY_SELL") or ""
        ).strip().upper()
        qty = (
            row.get("noOfSharesBought")
            or row.get("noOfSharesSold")
            or row.get("BD_QTY_TRD")
            or 0
        )
        price = row.get("tradePrice") or row.get("BD_TP_WATP") or 0
        trade_dt = (
            row.get("tradeDate") or row.get("BD_DT_DATE") or _today_str()
        )

        direction = "BUY" if buy_sell in ("B", "BUY") else "SELL" if buy_sell in ("S", "SELL") else "UNKNOWN"

        if symbol:
            result.append({
                "raw_type": "block_deal",
                "symbol": symbol,
                "client": client,
                "direction": direction,
                "quantity": _safe_int(qty),
                "price": _safe_float(price),
                "trade_date": trade_dt,
            })

    logger.info(f"Fetched {len(result)} block deals")
    return result


# ---------------------------------------------------------------------------
# Insider / PIT Trades
# ---------------------------------------------------------------------------

# SEBI's Prohibition of Insider Trading (PIT) regulations require promoters
# and designated persons to disclose trades within 2 trading days.
# Source: https://www.nseindia.com/api/corporates-pit?index=equities&period=lastday
#
# period values: "lastday", "lastweek", "lastmonth"
# NSE field names (as observed):
#   symbol, acqName, acqCategory, secType, secAcq, secVal, tdpTransactionType,
#   date (or "acqfromDt"/"acqtoDt")

def get_insider_trades(
    period: str = "lastday",
    symbol: Optional[str] = None,
) -> list[dict[str, Any]]:
    """
    Fetch insider (PIT) trades disclosed to NSE.

    Args:
        period: "lastday", "lastweek", or "lastmonth"
        symbol: Optional — filter by NSE symbol after fetching

    Returns:
        List of normalised insider trade dicts (raw_type="insider_trade").
    """
    params = {"index": "equities", "period": period}
    if symbol:
        params["symbol"] = symbol.upper()

    data = nse_get("/api/corporates-pit", params=params)
    if not data:
        return []

    rows: list = []
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = data.get("data") or data.get("pit") or []

    result = []
    for row in rows:
        sym = (row.get("symbol") or "").strip().upper()
        acquirer = (row.get("acqName") or row.get("personName") or "Unknown").strip()
        category = (row.get("acqCategory") or row.get("category") or "").strip()
        transaction = (
            row.get("tdpTransactionType") or row.get("transactionType") or ""
        ).strip().upper()
        qty = row.get("secAcq") or row.get("noOfSec") or 0
        value = row.get("secVal") or row.get("value") or 0
        trade_dt = (
            row.get("acqfromDt") or row.get("date") or row.get("intimDate") or _today_str()
        )

        # Normalise direction from NSE tdpTransactionType values.
        # NSE uses varied strings: "Buy", "Sell", "Pledge", "Revoke Pledge",
        # "Transmission of shares", "Conversion of security", "ESOP", "Gift", etc.
        t = transaction  # already upper-cased
        if any(k in t for k in ("BUY", "ACQUIRE", "SUBSCRI", "ALLOT", "ESOP", "REVOKE")):
            direction = "BUY"
        elif any(k in t for k in ("SELL", "DISPOS", "TRANSFER", "PLEDGE", "ENCUMBER")):
            direction = "SELL"
        elif any(k in t for k in ("CONVERSION", "TRANSMISSION", "GIFT", "INHERIT")):
            direction = "NEUTRAL"
        else:
            direction = "UNKNOWN"

        # Classify insider type
        cat_upper = category.upper()
        if "PROMOTER" in cat_upper:
            insider_type = "promoter"
        elif "DIRECTOR" in cat_upper or "KMP" in cat_upper or "DESIGNATED" in cat_upper:
            insider_type = "director_kmp"
        else:
            insider_type = "other_insider"

        if sym:
            result.append({
                "raw_type": "insider_trade",
                "symbol": sym,
                "acquirer": acquirer,
                "category": category,
                "insider_type": insider_type,
                "direction": direction,
                "quantity": _safe_int(qty),
                "value_inr": _safe_float(value),
                "trade_date": trade_dt,
            })

    # Filter by symbol if requested
    if symbol:
        result = [r for r in result if r["symbol"] == symbol.upper()]

    logger.info(f"Fetched {len(result)} insider trades (period={period})")
    return result


# ---------------------------------------------------------------------------
# Sentiment scoring
# ---------------------------------------------------------------------------

_SENTIMENT = {
    # (raw_type, direction) → sentiment score
    ("bulk_deal",    "BUY"):     0.65,
    ("bulk_deal",    "SELL"):   -0.65,
    ("block_deal",   "BUY"):    0.50,
    ("block_deal",   "SELL"):   -0.50,
    ("insider_trade","BUY"):    0.75,   # Promoter/director conviction signal
    ("insider_trade","SELL"):  -0.55,   # Stake reduction — caution
    ("insider_trade","NEUTRAL"): 0.0,   # Conversion/transmission — no signal
    ("insider_trade","UNKNOWN"): 0.0,
}

_INSIDER_PROMOTER_BOOST = 0.10  # extra sentiment for promoter vs. director/KMP

def _score_event(event: dict) -> float:
    """Compute sentiment score for a single event dict."""
    base = _SENTIMENT.get((event["raw_type"], event["direction"]), 0.0)
    # Only apply promoter boost when there's already a directional signal
    # (base == 0.0 means direction is UNKNOWN — leave it neutral)
    if base != 0.0 and event["raw_type"] == "insider_trade" and event.get("insider_type") == "promoter":
        base += _INSIDER_PROMOTER_BOOST if base > 0 else -_INSIDER_PROMOTER_BOOST
    return max(-1.0, min(1.0, base))


# ---------------------------------------------------------------------------
# Format as news_entries rows
# ---------------------------------------------------------------------------

def format_as_news_entries(
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Convert raw NSE event dicts into news_entries rows ready for insert_batch().

    Each event gets:
      - feed_key:       "nse_bulk_deal" | "nse_block_deal" | "nse_insider_trade"
      - title:          Human-readable one-liner (contains symbol name for text search)
      - summary:        More detail (acquirer, qty, price)
      - source:         "NSE"
      - published:      ISO datetime string
      - symbol:         NSE symbol (for indexed DB query)
      - sentiment_score: Pre-computed based on direction + insider type
      - relevance_score: 0.90–0.95 (these are highly stock-specific events)
    """
    entries = []
    for ev in events:
        raw_type = ev["raw_type"]
        symbol = ev["symbol"]
        direction = ev["direction"]
        sentiment = _score_event(ev)

        # Parse trade date into ISO datetime
        parsed_dt = _parse_nse_date(str(ev.get("trade_date", "")))
        published_iso = parsed_dt.isoformat() if parsed_dt else datetime.now().isoformat()

        if raw_type == "bulk_deal":
            feed_key = "nse_bulk_deal"
            title = (
                f"NSE Bulk Deal: {ev['client']} {direction}S {symbol} "
                f"— {_fmt_qty(ev['quantity'])} shares @ ₹{ev['price']:.1f}"
            )
            summary = (
                f"Bulk deal on NSE: {ev['client']} executed a large {direction.lower()} "
                f"of {_fmt_qty(ev['quantity'])} shares of {symbol} at ₹{ev['price']:.2f}. "
                f"This constitutes >0.5%% of listed equity (SEBI bulk-deal threshold)."
            )
            relevance = 0.90

        elif raw_type == "block_deal":
            feed_key = "nse_block_deal"
            title = (
                f"NSE Block Deal: {ev['client']} {direction}S {symbol} "
                f"— {_fmt_qty(ev['quantity'])} shares @ ₹{ev['price']:.1f}"
            )
            summary = (
                f"Pre-open block deal on NSE: {ev['client']} {direction.lower()}s "
                f"{_fmt_qty(ev['quantity'])} shares of {symbol} at ₹{ev['price']:.2f}."
            )
            relevance = 0.90

        elif raw_type == "insider_trade":
            feed_key = "nse_insider_trade"
            insider_label = ev.get("insider_type", "insider").replace("_", " ").title()
            _dir_verb = {"BUY": "BUYS", "SELL": "SELLS", "NEUTRAL": "TRANSACTS", "UNKNOWN": "TRANSACTS"}.get(direction, "TRANSACTS")
            title = (
                f"NSE Insider Trade ({insider_label}): {ev['acquirer']} {_dir_verb} {symbol} "
                f"— {_fmt_qty(ev['quantity'])} shares"
            )
            value_cr = ev.get("value_inr", 0) / 1e7  # convert to crores
            summary = (
                f"PIT disclosure: {ev['acquirer']} ({ev.get('category', insider_label)}) "
                f"{direction.lower()}s {_fmt_qty(ev['quantity'])} shares of {symbol}. "
                f"Transaction value: ₹{value_cr:.2f} Cr. "
                f"Source: NSE PIT / SEBI insider trading regulations."
            )
            relevance = 0.95

        else:
            continue

        entries.append({
            "feed_key": feed_key,
            "title": title,
            "link": f"https://www.nseindia.com/companies-listing/corporate-filings-announcements",
            "published": published_iso,
            "summary": summary,
            "source": "NSE",
            "symbol": symbol,
            "sentiment_score": sentiment,
            "relevance_score": relevance,
        })

    return entries


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _safe_int(v) -> int:
    try:
        return int(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0


def _safe_float(v) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


def _fmt_qty(qty: int) -> str:
    """Format large quantities as L (lakh) or Cr (crore) for readability."""
    if qty >= 1_00_00_000:
        return f"{qty / 1_00_00_000:.1f}Cr"
    if qty >= 1_00_000:
        return f"{qty / 1_00_000:.1f}L"
    return f"{qty:,}"
