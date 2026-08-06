"""Groww Trade API tools: an explicit secondary market-data source alongside Kite.

Only reached for when the user specifically asks for Groww's numbers (see the
GROWW guidance block in lex/prompt.py) — never a silent fallback for
market_quote/price_history.
"""
from lex.tools.common import _ok, _err


def handle_groww_quote(args: dict, **kwargs) -> str:
    try:
        from services.groww_data import groww_data
        symbols = [s.upper() for s in args["symbols"]][:25]
        return _ok(groww_data.get_quotes(symbols))
    except Exception as e:
        return _err(e)


def handle_groww_price_history(args: dict, **kwargs) -> str:
    try:
        from services.groww_data import groww_data
        raw = groww_data.get_historical_data(
            args["symbol"].upper(), args["from_date"], args["to_date"],
            interval=args.get("interval", "day"),
        )
        return _ok({"symbol": args["symbol"].upper(), "raw": raw})
    except Exception as e:
        return _err(e)
