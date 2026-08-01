import json
from lex.tools import portfolio


def test_thesis_roundtrip(lex_home_tmp):
    out = json.loads(portfolio.handle_thesis_note(
        {"symbol": "infy", "thesis": "IT recovery play"}))
    assert out["success"]
    assert portfolio.get_thesis("INFY") == "IT recovery play"
    assert portfolio.get_thesis("TCS") is None


def test_portfolio_status_joins_thesis(lex_home_tmp, monkeypatch):
    portfolio.handle_thesis_note({"symbol": "INFY", "thesis": "hold"})
    monkeypatch.setattr("services.kite_data.kite_data.get_holdings", lambda: [
        {"tradingsymbol": "INFY", "quantity": 10,
         "average_price": 1400.0, "last_price": 1500.0}])
    monkeypatch.setattr(portfolio, "_get_kite", lambda: type("K", (), {
        "margins": lambda self: {"equity": {"available": {"live_balance": 50000.0}}}})())
    data = json.loads(portfolio.handle_portfolio_status({}))["data"]
    assert data["holdings"][0]["pnl"] == 1000.0
    assert data["holdings"][0]["thesis"] == "hold"
    assert data["total_value"] == 50000.0 + 15000.0


def test_portfolio_status_fail_closed_on_margins(lex_home_tmp, monkeypatch):
    monkeypatch.setattr("services.kite_data.kite_data.get_holdings", lambda: [])
    def boom():
        raise RuntimeError("token expired")
    monkeypatch.setattr(portfolio, "_get_kite", boom)
    out = json.loads(portfolio.handle_portfolio_status({}))
    assert not out["success"]
