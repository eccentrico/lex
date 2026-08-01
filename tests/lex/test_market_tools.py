import json
from unittest.mock import patch

import pandas as pd

from lex.tools.market import (
    handle_market_quote, handle_price_history, search_symbols,
    handle_market_overview, handle_fundamentals, handle_market_events,
)


def test_market_quote_returns_quotes():
    fake = {"INFY": {"last_price": 1500.5, "ohlc": {"open": 1490}}}
    with patch("services.kite_data.kite_data.get_quotes", return_value=fake):
        out = json.loads(handle_market_quote({"symbols": ["INFY"]}))
    assert out["success"] and out["data"]["INFY"]["last_price"] == 1500.5


def test_market_quote_error_is_enveloped():
    with patch("services.kite_data.kite_data.get_quotes", side_effect=RuntimeError("kite down")):
        out = json.loads(handle_market_quote({"symbols": ["INFY"]}))
    assert out["success"] is False and "kite down" in out["error"]


def test_price_history_serializes_dataframe():
    df = pd.DataFrame({"date": ["2026-07-01"], "open": [1.0], "high": [2.0],
                       "low": [0.5], "close": [1.5], "volume": [100]})
    with patch("services.kite_data.kite_data.get_historical_data", return_value=df):
        out = json.loads(handle_price_history(
            {"symbol": "INFY", "from_date": "2026-07-01", "to_date": "2026-07-10"}))
    assert out["success"] and out["data"]["rows"][0]["close"] == 1.5


DF = pd.DataFrame([
    {"tradingsymbol": "TATAMOTORS", "name": "TATA MOTORS LIMITED", "exchange": "NSE", "instrument_type": "EQ"},
    {"tradingsymbol": "TATASTEEL", "name": "TATA STEEL LIMITED", "exchange": "NSE", "instrument_type": "EQ"},
    {"tradingsymbol": "INFY", "name": "INFOSYS LIMITED", "exchange": "NSE", "instrument_type": "EQ"},
    {"tradingsymbol": "INFY24DECFUT", "name": "INFY", "exchange": "NFO", "instrument_type": "FUT"},
])


def test_finds_by_company_name():
    out = search_symbols("tata motors", instruments_df=DF)
    assert out[0]["tradingsymbol"] == "TATAMOTORS"


def test_finds_by_partial_symbol():
    out = search_symbols("infy", instruments_df=DF)
    assert out[0]["tradingsymbol"] == "INFY"


def test_only_nse_equity():
    out = search_symbols("infy", instruments_df=DF)
    assert all(o["exchange"] == "NSE" for o in out)


def test_no_match_returns_empty():
    assert search_symbols("zzzz-not-a-company", instruments_df=DF) == []


def test_overview_degrades_gracefully():
    with patch("services.kite_data.kite_data.get_quotes",
               return_value={"NIFTY 50": {"last_price": 25000}}), \
         patch("lex.tools.market._fetch_fii_dii", side_effect=RuntimeError("nse down")), \
         patch("lex.tools.market._fetch_sector_indices", return_value={"NIFTY IT": 1.2}):
        out = json.loads(handle_market_overview({}))
    assert out["success"]
    assert out["data"]["indices"]["NIFTY 50"]["last_price"] == 25000
    assert out["data"]["sector_indices"] == {"NIFTY IT": 1.2}
    assert any("fii" in e.lower() or "nse down" in e for e in out["data"]["errors"])


def test_fundamentals_passthrough():
    fake = {"symbol": "TCS", "trailingPE": 28.1, "returnOnEquity": 0.46, "source": "screener"}
    with patch("services.indian_data.fundamental_service.get_enriched_fundamentals",
               return_value=fake):
        out = json.loads(handle_fundamentals({"symbol": "tcs"}))
    assert out["success"] and out["data"]["trailingPE"] == 28.1


def test_fundamentals_error_enveloped():
    with patch("services.indian_data.fundamental_service.get_enriched_fundamentals",
               side_effect=RuntimeError("scrape failed")):
        out = json.loads(handle_fundamentals({"symbol": "TCS"}))
    assert out["success"] is False


def test_events_merges_sources_and_degrades():
    with patch("lex.tools.market._fetch_nse_announcements",
               return_value=[{"subject": "Board meeting"}]), \
         patch("lex.tools.market._fetch_bse_announcements",
               side_effect=RuntimeError("bse down")), \
         patch("lex.tools.market._fetch_earnings",
               return_value=[{"symbol": "TCS", "date": "2026-07-20"}]), \
         patch("lex.tools.market._fetch_corporate_actions",
               return_value=[{"type": "dividend", "subject": "Dividend - Rs 5"}]), \
         patch("lex.tools.market._fetch_board_meetings",
               return_value=[{"date": "20-07-2026", "is_results": True}]), \
         patch("lex.tools.market._fetch_insider_trades",
               side_effect=RuntimeError("pit down")):
        out = json.loads(handle_market_events({"symbol": "TCS"}))
    d = out["data"]
    assert out["success"]
    assert d["announcements"] == [{"subject": "Board meeting"}]
    assert d["earnings"][0]["date"] == "2026-07-20"
    # corporate actions are real corporate actions now, and insider trades have
    # their own key instead of masquerading as one
    assert d["corporate_actions"][0]["type"] == "dividend"
    assert d["board_meetings"][0]["is_results"] is True
    assert d["insider_trades"] == []
    assert any("bse" in e.lower() for e in d["errors"])
    assert any("insider_trades" in e for e in d["errors"])


def test_tools_dict_shape():
    from lex.tools import TOOLS
    for name, t in TOOLS.items():
        assert t["schema"]["name"] == name
        assert callable(t["handler"])
