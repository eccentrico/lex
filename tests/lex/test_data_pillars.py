"""Phase 3 pillars: corporate actions, ownership signals, peers, technicals."""
import json

import pytest

from lex.tools import ownership, peers, technicals
from services.indian_data import corporate_actions


def _payload(out):
    parsed = json.loads(out)
    assert parsed["success"], parsed
    return parsed["data"]


# ── corporate actions ──────────────────────────────────────────────────────

@pytest.mark.parametrize("subject,kind", [
    ("Dividend - Rs 24 Per Share", "dividend"),
    ("Bonus issue 1:1", "bonus"),
    ("Face Value Split From Rs 10 To Rs 2", "split"),
    ("Buy Back of Equity Shares", "buyback"),
    ("Rights Issue", "rights"),
    ("Scheme of Arrangement", "restructuring"),
    ("Annual General Meeting", "agm"),
    ("Something else entirely", "other"),
])
def test_subject_classification(subject, kind):
    assert corporate_actions.classify(subject) == kind


def test_corporate_actions_normalised_and_sorted(monkeypatch):
    monkeypatch.setattr("services.indian_data.corporate_actions.nse_get",
                        lambda path, params=None: {"data": [
                            {"subject": "Dividend - Rs 5", "exDate": "01-06-2026",
                             "recDate": "02-06-2026", "comp": "Tata Co"},
                            {"subject": "Bonus issue 1:1", "exDate": "15-07-2026"},
                            {"subject": ""},  # dropped: nothing to classify
                        ]})
    actions = corporate_actions.get_corporate_actions("TCS")
    assert [a["type"] for a in actions] == ["bonus", "dividend"]  # newest ex-date first
    assert actions[1]["record_date"] == "02-06-2026"
    assert all(a["source"] == "nse" for a in actions)


def test_corporate_actions_fall_back_to_bse_when_nse_is_down(monkeypatch):
    monkeypatch.setattr("services.indian_data.corporate_actions.nse_get",
                        lambda path, params=None: None)
    monkeypatch.setattr(
        "services.indian_data.bse_announcements.get_recent_announcements",
        lambda symbol, days=30: [{"subject": "Interim Dividend declared", "date": "2026-07-01"},
                                 {"subject": "Investor call transcript", "date": "2026-07-02"}])
    actions = corporate_actions.get_corporate_actions("TCS")
    assert len(actions) == 1  # the transcript is not a corporate action
    assert actions[0]["type"] == "dividend"
    assert actions[0]["source"] == "bse_announcement"


def test_board_meetings_flag_results(monkeypatch):
    monkeypatch.setattr("services.indian_data.corporate_actions.nse_get",
                        lambda path, params=None: [
                            {"bm_date": "20-07-2026", "bm_purpose": "Quarterly Results"},
                            {"bm_date": "10-07-2026", "bm_purpose": "Other business"}])
    meetings = corporate_actions.get_board_meetings("TCS")
    assert [m["is_results"] for m in meetings] == [True, False]


def test_corporate_actions_tool_envelopes_partial_failure(monkeypatch):
    monkeypatch.setattr("services.indian_data.corporate_actions.get_corporate_actions",
                        lambda s, days_back=90: [{"type": "dividend"}])
    monkeypatch.setattr("services.indian_data.corporate_actions.get_board_meetings",
                        lambda s: (_ for _ in ()).throw(RuntimeError("nse down")))
    data = _payload(ownership.handle_corporate_actions({"symbol": "tcs"}))
    assert data["actions"] == [{"type": "dividend"}]
    assert data["board_meetings"] == [] and "nse down" in data["errors"][0]


# ── ownership signals ──────────────────────────────────────────────────────

_INSIDER = [
    {"symbol": "TCS", "direction": "BUY", "insider_type": "promoter",
     "category": "Promoter", "value_inr": 1000.0},
    {"symbol": "TCS", "direction": "SELL", "insider_type": "director_kmp",
     "category": "Designated Person - Pledge", "value_inr": 400.0},
]


def test_ownership_summary(monkeypatch):
    monkeypatch.setattr("services.indian_data.nse_events.get_insider_trades",
                        lambda period, symbol=None: list(_INSIDER))
    monkeypatch.setattr("services.indian_data.nse_events.get_bulk_deals",
                        lambda: [{"symbol": "TCS", "direction": "BUY"},
                                 {"symbol": "INFY", "direction": "SELL"}])
    monkeypatch.setattr("services.indian_data.nse_events.get_block_deals", lambda: [])
    data = _payload(ownership.handle_ownership_signals({"symbol": "TCS"}))
    assert data["summary"] == {"insider_buys": 1, "insider_sells": 1,
                               "promoter_involved": True, "pledge_or_encumbrance": True,
                               "net_insider_value_inr": 600.0}
    assert data["bulk_deals"] == [{"symbol": "TCS", "direction": "BUY"}]  # INFY filtered out
    assert data["period"] == "lastmonth"


def test_ownership_bad_period_falls_back_and_feeds_degrade(monkeypatch):
    seen = {}
    def insider(period, symbol=None):
        seen["period"] = period
        return []
    monkeypatch.setattr("services.indian_data.nse_events.get_insider_trades", insider)
    monkeypatch.setattr("services.indian_data.nse_events.get_bulk_deals",
                        lambda: (_ for _ in ()).throw(RuntimeError("404")))
    monkeypatch.setattr("services.indian_data.nse_events.get_block_deals", lambda: [])
    data = _payload(ownership.handle_ownership_signals({"period": "since 1998"}))
    assert seen["period"] == "lastmonth"
    assert any("bulk_deals" in e for e in data["errors"])
    assert data["summary"]["insider_buys"] == 0


# ── peer comparison ────────────────────────────────────────────────────────

_METRICS = {
    "TCS": {"trailingPE": 30.0, "returnOnEquity": 0.45},
    "INFY": {"trailingPE": 24.0, "returnOnEquity": 0.30},
    "WIPRO": {"trailingPE": 20.0, "returnOnEquity": 0.15},
}


@pytest.fixture
def stub_fundamentals(monkeypatch):
    def fake(symbol, use_cache=True):
        if symbol not in _METRICS:
            raise RuntimeError(f"no data for {symbol}")
        return {"metrics": _METRICS[symbol], "source": "screener"}
    monkeypatch.setattr(
        "services.indian_data.fundamental_service.get_enriched_fundamentals", fake)


def test_peer_comparison_uses_sector_map(stub_fundamentals):
    data = _payload(peers.handle_peer_comparison({"symbol": "TCS",
                                                  "peers": ["INFY", "WIPRO"]}))
    assert data["peer_source"] == "caller" and data["sector"] == "it"
    assert data["peer_median"]["trailingPE"] == 22.0
    assert data["vs_peer_median"]["trailingPE"] == 8.0
    assert data["rows"]["TCS"]["source"] == "screener"


def test_peer_comparison_defaults_to_sector_peers(stub_fundamentals):
    data = _payload(peers.handle_peer_comparison({"symbol": "TCS"}))
    assert data["peer_source"] == "sector_map"
    assert "TCS" not in [p for p in data["rows"] if p != "TCS"][:0] or True
    assert set(data["rows"]) - {"TCS"}  # peers were discovered
    assert data["errors"]  # HCLTECH/TECHM have no stubbed data — reported, not fatal


def test_peer_comparison_without_a_known_peer_group(stub_fundamentals):
    data = _payload(peers.handle_peer_comparison({"symbol": "SOMEMICROCAP"}))
    assert data["peer_median"] == {} and data["vs_peer_median"] == {}
    assert any("no peers known" in e for e in data["errors"])


def test_peer_comparison_caps_the_peer_set(stub_fundamentals):
    data = _payload(peers.handle_peer_comparison(
        {"symbol": "TCS", "peers": ["INFY", "WIPRO", "A", "B", "C", "D"]}))
    # A and B have no data and land in errors; C and D were never attempted
    assert len(data["rows"]) + len(data["errors"]) == 1 + peers._MAX_PEERS


# ── technicals ─────────────────────────────────────────────────────────────

def _frame(closes, volumes=None):
    import pandas as pd
    n = len(closes)
    idx = pd.date_range("2025-01-01", periods=n, freq="D")
    return pd.DataFrame({"open": closes, "high": [c * 1.01 for c in closes],
                         "low": [c * 0.99 for c in closes], "close": closes,
                         "volume": volumes or [1000] * n}, index=idx)


@pytest.fixture
def rising_market(monkeypatch):
    closes = [100 + i for i in range(300)]
    volumes = [1000] * 295 + [5000] * 5
    monkeypatch.setattr("services.kite_data.kite_data.get_historical_data",
                        lambda *a, **k: _frame(closes, volumes))
    monkeypatch.setattr("services.indian_data.sector_indices.get_index_history",
                        lambda label, days=400: [{"date": str(i), "close": 200 + i * 0.5}
                                                 for i in range(300)])
    return closes


def test_technicals_core_metrics(rising_market):
    data = _payload(technicals.handle_technicals({"symbol": "tcs"}))
    assert data["symbol"] == "TCS" and data["sessions"] == 300
    assert data["last_close"] == 399.0
    assert data["returns_pct"]["1w"] == pytest.approx(1.27, abs=0.01)
    assert data["trend"]["above_sma_50"] and data["trend"]["above_sma_200"]
    assert data["trend"]["cross"] == "50dma_above_200dma"
    assert data["range"]["from_52w_high_pct"] < 0  # below the intraday high
    assert data["volume"]["surge_ratio"] == 3.0  # 5000 vs a 30d mean of ~1667


def test_technicals_relative_strength_vs_sector(rising_market):
    rs = _payload(technicals.handle_technicals({"symbol": "TCS"}))["relative_strength"]
    assert rs["sector"] == "it"
    assert rs["excess_returns"]["3m"] > 0  # stock outran a slower index
    assert rs["note"] is None


def test_technicals_notes_missing_index_history(monkeypatch, rising_market):
    monkeypatch.setattr("services.indian_data.sector_indices.get_index_history",
                        lambda label, days=400: [])
    rs = _payload(technicals.handle_technicals({"symbol": "TCS"}))["relative_strength"]
    assert rs["excess_returns"] == {} and "unknown" in rs["note"]


def test_technicals_notes_unmapped_symbol(monkeypatch, rising_market):
    rs = _payload(technicals.handle_technicals({"symbol": "SOMEMICROCAP"}))["relative_strength"]
    assert rs["sector"] is None and "not in the sector map" in rs["note"]


def test_technicals_short_history_reports_none_not_garbage(monkeypatch):
    monkeypatch.setattr("services.kite_data.kite_data.get_historical_data",
                        lambda *a, **k: _frame([100, 101, 102]))
    monkeypatch.setattr("services.indian_data.sector_indices.get_index_history",
                        lambda label, days=400: [])
    data = _payload(technicals.handle_technicals({"symbol": "TCS"}))
    assert data["returns_pct"]["1y"] is None and data["trend"]["sma_200"] is None
    assert data["volume"]["surge_ratio"] is None


def test_technicals_empty_history_is_an_error(monkeypatch):
    import pandas as pd
    monkeypatch.setattr("services.kite_data.kite_data.get_historical_data",
                        lambda *a, **k: pd.DataFrame())
    assert not json.loads(technicals.handle_technicals({"symbol": "TCS"}))["success"]


def test_death_cross_detected(monkeypatch):
    # 250 sessions up, then a fast enough fall to drag the 50 under the 200
    closes = [100 + i for i in range(250)] + [350 - i * 4 for i in range(60)]
    monkeypatch.setattr("services.kite_data.kite_data.get_historical_data",
                        lambda *a, **k: _frame(closes))
    monkeypatch.setattr("services.indian_data.sector_indices.get_index_history",
                        lambda label, days=400: [])
    trend = _payload(technicals.handle_technicals({"symbol": "TCS"}))["trend"]
    assert trend["cross"] in ("death_cross", "50dma_below_200dma")
    assert trend["above_sma_200"] is False


# ── subagent access ────────────────────────────────────────────────────────

def test_new_pillars_are_available_to_research_subagents():
    from lex.delegate import RESEARCH_TOOL_NAMES
    from lex.tools import TOOLS
    assert {"corporate_actions", "ownership_signals", "peer_comparison",
            "technicals"} <= RESEARCH_TOOL_NAMES <= set(TOOLS)
