from services.indian_data import mutual_funds

_SAMPLE = (
    "120503;Parag Parikh Flexi Cap Fund - Direct Growth;INF879O01019;INF879O01027;74.5000;74.3000;74.6000;01-Aug-2026\n"
    "120503;Parag Parikh Flexi Cap Fund - Direct Growth;INF879O01019;INF879O01027;75.1000;74.9000;75.2000;02-Aug-2026\n"
    "999999;Some Other Fund;XXXXXXXXXXX;XXXXXXXXXXX;10.0000;9.8000;10.2000;01-Aug-2026\n"
)


def test_parse_nav_history_filters_by_scheme_code():
    rows = mutual_funds._parse_nav_history(_SAMPLE, "120503")
    assert rows == [{"date": "2026-08-01", "nav": 74.5}, {"date": "2026-08-02", "nav": 75.1}]


def test_parse_nav_history_ignores_other_schemes():
    rows = mutual_funds._parse_nav_history(_SAMPLE, "999999")
    assert rows == [{"date": "2026-08-01", "nav": 10.0}]


def test_parse_nav_history_no_match_is_empty():
    assert mutual_funds._parse_nav_history(_SAMPLE, "000000") == []


def test_get_nav_history_fetches_and_caches(monkeypatch):
    calls = []

    class FakeResp:
        text = _SAMPLE

        def raise_for_status(self):
            pass

    def fake_get(url, params=None, timeout=None):
        calls.append(params)
        return FakeResp()

    monkeypatch.setattr("requests.get", fake_get)
    rows = mutual_funds.get_nav_history("120503", "2026-08-01", "2026-08-02")
    assert rows == [{"date": "2026-08-01", "nav": 74.5}, {"date": "2026-08-02", "nav": 75.1}]
    assert len(calls) == 1

    # same range again is served from the on-disk cache, no second fetch
    mutual_funds.get_nav_history("120503", "2026-08-01", "2026-08-02")
    assert len(calls) == 1


def test_get_nav_history_falls_back_to_cache_on_fetch_failure(monkeypatch):
    def fail_get(*a, **k):
        raise RuntimeError("AMFI down")

    monkeypatch.setattr("requests.get", fail_get)
    assert mutual_funds.get_nav_history("120503", "2026-08-01", "2026-08-02") == []
