"""Fund search and quote tools: fuzzy scheme lookup + latest NAV.

Mirrors lex/tools/market.py's symbol_search/market_quote shape, but a scheme is
searched by name/AMC rather than by a partial trading-symbol prefix — nobody
types half an AMFI scheme code.
"""
import difflib

from lex.tools.common import _ok, _err


def search_schemes(query: str, limit: int = 5, instruments_df=None) -> list:
    if instruments_df is None:
        from services.kite_data import kite_data
        instruments_df = kite_data._get_mf_instruments()
    df = instruments_df
    q = query.strip().upper()

    exact = df[df["tradingsymbol"].astype(str).str.upper() == q]
    if len(exact):
        return exact.head(limit).to_dict("records")

    name_hit = df[df["name"].str.upper().str.contains(q, regex=False, na=False)]

    names = df["name"].str.upper().tolist()
    fuzzy_names = set(difflib.get_close_matches(q, names, n=limit, cutoff=0.6))
    fuzzy = df[df["name"].str.upper().isin(fuzzy_names)]

    import pandas as pd
    merged = pd.concat([name_hit, fuzzy]).drop_duplicates("tradingsymbol")
    return merged.head(limit)[
        ["tradingsymbol", "name", "amc", "plan", "scheme_type"]
    ].to_dict("records")


def handle_fund_search(args: dict, **kwargs) -> str:
    try:
        return _ok(search_schemes(args["query"], limit=int(args.get("limit", 5))))
    except Exception as e:
        return _err(e)


def handle_fund_quote(args: dict, **kwargs) -> str:
    try:
        from services.kite_data import kite_data
        codes = [str(c) for c in args["scheme_codes"]][:25]
        return _ok(kite_data.get_mf_quote(codes))
    except Exception as e:
        return _err(e)
