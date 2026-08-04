import json

import pandas as pd
import pytest

from lex.tools.mutual_funds import (
    search_schemes, handle_fund_search, handle_fund_quote, handle_fund_history)

MF_DF = pd.DataFrame([
    {"tradingsymbol": "120503", "name": "PARAG PARIKH FLEXI CAP FUND - DIRECT GROWTH",
     "amc": "PPFAS", "plan": "direct", "scheme_type": "Equity", "last_price": 75.5,
     "last_price_date": "2026-08-01"},
    {"tradingsymbol": "120504", "name": "PARAG PARIKH FLEXI CAP FUND - REGULAR GROWTH",
     "amc": "PPFAS", "plan": "regular", "scheme_type": "Equity", "last_price": 70.1,
     "last_price_date": "2026-08-01"},
    {"tradingsymbol": "100001", "name": "HDFC TOP 100 FUND - DIRECT GROWTH",
     "amc": "HDFC", "plan": "direct", "scheme_type": "Equity", "last_price": 900.0,
     "last_price_date": "2026-08-01"},
])


def test_finds_by_scheme_name():
    out = search_schemes("parag parikh flexi cap direct", instruments_df=MF_DF)
    assert out[0]["tradingsymbol"] == "120503"


def test_finds_by_exact_scheme_code():
    out = search_schemes("120503", instruments_df=MF_DF)
    assert out[0]["tradingsymbol"] == "120503"


def test_finds_by_amc():
    out = search_schemes("PPFAS", instruments_df=MF_DF)
    assert len(out) == 2
    assert out[0]["amc"] == "PPFAS"
    assert out[1]["amc"] == "PPFAS"


def test_finds_by_amc_lowercase():
    out = search_schemes("hdfc", instruments_df=MF_DF)
    assert len(out) == 1
    assert out[0]["tradingsymbol"] == "100001"


def test_no_match_returns_empty():
    assert search_schemes("zzzz-not-a-fund", instruments_df=MF_DF) == []


def test_handle_fund_search_envelopes(monkeypatch):
    monkeypatch.setattr("services.kite_data.kite_data._get_mf_instruments", lambda: MF_DF)
    out = json.loads(handle_fund_search({"query": "hdfc top 100"}))
    assert out["success"] and out["data"][0]["tradingsymbol"] == "100001"


def test_handle_fund_quote_returns_nav(monkeypatch):
    fake = {"120503": {"scheme_code": "120503", "nav": 75.5}}
    monkeypatch.setattr("services.kite_data.kite_data.get_mf_quote", lambda codes: fake)
    out = json.loads(handle_fund_quote({"scheme_codes": ["120503"]}))
    assert out["success"] and out["data"]["120503"]["nav"] == 75.5


def test_handle_fund_quote_error_enveloped(monkeypatch):
    def raise_error(codes):
        raise RuntimeError("kite down")
    monkeypatch.setattr("services.kite_data.kite_data.get_mf_quote", raise_error)
    out = json.loads(handle_fund_quote({"scheme_codes": ["120503"]}))
    assert out["success"] is False and "kite down" in out["error"]


def test_tools_dict_registers_fund_search_and_quote():
    from lex.tools import TOOLS
    assert TOOLS["fund_search"]["schema"]["name"] == "fund_search"
    assert TOOLS["fund_quote"]["schema"]["name"] == "fund_quote"


def test_handle_fund_history_returns_rows(monkeypatch):
    from unittest.mock import patch
    fake = [{"date": "2026-08-01", "nav": 74.5}]
    with patch("services.indian_data.mutual_funds.get_nav_history", return_value=fake):
        out = json.loads(handle_fund_history(
            {"scheme_code": "120503", "from_date": "2026-08-01", "to_date": "2026-08-02"}))
    assert out["success"] and out["data"]["rows"] == fake


def test_handle_fund_history_error_enveloped(monkeypatch):
    from unittest.mock import patch
    with patch("services.indian_data.mutual_funds.get_nav_history",
              side_effect=RuntimeError("amfi down")):
        out = json.loads(handle_fund_history(
            {"scheme_code": "120503", "from_date": "2026-08-01", "to_date": "2026-08-02"}))
    assert out["success"] is False and "amfi down" in out["error"]


def test_tools_dict_registers_fund_history():
    from lex.tools import TOOLS
    assert TOOLS["fund_history"]["schema"]["name"] == "fund_history"
