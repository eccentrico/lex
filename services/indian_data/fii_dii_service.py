"""
FII/DII Daily Flow Service.

Fetches institutional activity data from NSE's fiidiiTradeReact endpoint.
FII (Foreign Institutional Investors) and DII (Domestic Institutional Investors)
net buy/sell data is published daily by NSE after market close (~18:30 IST).

Key insight for the trading system:
- Consecutive FII selling sessions (3–5 days) = broad market de-risking signal
- FII net > +₹3,000 Cr on a day = strong bullish institutional flow
- FII net < -₹3,000 Cr on a day = significant outflow pressure
- DII countering FII selling = support floor (domestic institutions buying dips)
- Both FII + DII selling together = strongest bearish signal

The risk_analyst uses these flows to:
1. Adjust market_regime confidence (consecutive FII selling → lean toward
   "trending_bear" even if price hasn't moved much yet)
2. Set a flow_pressure multiplier on position sizes
   (e.g., 3+ days of net FII selling → cap new buys at 50% of normal size)
"""

import logging
from datetime import datetime, date, timedelta
from typing import Any, Optional

from services.indian_data.nse_session import nse_get

logger = logging.getLogger(__name__)

# ── In-memory cache (same-day) ────────────────────────────────────────────────
_CACHE: dict[str, Any] = {}
_CACHE_DATE: Optional[date] = None

# Thresholds (₹ Crore)
_STRONG_FLOW_THRESHOLD = 3_000     # ±₹3,000 Cr = strong signal
_MODERATE_FLOW_THRESHOLD = 1_000   # ±₹1,000 Cr = moderate signal
_CONSECUTIVE_DAYS_SIGNAL = 3       # 3+ consecutive days same direction = trend


def get_fii_dii_flows(days: int = 10) -> list[dict[str, Any]]:
    """
    Fetch recent FII/DII daily flow data from NSE.

    Args:
        days: Number of recent trading days to fetch (default 10).

    Returns:
        List of daily flow dicts, most-recent first:
        [
          {
            "date": "2026-04-01",
            "fii_buy": 15234.50,    # ₹ Crore
            "fii_sell": 12100.25,
            "fii_net": 3134.25,     # positive = net buyer
            "dii_buy": 8900.00,
            "dii_sell": 9800.00,
            "dii_net": -900.00,
          }, ...
        ]
    """
    global _CACHE, _CACHE_DATE

    today = date.today()
    if _CACHE_DATE == today and _CACHE.get("flows"):
        return _CACHE["flows"]

    try:
        data = nse_get("/api/fiidiiTradeReact")
        if not data or not isinstance(data, list):
            logger.warning("FII/DII: empty or unexpected response from NSE")
            return []

        rows = []
        for row in data[:days]:
            try:
                # NSE field names vary slightly across API versions
                trade_date = (
                    row.get("date") or row.get("tradeDate") or row.get("DATE") or ""
                )
                fii_buy  = _f(row.get("fiiBuyValue")  or row.get("FII_BUY_VALUE")  or row.get("buyValue"))
                fii_sell = _f(row.get("fiiSellValue") or row.get("FII_SELL_VALUE") or row.get("sellValue"))
                fii_net  = _f(row.get("fiiNetValue")  or row.get("FII_NET_VALUE")  or row.get("netValue"))
                dii_buy  = _f(row.get("diiBuyValue")  or row.get("DII_BUY_VALUE"))
                dii_sell = _f(row.get("diiSellValue") or row.get("DII_SELL_VALUE"))
                dii_net  = _f(row.get("diiNetValue")  or row.get("DII_NET_VALUE"))

                # Derive net if not directly provided
                if fii_net is None and fii_buy is not None and fii_sell is not None:
                    fii_net = fii_buy - fii_sell
                if dii_net is None and dii_buy is not None and dii_sell is not None:
                    dii_net = dii_buy - dii_sell

                if not trade_date:
                    continue

                rows.append({
                    "date":     _parse_date(trade_date),
                    "fii_buy":  fii_buy  or 0.0,
                    "fii_sell": fii_sell or 0.0,
                    "fii_net":  fii_net  or 0.0,
                    "dii_buy":  dii_buy  or 0.0,
                    "dii_sell": dii_sell or 0.0,
                    "dii_net":  dii_net  or 0.0,
                })
            except Exception as e:
                logger.debug(f"FII/DII row parse error: {e}")
                continue

        rows = [r for r in rows if r["date"]]
        rows.sort(key=lambda r: r["date"], reverse=True)

        _CACHE["flows"] = rows
        _CACHE_DATE = today
        logger.info(f"FII/DII: fetched {len(rows)} days of flow data")
        return rows

    except Exception as e:
        logger.warning(f"FII/DII fetch failed: {e}")
        return []


def analyse_flows(flows: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Derive actionable market signals from FII/DII flow data.

    Returns a dict that risk_analyst merges into its context:
    {
      "latest_fii_net":   3134.25,        # ₹ Cr, today
      "latest_dii_net":   -900.00,
      "fii_5d_net":       8200.00,        # rolling 5-day total
      "fii_consecutive_buy_days":  2,     # +ve = buying streak, -ve = selling streak
      "flow_signal":      "bullish",      # "bullish" | "bearish" | "neutral" | "mixed"
      "flow_pressure":    1.0,            # multiplier for position sizing (0.5–1.0)
      "flow_note":        "FII net buyers for 3 consecutive sessions (+₹8,200 Cr)"
    }
    """
    if not flows:
        return _neutral_flow()

    latest = flows[0]
    recent = flows[:5]

    fii_net_today = latest["fii_net"]
    dii_net_today = latest["dii_net"]
    fii_5d_net = sum(r["fii_net"] for r in recent)

    # Consecutive buy/sell streak
    streak = 0
    for r in flows:
        if r["fii_net"] > 0:
            if streak >= 0:
                streak += 1
            else:
                break
        elif r["fii_net"] < 0:
            if streak <= 0:
                streak -= 1
            else:
                break

    # Classify signal
    if fii_net_today >= _STRONG_FLOW_THRESHOLD and streak >= 2:
        signal = "bullish"
        pressure = 1.0
        note = f"FII strong buyers today (+₹{fii_net_today:,.0f} Cr), {streak}-day buying streak"
    elif fii_net_today <= -_STRONG_FLOW_THRESHOLD and streak <= -2:
        signal = "bearish"
        pressure = 0.5
        note = f"FII strong sellers today (₹{fii_net_today:,.0f} Cr), {abs(streak)}-day selling streak"
    elif fii_net_today <= -_MODERATE_FLOW_THRESHOLD and streak <= -_CONSECUTIVE_DAYS_SIGNAL:
        signal = "bearish"
        pressure = 0.6
        note = f"FII net sellers for {abs(streak)} consecutive sessions (5d total: ₹{fii_5d_net:,.0f} Cr)"
    elif fii_net_today >= _MODERATE_FLOW_THRESHOLD and streak >= _CONSECUTIVE_DAYS_SIGNAL:
        signal = "bullish"
        pressure = 1.0
        note = f"FII net buyers for {streak} consecutive sessions (5d total: +₹{fii_5d_net:,.0f} Cr)"
    elif dii_net_today > _MODERATE_FLOW_THRESHOLD and fii_net_today < 0:
        signal = "mixed"
        pressure = 0.8
        note = f"FII selling (₹{fii_net_today:,.0f} Cr) but DII absorbing (+₹{dii_net_today:,.0f} Cr)"
    elif fii_net_today < -_MODERATE_FLOW_THRESHOLD and dii_net_today < -_MODERATE_FLOW_THRESHOLD:
        signal = "bearish"
        pressure = 0.45
        note = f"Both FII and DII selling today (FII: ₹{fii_net_today:,.0f} Cr, DII: ₹{dii_net_today:,.0f} Cr)"
    else:
        signal = "neutral"
        pressure = 1.0
        note = f"FII flows neutral today (net: ₹{fii_net_today:,.0f} Cr)"

    return {
        "latest_fii_net":             round(fii_net_today, 2),
        "latest_dii_net":             round(dii_net_today, 2),
        "fii_5d_net":                 round(fii_5d_net, 2),
        "fii_consecutive_days":       streak,         # +ve = buying, -ve = selling
        "flow_signal":                signal,
        "flow_pressure":              pressure,
        "flow_note":                  note,
    }


def get_flow_analysis() -> dict[str, Any]:
    """Convenience: fetch flows and return analysis in one call."""
    flows = get_fii_dii_flows(days=10)
    return analyse_flows(flows)


def _neutral_flow() -> dict[str, Any]:
    return {
        "latest_fii_net": 0.0,
        "latest_dii_net": 0.0,
        "fii_5d_net":     0.0,
        "fii_consecutive_days": 0,
        "flow_signal":    "neutral",
        "flow_pressure":  1.0,
        "flow_note":      "FII/DII data unavailable",
    }


def _f(v) -> Optional[float]:
    """Safe float parse."""
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _parse_date(s: str) -> str:
    """Return ISO date string or empty string on failure."""
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s.strip(), fmt).strftime("%Y-%m-%d")
        except (ValueError, AttributeError):
            continue
    return ""
