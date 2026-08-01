"""Who is buying and selling the stock: insider (PIT), bulk and block deals.

These three feeds were already vendored and only reachable as a mislabelled
corner of `market_events`. Promoter behaviour is where a lot of the India-
specific signal lives, so it gets its own tool.
"""
import logging

from lex.tools.common import _ok, _err

logger = logging.getLogger(__name__)

_PERIODS = {"lastday", "lastweek", "lastmonth"}


def handle_ownership_signals(args: dict) -> str:
    from services.indian_data import nse_events
    symbol = (args.get("symbol") or "").strip().upper() or None
    period = args.get("period", "lastmonth")
    if period not in _PERIODS:
        period = "lastmonth"
    data = {"symbol": symbol, "period": period, "insider_trades": [],
            "bulk_deals": [], "block_deals": [], "summary": {}, "errors": []}

    try:
        data["insider_trades"] = nse_events.get_insider_trades(
            period=period, symbol=symbol) or []
    except Exception as e:
        data["errors"].append(f"insider_trades: {e}")

    # bulk/block feeds are per trade-date, not per symbol — fetch today's and
    # filter. NSE pulled the bulk-deals JSON endpoint, so an empty list here
    # means "no data available", not "no deals happened".
    for key, fetch in (("bulk_deals", nse_events.get_bulk_deals),
                       ("block_deals", nse_events.get_block_deals)):
        try:
            rows = fetch() or []
            data[key] = [r for r in rows if r.get("symbol") == symbol] if symbol else rows
        except Exception as e:
            data["errors"].append(f"{key}: {e}")

    insider = data["insider_trades"]
    data["summary"] = {
        "insider_buys": sum(1 for t in insider if t.get("direction") == "BUY"),
        "insider_sells": sum(1 for t in insider if t.get("direction") == "SELL"),
        "promoter_involved": any(t.get("insider_type") == "promoter" for t in insider),
        "pledge_or_encumbrance": any(
            "PLEDGE" in str(t.get("category", "")).upper()
            or "ENCUMBER" in str(t.get("category", "")).upper()
            for t in insider),
        "net_insider_value_inr": round(
            sum((t.get("value_inr") or 0) * (1 if t.get("direction") == "BUY" else -1)
                for t in insider if t.get("direction") in ("BUY", "SELL")), 2),
    }
    return _ok(data)


def handle_corporate_actions(args: dict) -> str:
    try:
        from services.indian_data import corporate_actions
        symbol = args["symbol"].strip().upper()
        days_back = int(args.get("days_back", 90))
        data = {"symbol": symbol, "actions": [], "board_meetings": [], "errors": []}
        try:
            data["actions"] = corporate_actions.get_corporate_actions(
                symbol, days_back=days_back)
        except Exception as e:
            data["errors"].append(f"corporate_actions: {e}")
        try:
            data["board_meetings"] = corporate_actions.get_board_meetings(symbol)
        except Exception as e:
            data["errors"].append(f"board_meetings: {e}")
        return _ok(data)
    except Exception as e:
        return _err(e)
