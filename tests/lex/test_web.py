import json

import pytest

from lex.tools import web


class FakeDDGS:
    """Records the kwargs it was called with; returns whatever it was seeded."""
    last_kwargs = None

    def __init__(self, results=None):
        self.results = results if results is not None else [
            {"title": "t", "href": "http://x", "body": "b"}] * 10

    def text(self, q, max_results=5, timelimit=None):
        FakeDDGS.last_kwargs = {"q": q, "max_results": max_results,
                                "timelimit": timelimit}
        return self.results[:max_results]


def _search(monkeypatch, results=None, **args):
    monkeypatch.setattr(web, "_ddgs", lambda: FakeDDGS(results))
    out = json.loads(web.handle_web_search({"query": "nifty", **args}))
    assert out["success"], out
    return out["data"]


def test_web_search_respects_limit(monkeypatch):
    assert len(_search(monkeypatch, limit=3)) == 3


@pytest.mark.parametrize("url,tier", [
    ("https://www.nseindia.com/companies/x", "primary"),
    ("https://sebi.gov.in/order.html", "primary"),
    ("https://www.moneycontrol.com/news/x.html", "press"),
    ("https://economictimes.indiatimes.com/markets/x", "press"),
    ("https://old.reddit.com/r/IndiaInvestments/x", "aggregator"),
    ("https://some-random-blog.in/post", "other"),
    ("not a url at all", "other"),
])
def test_source_classification(url, tier):
    assert web.classify_source(url)[0] == tier


def test_results_ranked_primary_first_and_annotated(monkeypatch):
    results = [
        {"title": "forum take", "href": "https://reddit.com/r/x"},
        {"title": "press", "href": "https://www.moneycontrol.com/news/y"},
        {"title": "filing", "href": "https://nseindia.com/z"},
        {"title": "blog", "href": "https://randomsite.co/p"},
    ]
    ranked = _search(monkeypatch, results, limit=4)
    assert [r["source_tier"] for r in ranked] == ["primary", "press", "other", "aggregator"]
    assert ranked[0]["domain"] == "nseindia.com"
    assert ranked[0]["source_rank"] < ranked[-1]["source_rank"]


def test_ranking_is_stable_within_a_tier(monkeypatch):
    results = [{"title": "first", "href": "https://livemint.com/a"},
               {"title": "second", "href": "https://moneycontrol.com/b"}]
    assert [r["title"] for r in _search(monkeypatch, results)] == ["first", "second"]


def test_date_hint_from_field_url_then_body(monkeypatch):
    results = [
        {"href": "https://nseindia.com/a", "date": "2026-07-01"},
        {"href": "https://moneycontrol.com/news/2026/07/11/story.html"},
        {"href": "https://moneycontrol.com/x", "body": "Published 3 Jul 2026 by staff"},
        {"href": "https://moneycontrol.com/y", "body": "no date anywhere"},
    ]
    hints = [r["date_hint"] for r in _search(monkeypatch, results, limit=4)]
    assert hints[0] == "2026-07-01"
    assert "2026-07-11" in hints and "3 Jul 2026" in hints
    assert None in hints


@pytest.mark.parametrize("days,bucket", [
    (1, "d"), (5, "w"), (30, "m"), (200, "y"), (5000, None), (None, None),
    ("garbage", None),
])
def test_recency_window_maps_to_a_ddgs_bucket(monkeypatch, days, bucket):
    _search(monkeypatch, days=days)
    assert FakeDDGS.last_kwargs["timelimit"] == bucket


def test_search_survives_a_ddgs_without_timelimit(monkeypatch):
    class OldDDGS:
        def text(self, q, max_results=5):
            return [{"title": "t", "href": "https://nseindia.com/a"}]
    monkeypatch.setattr(web, "_ddgs", lambda: OldDDGS())
    out = json.loads(web.handle_web_search({"query": "x", "days": 7}))
    assert out["success"] and out["data"][0]["source_tier"] == "primary"


def test_search_error_is_enveloped(monkeypatch):
    monkeypatch.setattr(web, "_ddgs", lambda: (_ for _ in ()).throw(OSError("offline")))
    assert not json.loads(web.handle_web_search({"query": "x"}))["success"]


def _fetch(monkeypatch, html, url="https://www.moneycontrol.com/news/x"):
    class R:
        text = html
        def raise_for_status(self): pass
    monkeypatch.setattr(web.requests, "get", lambda *a, **k: R())
    out = json.loads(web.handle_web_fetch({"url": url}))
    assert out["success"], out
    return out["data"]


def test_web_fetch_strips_html(monkeypatch):
    data = _fetch(monkeypatch,
                  "<html><script>x=1</script><body><h1>Hi</h1>"
                  "<p>News &amp; views</p></body></html>")
    assert "Hi" in data["text"] and "News & views" in data["text"]
    assert "script" not in data["text"] and "x=1" not in data["text"]


def test_web_fetch_reports_source_and_published_date(monkeypatch):
    data = _fetch(monkeypatch,
                  '<html><head><meta property="article:published_time" '
                  'content="2026-07-11T08:00:00Z"></head><body>Story</body></html>')
    assert data["published"] == "2026-07-11T08:00:00Z"
    assert data["source_tier"] == "press" and data["domain"] == "moneycontrol.com"
    assert data["truncated"] is False


def test_web_fetch_falls_back_to_a_time_element(monkeypatch):
    data = _fetch(monkeypatch,
                  '<html><body><time datetime="2026-06-02">June 2</time>Story</body></html>')
    assert data["published"] == "2026-06-02"


def test_web_fetch_flags_truncation(monkeypatch):
    data = _fetch(monkeypatch, "<html><body>" + ("word " * 5000) + "</body></html>")
    assert data["truncated"] is True and len(data["text"]) == web._MAX_CHARS


def test_web_fetch_error(monkeypatch):
    def boom(*a, **k):
        raise OSError("no network")
    monkeypatch.setattr(web.requests, "get", boom)
    assert not json.loads(web.handle_web_fetch({"url": "http://x"}))["success"]
