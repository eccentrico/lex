import json
from unittest.mock import patch

from lex.tools.groww import handle_groww_quote, handle_groww_price_history


def test_groww_quote_returns_quotes():
    fake = {"RELIANCE": {"last_price": 2500.0}}
    with patch("services.groww_data.groww_data.get_quotes", return_value=fake):
        out = json.loads(handle_groww_quote({"symbols": ["RELIANCE"]}))
    assert out["success"] and out["data"]["RELIANCE"]["last_price"] == 2500.0


def test_groww_quote_error_is_enveloped():
    with patch("services.groww_data.groww_data.get_quotes",
               side_effect=RuntimeError("GROWW_API_KEY / GROWW_API_SECRET not set")):
        out = json.loads(handle_groww_quote({"symbols": ["RELIANCE"]}))
    assert out["success"] is False
    assert "not set" in out["error"]


def test_groww_price_history_wraps_raw_payload():
    fake = {"candles": [["2026-07-01 00:00:00", "2026-07-10 23:59:59", "NSE-RELIANCE"]]}
    with patch("services.groww_data.groww_data.get_historical_data", return_value=fake):
        out = json.loads(handle_groww_price_history(
            {"symbol": "RELIANCE", "from_date": "2026-07-01", "to_date": "2026-07-10"}))
    assert out["success"]
    assert out["data"]["symbol"] == "RELIANCE"
    assert out["data"]["raw"] == fake


def test_groww_price_history_error_is_enveloped():
    with patch("services.groww_data.groww_data.get_historical_data",
               side_effect=ValueError("Unsupported interval 'week'")):
        out = json.loads(handle_groww_price_history(
            {"symbol": "RELIANCE", "from_date": "2026-07-01", "to_date": "2026-07-10",
             "interval": "week"}))
    assert out["success"] is False
    assert "Unsupported interval" in out["error"]
