from datetime import datetime

import pytest

from services.indian_data import mutual_funds

_SAMPLE = (
    "120503;Parag Parikh Flexi Cap Fund - Direct Growth;INF879O01019;INF879O01027;74.5000;74.3000;74.6000;01-Aug-2026\n"
    "120503;Parag Parikh Flexi Cap Fund - Direct Growth;INF879O01019;INF879O01027;75.1000;74.9000;75.2000;02-Aug-2026\n"
    "999999;Some Other Fund;XXXXXXXXXXX;XXXXXXXXXXX;10.0000;9.8000;10.2000;01-Aug-2026\n"
)

# July 2026: the 1st is a Wednesday, so the 4th/5th are Sat/Sun. AMFI publishes
# no NAV for those, which is exactly the gap that used to defeat the cache.
_JULY_NAVS = {"2026-07-01": 74.0, "2026-07-02": 74.5, "2026-07-03": 75.0,
              "2026-07-06": 75.5}


def _amfi_line(code: str, iso_date: str, nav: float) -> str:
    d = datetime.strptime(iso_date, "%Y-%m-%d").strftime("%d-%b-%Y")
    return f"{code};A Fund;INF000000001;INF000000002;{nav:.4f};{nav:.4f};{nav:.4f};{d}"


class _Resp:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class FakeAmfi:
    """Stands in for requests.get against AMFI's whole-market history report.

    Serves only the dates it actually has a NAV for, the way AMFI serves only
    trading days, and records every window it was asked for.
    """

    def __init__(self, navs=None, code="120503", fail_windows=()):
        self.navs = dict(navs or {})
        self.code = code
        self.fail_windows = set(fail_windows)
        self.calls = []

    def __call__(self, url, params=None, timeout=None):
        self.calls.append((params["frmdt"], params["todt"]))
        if params["frmdt"] in self.fail_windows:
            raise RuntimeError("AMFI down")
        lo = datetime.strptime(params["frmdt"], "%d-%b-%Y").strftime("%Y-%m-%d")
        hi = datetime.strptime(params["todt"], "%d-%b-%Y").strftime("%Y-%m-%d")
        return _Resp("\n".join(_amfi_line(self.code, d, n)
                               for d, n in sorted(self.navs.items()) if lo <= d <= hi))


def _install(monkeypatch, fake):
    monkeypatch.setattr("requests.get", fake)
    return fake


def test_parse_nav_history_filters_by_scheme_code():
    rows = mutual_funds._parse_nav_history(_SAMPLE, "120503")
    assert rows == [{"date": "2026-08-01", "nav": 74.5}, {"date": "2026-08-02", "nav": 75.1}]


def test_parse_nav_history_ignores_other_schemes():
    rows = mutual_funds._parse_nav_history(_SAMPLE, "999999")
    assert rows == [{"date": "2026-08-01", "nav": 10.0}]


def test_parse_nav_history_no_match_is_empty():
    assert mutual_funds._parse_nav_history(_SAMPLE, "000000") == []


def test_get_nav_history_fetches_and_caches(monkeypatch):
    # Wed-Fri, every day a real trading day with a NAV row.
    fake = _install(monkeypatch, FakeAmfi(_JULY_NAVS))
    rows = mutual_funds.get_nav_history("120503", "2026-07-01", "2026-07-03")
    assert rows == [{"date": "2026-07-01", "nav": 74.0},
                    {"date": "2026-07-02", "nav": 74.5},
                    {"date": "2026-07-03", "nav": 75.0}]
    assert len(fake.calls) == 1

    # same range again is served from the on-disk cache, no second fetch
    assert mutual_funds.get_nav_history("120503", "2026-07-01", "2026-07-03") == rows
    assert len(fake.calls) == 1


def test_get_nav_history_cache_hits_across_a_weekend_gap(monkeypatch):
    # 04/05 July are Sat/Sun and have no NAV, so not every calendar day in the
    # range has a row — the fetched range is still fully covered.
    fake = _install(monkeypatch, FakeAmfi(_JULY_NAVS))
    rows = mutual_funds.get_nav_history("120503", "2026-07-01", "2026-07-06")
    assert [r["date"] for r in rows] == ["2026-07-01", "2026-07-02", "2026-07-03", "2026-07-06"]
    assert len(fake.calls) == 1

    assert mutual_funds.get_nav_history("120503", "2026-07-01", "2026-07-06") == rows
    assert len(fake.calls) == 1


def test_get_nav_history_narrower_range_hits_the_cache(monkeypatch):
    fake = _install(monkeypatch, FakeAmfi(_JULY_NAVS))
    mutual_funds.get_nav_history("120503", "2026-07-01", "2026-07-06")
    assert len(fake.calls) == 1

    inner = mutual_funds.get_nav_history("120503", "2026-07-02", "2026-07-03")
    assert [r["date"] for r in inner] == ["2026-07-02", "2026-07-03"]
    assert len(fake.calls) == 1


def test_get_nav_history_no_data_scheme_does_not_refetch_forever(monkeypatch):
    # A genuinely empty result for a fetched range is still a valid cache hit.
    fake = _install(monkeypatch, FakeAmfi(_JULY_NAVS, code="120503"))
    assert mutual_funds.get_nav_history("555555", "2026-07-01", "2026-07-06") == []
    assert len(fake.calls) == 1

    assert mutual_funds.get_nav_history("555555", "2026-07-01", "2026-07-06") == []
    assert len(fake.calls) == 1


def test_get_nav_history_chunks_a_long_range(monkeypatch):
    fake = _install(monkeypatch, FakeAmfi({"2026-01-05": 60.0, "2026-02-10": 62.0}))
    rows = mutual_funds.get_nav_history("120503", "2026-01-01", "2026-03-01")
    assert [r["date"] for r in rows] == ["2026-01-05", "2026-02-10"]
    # 60 calendar days -> two 30-day windows, contiguous and non-overlapping
    assert fake.calls == [("01-Jan-2026", "30-Jan-2026"), ("31-Jan-2026", "01-Mar-2026")]

    assert mutual_funds.get_nav_history("120503", "2026-01-01", "2026-03-01") == rows
    assert len(fake.calls) == 2  # adjacent windows merged into one coverage interval


def test_get_nav_history_keeps_good_chunks_when_one_fails(monkeypatch):
    fake = _install(monkeypatch, FakeAmfi({"2026-01-05": 60.0, "2026-02-10": 62.0},
                                          fail_windows={"01-Jan-2026"}))
    rows = mutual_funds.get_nav_history("120503", "2026-01-01", "2026-03-01")
    assert [r["date"] for r in rows] == ["2026-02-10"]  # second window survived

    # the failed window was NOT recorded as covered, so it is retried
    fake.fail_windows.clear()
    rows = mutual_funds.get_nav_history("120503", "2026-01-01", "2026-03-01")
    assert [r["date"] for r in rows] == ["2026-01-05", "2026-02-10"]
    assert fake.calls[2] == ("01-Jan-2026", "30-Jan-2026")
    assert len(fake.calls) == 3  # only the missing window refetched


def test_get_nav_history_persists_each_window_as_it_lands(monkeypatch):
    """An interrupted long fetch must not throw away the windows that landed.

    A multi-year range is many minutes of fetching; if the caller times out or
    interrupts mid-loop, saving only at the end would lose every completed
    window and make the retry start from zero.
    """
    class Interrupted(FakeAmfi):
        interrupt_on = "31-Jan-2026"

        def __call__(self, url, params=None, timeout=None):
            if params["frmdt"] == self.interrupt_on:
                self.interrupt_on = None  # interrupt once, then behave
                raise KeyboardInterrupt  # escapes the per-window except Exception
            return super().__call__(url, params=params, timeout=timeout)

    fake = _install(monkeypatch, Interrupted({"2026-01-05": 60.0, "2026-02-10": 62.0}))
    with pytest.raises(KeyboardInterrupt):
        mutual_funds.get_nav_history("120503", "2026-01-01", "2026-03-01")

    # the first window survived on disk, so the retry only fetches the rest
    fake.calls.clear()
    rows = mutual_funds.get_nav_history("120503", "2026-01-01", "2026-03-01")
    assert [r["date"] for r in rows] == ["2026-01-05", "2026-02-10"]
    assert fake.calls == [("31-Jan-2026", "01-Mar-2026")]


def test_get_nav_history_raises_when_fetch_fails_and_nothing_cached(monkeypatch):
    def fail_get(*a, **k):
        raise RuntimeError("AMFI down")

    monkeypatch.setattr("requests.get", fail_get)
    with pytest.raises(RuntimeError, match="unavailable"):
        mutual_funds.get_nav_history("120503", "2026-07-01", "2026-07-06")


def test_get_nav_history_serves_cache_when_a_later_fetch_fails(monkeypatch):
    fake = _install(monkeypatch, FakeAmfi(_JULY_NAVS))
    mutual_funds.get_nav_history("120503", "2026-07-01", "2026-07-06")

    # a slightly wider range is not covered, so it refetches — and AMFI is now down
    fake.fail_windows.add("01-Jul-2026")
    rows = mutual_funds.get_nav_history("120503", "2026-07-01", "2026-07-08")
    assert [r["date"] for r in rows] == ["2026-07-01", "2026-07-02", "2026-07-03", "2026-07-06"]


def test_get_nav_history_rejects_an_oversized_range(monkeypatch):
    def explode(*a, **k):
        raise AssertionError("must not reach the network")

    monkeypatch.setattr("requests.get", explode)
    with pytest.raises(ValueError, match="narrower range"):
        mutual_funds.get_nav_history("120503", "2021-08-01", "2026-08-01")


def test_get_nav_history_rejects_a_backwards_range(monkeypatch):
    with pytest.raises(ValueError, match="after"):
        mutual_funds.get_nav_history("120503", "2026-07-06", "2026-07-01")


def test_merge_ranges_joins_overlapping_and_adjacent():
    assert mutual_funds._merge_ranges(
        [["2026-01-01", "2026-01-30"], ["2026-01-31", "2026-03-01"]]
    ) == [["2026-01-01", "2026-03-01"]]
    assert mutual_funds._merge_ranges(
        [["2026-01-10", "2026-01-20"], ["2026-01-15", "2026-01-25"]]
    ) == [["2026-01-10", "2026-01-25"]]
    # a real gap stays a gap
    assert mutual_funds._merge_ranges(
        [["2026-01-01", "2026-01-10"], ["2026-02-01", "2026-02-10"]]
    ) == [["2026-01-01", "2026-01-10"], ["2026-02-01", "2026-02-10"]]


def test_covered_needs_the_whole_range():
    ranges = [["2026-01-01", "2026-01-10"]]
    assert mutual_funds._covered(ranges, "2026-01-02", "2026-01-09")
    assert mutual_funds._covered(ranges, "2026-01-01", "2026-01-10")
    assert not mutual_funds._covered(ranges, "2026-01-01", "2026-01-11")
    assert not mutual_funds._covered([], "2026-01-01", "2026-01-02")


def test_get_nav_history_migrates_the_old_bare_list_cache(monkeypatch):
    import json

    from services.paths import lex_home

    lex_home().mkdir(parents=True, exist_ok=True)
    (lex_home() / "mf_nav_cache.json").write_text(
        json.dumps({"120503": [{"date": "2026-07-01", "nav": 74.0}]}), encoding="utf-8")

    # the old format recorded no coverage, so this refetches rather than
    # serving a range it cannot prove it fetched
    fake = _install(monkeypatch, FakeAmfi(_JULY_NAVS))
    rows = mutual_funds.get_nav_history("120503", "2026-07-01", "2026-07-06")
    assert len(fake.calls) == 1
    assert [r["date"] for r in rows] == ["2026-07-01", "2026-07-02", "2026-07-03", "2026-07-06"]
