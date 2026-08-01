"""Fuzzy company-name → NSE trading-symbol lookup over Kite's instruments dump."""
import difflib
from typing import Optional

from lex.tools.common import _ok, _err


def search_symbols(query: str, limit: int = 5, instruments_df=None) -> list:
    if instruments_df is None:
        from services.kite_data import kite_data
        instruments_df = kite_data._get_instruments()
    df = instruments_df
    eq = df[(df["exchange"] == "NSE") & (df["instrument_type"] == "EQ")]
    q = query.strip().upper()

    # 1) exact / prefix symbol match
    exact = eq[eq["tradingsymbol"].str.upper() == q]
    if len(exact):
        return exact.head(limit).to_dict("records")
    prefix = eq[eq["tradingsymbol"].str.upper().str.startswith(q)]

    # 2) substring on company name
    name_hit = eq[eq["name"].str.upper().str.contains(q, regex=False, na=False)]

    # 3) fuzzy on company name (stdlib difflib — no new dependency)
    names = eq["name"].str.upper().tolist()
    fuzzy_names = set(difflib.get_close_matches(q, names, n=limit, cutoff=0.6))
    fuzzy = eq[eq["name"].str.upper().isin(fuzzy_names)]

    import pandas as pd
    merged = pd.concat([prefix, name_hit, fuzzy]).drop_duplicates("tradingsymbol")
    return merged.head(limit)[
        ["tradingsymbol", "name", "exchange", "instrument_type"]
    ].to_dict("records")


def handle_symbol_search(args: dict, **kwargs) -> str:
    try:
        return _ok(search_symbols(args["query"], limit=int(args.get("limit", 5))))
    except Exception as e:
        return _err(e)


"""Quotes, OHLCV history, and market overview tools."""


def handle_market_quote(args: dict, **kwargs) -> str:
    try:
        from services.kite_data import kite_data
        symbols = [s.upper() for s in args["symbols"]][:25]
        return _ok(kite_data.get_quotes(symbols))
    except Exception as e:
        return _err(e)


def handle_price_history(args: dict, **kwargs) -> str:
    try:
        from services.kite_data import kite_data
        df = kite_data.get_historical_data(
            args["symbol"].upper(), args["from_date"], args["to_date"],
            interval=args.get("interval", "day"),
        )
        rows = df.to_dict("records") if df is not None and len(df) else []
        return _ok({"symbol": args["symbol"].upper(), "rows": rows})
    except Exception as e:
        return _err(e)


INDEX_SYMBOLS = ["NIFTY 50", "NIFTY BANK", "SENSEX"]


def _fetch_fii_dii() -> dict:
    from services.indian_data import fii_dii_service
    return fii_dii_service.get_fii_dii_flows()


def _fetch_sector_indices() -> dict:
    from services.indian_data import sector_indices
    return sector_indices.get_sector_indices()


def handle_market_overview(args: dict, **kwargs) -> str:
    data = {"indices": {}, "sector_indices": {}, "fii_dii": {}, "errors": []}
    try:
        from services.kite_data import kite_data
        data["indices"] = kite_data.get_quotes(INDEX_SYMBOLS)
    except Exception as e:
        data["errors"].append(f"indices: {e}")
    try:
        data["sector_indices"] = _fetch_sector_indices()
    except Exception as e:
        data["errors"].append(f"sector_indices: {e}")
    try:
        data["fii_dii"] = _fetch_fii_dii()
    except Exception as e:
        data["errors"].append(f"fii_dii: {e}")
    return _ok(data)


"""Fundamentals and corporate-events tools."""


def handle_fundamentals(args: dict, **kwargs) -> str:
    try:
        from services.indian_data import fundamental_service
        return _ok(fundamental_service.get_enriched_fundamentals(args["symbol"].upper()))
    except Exception as e:
        return _err(e)


def _fetch_nse_announcements(symbol: str, days_back: int) -> list:
    from services.indian_data import nse_announcements
    return nse_announcements.get_recent_announcements(symbol, days=days_back)


def _fetch_bse_announcements(symbol: str, days_back: int) -> list:
    from services.indian_data import bse_announcements
    return bse_announcements.get_recent_announcements(symbol, days=days_back)


def _fetch_earnings(symbol) -> list:
    from services.indian_data import earnings_calendar
    rows = earnings_calendar.get_earnings_calendar()
    if symbol:
        rows = [r for r in rows if str(r.get("symbol", "")).upper() == symbol]
    return rows


def _fetch_corporate_actions(symbol: str, days_back: int) -> list:
    from services.indian_data import corporate_actions
    return corporate_actions.get_corporate_actions(symbol, days_back=days_back)


def _fetch_board_meetings(symbol: str) -> list:
    from services.indian_data import corporate_actions
    return corporate_actions.get_board_meetings(symbol)


def _fetch_insider_trades(symbol: str) -> list:
    from services.indian_data import nse_events
    return nse_events.get_insider_trades(period="lastmonth", symbol=symbol)


def handle_market_events(args: dict, **kwargs) -> str:
    symbol = (args.get("symbol") or "").upper() or None
    days_back = int(args.get("days_back", 14))
    data = {"announcements": [], "earnings": [], "corporate_actions": [],
            "board_meetings": [], "insider_trades": [], "errors": []}
    if symbol:
        for name, fn in (("nse", _fetch_nse_announcements), ("bse", _fetch_bse_announcements)):
            try:
                data["announcements"] += fn(symbol, days_back) or []
            except Exception as e:
                data["errors"].append(f"{name}: {e}")
        for key, fn in (("corporate_actions",
                         lambda s: _fetch_corporate_actions(s, days_back)),
                        ("board_meetings", _fetch_board_meetings),
                        ("insider_trades", _fetch_insider_trades)):
            try:
                data[key] = fn(symbol) or []
            except Exception as e:
                data["errors"].append(f"{key}: {e}")
    try:
        data["earnings"] = _fetch_earnings(symbol) or []
    except Exception as e:
        data["errors"].append(f"earnings: {e}")
    return _ok(data)
