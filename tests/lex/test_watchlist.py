import json, time
from lex.tools import watchlist


def _add(sym):
    return json.loads(watchlist.handle_watchlist_add({"symbol": sym, "note": "n"}))


def test_add_remove_roundtrip(lex_home_tmp):
    assert _add("INFY")["success"]
    assert not _add("INFY")["success"]  # duplicate refused
    assert json.loads(watchlist.handle_watchlist_remove({"symbol": "INFY"}))["success"]
    assert not json.loads(watchlist.handle_watchlist_remove({"symbol": "INFY"}))["success"]


def test_status_reports_move_and_updates_baseline(lex_home_tmp, monkeypatch):
    _add("INFY")
    items = watchlist._load()
    items[0].update({"last_price": 1000.0, "last_checked_ts": time.time() - 86400})
    watchlist._save(items)
    monkeypatch.setattr("services.kite_data.kite_data.get_quotes",
                        lambda syms: {"INFY": {"last_price": 1060.0}})
    monkeypatch.setattr("lex.tools.market._fetch_nse_announcements",
                        lambda s, d: [{"subject": "Board meeting"}])
    data = json.loads(watchlist.handle_watchlist_status({}))["data"]
    entry = data["entries"][0]
    assert entry["symbol"] == "INFY"
    assert entry["change_pct"] == 6.0
    assert entry["announcements"] == [{"subject": "Board meeting"}]
    assert watchlist._load()[0]["last_price"] == 1060.0  # baseline advanced


def test_status_empty(lex_home_tmp):
    data = json.loads(watchlist.handle_watchlist_status({}))["data"]
    assert data["entries"] == []
