import json
import time

from lex.tools import mf_watchlist


def _add(code):
    return json.loads(mf_watchlist.handle_mf_watchlist_add({"scheme_code": code, "note": "n"}))


def test_add_remove_roundtrip(lex_home_tmp):
    assert _add("120503")["success"]
    assert not _add("120503")["success"]  # duplicate refused
    assert json.loads(mf_watchlist.handle_mf_watchlist_remove({"scheme_code": "120503"}))["success"]
    assert not json.loads(mf_watchlist.handle_mf_watchlist_remove({"scheme_code": "120503"}))["success"]


def test_status_reports_move_and_updates_baseline(lex_home_tmp, monkeypatch):
    _add("120503")
    items = mf_watchlist._load()
    items[0].update({"last_nav": 70.0, "last_checked_ts": time.time() - 86400})
    mf_watchlist._save(items)
    monkeypatch.setattr("services.kite_data.kite_data.get_mf_quote",
                        lambda codes: {"120503": {"nav": 74.2}})
    data = json.loads(mf_watchlist.handle_mf_watchlist_status({}))["data"]
    entry = data["entries"][0]
    assert entry["scheme_code"] == "120503"
    assert entry["change_pct"] == 6.0
    assert entry["days_since_check"] == 1
    assert mf_watchlist._load()[0]["last_nav"] == 74.2  # baseline advanced


def test_status_degrades_when_the_quote_lookup_misses(lex_home_tmp, monkeypatch):
    """A scheme_code absent from get_mf_quote's dict must not crash or fake a NAV.

    Distinguishes "no quote came back for this code" from a real NAV of 0 —
    the entry reports nav=None and no change, and the baseline is left alone
    rather than being overwritten with nothing.
    """
    _add("120503")
    items = mf_watchlist._load()
    items[0].update({"last_nav": 70.0, "last_checked_ts": time.time() - 86400})
    mf_watchlist._save(items)
    monkeypatch.setattr("services.kite_data.kite_data.get_mf_quote",
                        lambda codes: {})  # requested code simply absent
    out = json.loads(mf_watchlist.handle_mf_watchlist_status({}))
    assert out["success"]
    entry = out["data"]["entries"][0]
    assert entry["scheme_code"] == "120503"
    assert entry["nav"] is None
    assert entry["change_pct"] is None
    assert entry["days_since_check"] == 1
    assert mf_watchlist._load()[0]["last_nav"] == 70.0  # baseline not clobbered


def test_status_empty(lex_home_tmp):
    data = json.loads(mf_watchlist.handle_mf_watchlist_status({}))["data"]
    assert data["entries"] == []


def test_tools_dict_registers_mf_watchlist():
    from lex.tools import TOOLS
    for name in ("mf_watchlist_add", "mf_watchlist_remove", "mf_watchlist_status"):
        assert TOOLS[name]["schema"]["name"] == name
