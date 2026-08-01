"""
Sectoral Index Context Service.

Fetches live NSE sectoral index values so the technical_analyst's LLM
can understand whether a stock is swimming with or against its sector tide.

Why this matters:
  - A technically oversold IT stock is a better buy when NIFTY IT is flat
    than when NIFTY IT is in a confirmed 15% downtrend.
  - Strong sector momentum amplifies individual stock signals.
  - Weak/negative sector trend should discount buy signals.

Indices tracked:
  NIFTY BANK     — HDFCBANK, ICICIBANK, SBIN, KOTAKBANK, AXISBANK
  NIFTY IT       — TCS, INFY, WIPRO, HCLTECH, TECHM
  NIFTY PHARMA   — SUNPHARMA, DRREDDY, CIPLA, DIVISLAB
  NIFTY AUTO     — MARUTI, TATAMOTORS, M&M, BAJAJ-AUTO, HEROMOTOCO
  NIFTY FMCG     — HINDUNILVR, ITC, NESTLEIND, BRITANNIA, DABUR
  NIFTY METAL    — TATASTEEL, JSWSTEEL, HINDALCO, VEDL
  NIFTY ENERGY   — RELIANCE, ONGC, IOC, BPCL
  NIFTY REALTY   — DLF, GODREJPROP, OBEROIRLTY
  NIFTY 50       — Benchmark

Source: NSE /api/allIndices (same endpoint as VIX — one call covers all)

Output feeds into the technical_analyst LLM prompt as a "market_context" block,
giving the LLM the broader sector picture alongside the individual stock indicators.
"""

import logging
from datetime import date
from typing import Any, Optional

from services.indian_data.nse_session import nse_get

logger = logging.getLogger(__name__)

# ── Same-day cache ─────────────────────────────────────────────────────────────
_CACHE: dict[str, Any] = {}
_CACHE_DATE: Optional[date] = None

# NSE index name → short label used in prompts and logs
_SECTOR_MAP = {
    "NIFTY 50":           "nifty50",
    "NIFTY BANK":         "bank",
    "NIFTY IT":           "it",
    "NIFTY PHARMA":       "pharma",
    "Nifty Auto":         "auto",
    "NIFTY FMCG":         "fmcg",
    "NIFTY METAL":        "metal",
    "NIFTY ENERGY":       "energy",
    "NIFTY REALTY":       "realty",
    "NIFTY MIDCAP 100":   "midcap100",
    "NIFTY SMALLCAP 100": "smallcap100",
    "INDIA VIX":          "vix",
}

# Map NSE symbols → sector label (for technical_analyst to pick correct index)
SYMBOL_TO_SECTOR = {
    # Banking
    "HDFCBANK": "bank", "ICICIBANK": "bank", "SBIN": "bank",
    "KOTAKBANK": "bank", "AXISBANK": "bank", "INDUSINDBK": "bank",
    # IT
    "TCS": "it", "INFY": "it", "WIPRO": "it", "HCLTECH": "it", "TECHM": "it",
    # Pharma
    "SUNPHARMA": "pharma", "DRREDDY": "pharma", "CIPLA": "pharma",
    "DIVISLAB": "pharma", "APOLLOHOSP": "pharma",
    # Auto
    "MARUTI": "auto", "TATAMOTORS": "auto", "M&M": "auto",
    "BAJAJ-AUTO": "auto", "HEROMOTOCO": "auto", "EICHERMOT": "auto",
    # FMCG
    "HINDUNILVR": "fmcg", "ITC": "fmcg", "NESTLEIND": "fmcg",
    "BRITANNIA": "fmcg", "DABUR": "fmcg", "MARICO": "fmcg",
    # Metal / Mining
    "TATASTEEL": "metal", "JSWSTEEL": "metal", "HINDALCO": "metal",
    "VEDL": "metal", "COALINDIA": "metal", "NMDC": "metal",
    "SAIL": "metal", "NATIONALUM": "metal",
    # Energy
    "RELIANCE": "energy", "ONGC": "energy", "IOC": "energy",
    "BPCL": "energy", "GAIL": "energy", "NTPC": "energy", "POWERGRID": "energy",
    # Realty
    "DLF": "realty", "GODREJPROP": "realty",
}


def get_sector_indices() -> dict[str, dict[str, Any]]:
    """
    Fetch live sectoral index values from NSE.

    Returns:
        {
          "nifty50": {"last": 23450.25, "change_pct": 0.42, "trend": "up"},
          "bank":    {"last": 51200.50, "change_pct": -0.85, "trend": "down"},
          "it":      {"last": 38900.10, "change_pct": 1.12, "trend": "up"},
          ...
        }
    """
    global _CACHE, _CACHE_DATE
    today = date.today()
    if _CACHE_DATE == today and _CACHE.get("indices"):
        return _CACHE["indices"]

    try:
        data = nse_get("/api/allIndices")
        if not data:
            return {}

        index_list = data if isinstance(data, list) else data.get("data", [])
        result: dict[str, dict[str, Any]] = {}

        for row in index_list:
            name = str(row.get("index") or row.get("indexSymbol") or "")
            label = _SECTOR_MAP.get(name)
            if not label:
                continue

            last       = _f(row.get("last") or row.get("lastPrice"))
            change_pct = _f(row.get("percentChange") or row.get("pChange") or 0.0)
            prev_close = _f(row.get("previousClose") or row.get("previousOHLC", {}).get("close") if isinstance(row.get("previousOHLC"), dict) else None)

            if last is None:
                continue

            trend = "up" if (change_pct or 0) > 0.2 else \
                    "down" if (change_pct or 0) < -0.2 else "flat"

            result[label] = {
                "last":       round(last, 2),
                "change_pct": round(change_pct or 0.0, 2),
                "prev_close": round(prev_close, 2) if prev_close else None,
                "trend":      trend,
                "name":       name,
            }

        _CACHE["indices"] = result
        _CACHE_DATE = today
        logger.info(f"Sector indices loaded: {list(result.keys())}")
        return result

    except Exception as e:
        logger.warning(f"Sector indices fetch failed: {e}")
        return {}


def get_sector_for_symbol(symbol: str) -> Optional[str]:
    """Return the sector label for a given NSE symbol."""
    return SYMBOL_TO_SECTOR.get(symbol.upper())


# label -> NSE index name, for the endpoints that want the full name back
_LABEL_TO_INDEX = {v: k for k, v in _SECTOR_MAP.items()}


def peers_in_sector(symbol: str) -> list[str]:
    """Other symbols this module maps to the same sector, alphabetically."""
    sector = get_sector_for_symbol(symbol)
    if not sector:
        return []
    return sorted(s for s, sec in SYMBOL_TO_SECTOR.items()
                  if sec == sector and s != symbol.upper())


def get_index_history(label: str, days: int = 400) -> list[dict[str, Any]]:
    """Daily closes for a sector-index label ("it", "nifty50", ...).

    Kite's instrument dump only resolves EQ symbols, so index history has to
    come from NSE. Returns [] when the endpoint is unavailable — callers must
    treat that as "unknown", not as "flat".
    """
    from datetime import timedelta
    index_name = _LABEL_TO_INDEX.get(label)
    if not index_name:
        return []
    today = date.today()
    data = nse_get("/api/historical/indicesHistory", params={
        "indexType": index_name,
        "from": (today - timedelta(days=days)).strftime("%d-%m-%Y"),
        "to": today.strftime("%d-%m-%Y"),
    })
    rows = []
    if isinstance(data, dict):
        payload = data.get("data") or {}
        rows = payload.get("indexCloseOnlineRecords") or [] if isinstance(payload, dict) else []
    elif isinstance(data, list):
        rows = data
    out = []
    for row in rows:
        close = _f(row.get("EOD_CLOSE_INDEX_VAL") or row.get("close"))
        stamp = row.get("EOD_TIMESTAMP") or row.get("date")
        if close is not None and stamp:
            out.append({"date": str(stamp), "close": close})
    return out


def build_sector_context_text(symbol: str,
                               indices: Optional[dict] = None) -> str:
    """
    Build a short market-context paragraph for injection into LLM prompts.

    Example output:
      'Sector context: HDFCBANK belongs to NIFTY BANK which is currently
       DOWN -0.85% today. Nifty 50 is UP +0.42%. This is a headwind for
       the stock despite any positive individual signals.'

    Args:
        symbol: NSE stock symbol
        indices: Pre-fetched index dict (fetched fresh if not provided)

    Returns:
        Context string ready to inject into prompt, or empty string if unavailable.
    """
    if indices is None:
        indices = get_sector_indices()
    if not indices:
        return ""

    sector = get_sector_for_symbol(symbol)
    sector_data = indices.get(sector) if sector else None
    nifty50 = indices.get("nifty50")
    vix_data = indices.get("vix")

    parts = []

    if nifty50:
        direction = "UP" if nifty50["change_pct"] > 0 else "DOWN"
        parts.append(f"NIFTY 50 is {direction} {nifty50['change_pct']:+.2f}% today")

    if sector_data:
        direction = "UP" if sector_data["change_pct"] > 0 else "DOWN"
        parts.append(
            f"{symbol} sector ({sector_data['name']}) is {direction} "
            f"{sector_data['change_pct']:+.2f}% today"
        )
        # Headwind/tailwind note
        if sector_data["trend"] == "down":
            parts.append("sector is a HEADWIND — discount bullish individual signals")
        elif sector_data["trend"] == "up":
            parts.append("sector is a TAILWIND — amplifies bullish individual signals")

    if vix_data:
        parts.append(f"India VIX: {vix_data['last']:.2f}")

    return ". ".join(parts) + "." if parts else ""


def _f(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return None
