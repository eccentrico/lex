"""Offline research quality harness.

Five archetypes a research agent has to survive, each driven end to end through
the real pipeline with a scripted LLM: no network, no Kite, no tools. What is
being checked is structure, not judgement — every pillar filled or declared
missing, claims tagged, a bear pass that engaged with the earlier passes, a
verdict that commits and can be falsified, and an update that reads as an
update.
"""
import json

import pytest

from lex import prompt, quality, reports, research


# ── the scripted analyst ───────────────────────────────────────────────────

def _sections(**over) -> dict:
    """A structurally complete report; archetypes override the parts that matter."""
    base = {
        "business": "Paints and coatings, 52% share [fundamentals].",
        "financials": "Revenue CAGR 12.4% over 5y, ROE 27.1% [fundamentals].",
        "filings_events": "Q1 FY27 filed 2026-07-12 [market_events NSE 2026-07-12].",
        "news": "Capacity expansion reported [web_search moneycontrol.com 2026-07-02].",
        "valuation": "58x versus a peer median of 41x [peer_comparison].",
        "technicals": "11% below the 52-week high, under the 200 DMA [technicals].",
        "ownership": "Promoter holding 52.6%, no pledge [ownership_signals].",
        "risks": [
            {"risk": "input cost inflation", "likelihood": "medium", "impact": "high"},
            {"risk": "share loss to Grasim", "likelihood": "high", "impact": "high"},
            {"risk": "multiple compression", "likelihood": "medium", "impact": "medium"},
        ],
        "catalysts": [{"catalyst": "Q2 print", "timing": "Oct 2026", "direction": "negative"}],
        "verdict": {"stance": "neutral", "confidence": "medium",
                    "drivers": ["share loss", "premium multiple"],
                    "what_would_change_my_mind": ["volume growth back above 8%"],
                    "summary": "Great business, priced for a share it is losing."},
    }
    base.update(over)
    return base


ARCHETYPES = [
    {
        "id": "compounder", "symbol": "ASIANPAINT",
        "facts": "Revenue CAGR 12.4%, ROE 27.1% [fundamentals].",
        "narrative": "Distribution moat; management guides to 8% volume growth.",
        "bear": "The 12.4% CAGR predates Grasim; 58x leaves no room [peer_comparison].",
        "sections": _sections(),
        # a quality compounder brief has to argue the multiple, not just admire it
        "requires": {"valuation": "peer", "technicals": "52-week"},
    },
    {
        "id": "cyclical", "symbol": "TATASTEEL",
        "facts": "EBITDA/t fell to Rs 9,100 [fundamentals]; net debt 2.1x [fundamentals].",
        "narrative": "Earnings track spreads, not execution; China exports set the price.",
        "bear": "At 2.1x leverage a spread trough of two quarters wipes the equity story.",
        "sections": _sections(
            financials="EBITDA/t Rs 9,100, down 31% YoY; net debt/EBITDA 2.1x [fundamentals].",
            valuation="6.2x EV/EBITDA against a cycle median of 5.8x [peer_comparison].",
            verdict={"stance": "bearish", "confidence": "low",
                     "drivers": ["spread compression"],
                     "what_would_change_my_mind": ["China export curbs"],
                     "summary": "Mid-cycle multiple on peak-cycle earnings."}),
        "requires": {"financials": "debt", "valuation": "cycle"},
    },
    {
        "id": "contested_governance", "symbol": "ZEEL",
        "facts": "Promoter holding 3.9%, 61% of it pledged [ownership_signals].",
        "narrative": "Management disputes the regulator's findings; auditors flagged advances.",
        "bear": "61% pledged against 3.9% held is the whole thesis — a margin call ends it.",
        "sections": _sections(
            ownership="Promoter holding 3.9%, 61% pledged; two insider sells [ownership_signals].",
            filings_events="SEBI order 2026-06-30 [market_events NSE 2026-06-30].",
            news="Regulator's findings disputed by management [web_fetch bseindia.com 2026-07-01].",
            verdict={"stance": "bearish", "confidence": "high",
                     "drivers": ["pledge overhang", "regulatory action"],
                     "what_would_change_my_mind": ["pledge released in full"],
                     "summary": "Governance risk dominates every operating number."}),
        "requires": {"ownership": "pledge", "filings_events": "SEBI"},
    },
    {
        "id": "recent_blowup", "symbol": "PAYTM",
        "facts": "Down 64% from the 52-week high on 3.1x average volume [technicals].",
        "narrative": "Regulatory action removed the lending engine, not a demand problem.",
        "bear": "A 64% drawdown is not a valuation floor when the revenue line is gone.",
        "sections": _sections(
            technicals="64% below the 52-week high, volume 3.1x the 30d average [technicals].",
            news="unknown — no filing yet confirms the reported wind-down.",
            verdict={"stance": "bearish", "confidence": "low",
                     "drivers": ["revenue line removed"],
                     "what_would_change_my_mind": ["licence restored"],
                     "summary": "Cheap against a past that no longer exists."}),
        # the honest "unknown" is the point of this archetype
        "requires": {"technicals": "52-week", "news": "unknown"},
    },
    {
        "id": "boring_psu", "symbol": "COALINDIA",
        "facts": "Dividend yield 6.8%, payout 52% [fundamentals]; 7.1x earnings [peer_comparison].",
        "narrative": "Volume growth is policy-set; the equity story is the payout.",
        "bear": "The 6.8% yield rests on a payout the majority owner can redirect at will.",
        "sections": _sections(
            financials="Dividend yield 6.8%, payout ratio 52% [fundamentals].",
            ownership="Government holds 63.1%; no pledge [ownership_signals].",
            valuation="7.1x earnings against a peer median of 11.4x [peer_comparison].",
            verdict={"stance": "bullish", "confidence": "medium",
                     "drivers": ["yield", "cheap on earnings"],
                     "what_would_change_my_mind": ["payout cut below 40%"],
                     "summary": "Paid to wait, as long as the owner keeps paying."}),
        "requires": {"financials": "payout", "ownership": "Government"},
    },
]

_BY_ID = {a["id"]: a for a in ARCHETYPES}


class _ScriptedAnalyst:
    """Answers by pass, recognised from the system prompt it was handed."""

    def __init__(self, archetype, synthesis=None, passes=None):
        self.a = archetype
        self.synthesis = synthesis
        self.passes = passes or {}
        self.chat = type("chat", (), {"completions": self})()
        self.seen = []

    def create(self, model=None, messages=None, tools=None, **kw):
        system = messages[0]["content"]
        for name, marker in (("facts", prompt.FACTS_PASS),
                             ("narrative", prompt.NARRATIVE_PASS),
                             ("bear", prompt.BEAR_PASS)):
            if marker[:60] in system:
                self.seen.append(name)
                return _reply(self.passes.get(name, self.a[name]))
        self.seen.append("synthesis")
        body = self.synthesis if self.synthesis is not None else \
            json.dumps(self.a["sections"])
        return _reply(body)


def _reply(content):
    from types import SimpleNamespace as NS
    return NS(choices=[NS(message=NS(content=content, tool_calls=None))])


@pytest.fixture
def run(monkeypatch):
    """Run the real pipeline against a scripted analyst; return the report."""
    def _run(archetype_id, symbol=None, **client_kw):
        archetype = _BY_ID[archetype_id]
        client = _ScriptedAnalyst(archetype, **client_kw)
        monkeypatch.setattr("lex.llm.make_client", lambda: client)
        monkeypatch.setattr("lex.llm.default_model", lambda: "scripted")
        report = research.run_and_save(symbol or archetype["symbol"])
        report["_client"] = client
        return report
    return _run


# ── the archetypes ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("archetype", ARCHETYPES, ids=lambda a: a["id"])
def test_archetype_produces_a_structurally_complete_report(archetype, run,
                                                           lex_home_tmp):
    report = run(archetype["id"])
    score = report["quality"]
    assert score["findings"] == []
    assert score["score"] == 1.0

    sections = report["sections"]
    for name, keyword in archetype["requires"].items():
        assert keyword.lower() in str(sections[name]).lower(), \
            f"{archetype['id']}: {name} never mentions {keyword}"

    assert report["_client"].seen == ["facts", "narrative", "bear", "synthesis"]
    assert reports.load(archetype["symbol"])["symbol"] == archetype["symbol"]


def test_every_archetype_is_covered():
    assert {a["id"] for a in ARCHETYPES} == {
        "compounder", "cyclical", "contested_governance", "recent_blowup",
        "boring_psu"}


# ── the scorer catches degradation ─────────────────────────────────────────

def _score(**over):
    return quality.score_report({"symbol": "X", "sections": _sections(**over)})


def test_missing_section_is_caught():
    findings = _score(technicals="")["findings"]
    assert any("technicals" in f and "absent" in f for f in findings)


def test_declared_unknown_is_accepted_but_invented_numbers_are_not():
    assert _score(news="unknown — no coverage found")["findings"] == []
    findings = _score(news="Revenue will grow 40% next year")["findings"]
    assert any("no source tag" in f for f in findings)


def test_untagged_report_is_caught():
    bare = {name: "prose with no attribution at all"
            for name in ("business", "financials", "filings_events", "news",
                         "valuation", "technicals", "ownership")}
    findings = _score(**bare)["findings"]
    assert any("source-tagged claims" in f for f in findings)


def test_unstructured_synthesis_is_caught():
    report = {"symbol": "X", "sections": dict(_sections(), _raw="model rambled")}
    assert any("structured" in f for f in quality.score_report(report)["findings"])


@pytest.mark.parametrize("verdict,expected", [
    ({}, "stance"),
    ({"stance": "bearish", "summary": "s", "drivers": ["d"],
      "what_would_change_my_mind": ["w"]}, "confidence"),
    ({"stance": "bearish", "confidence": "high", "summary": "s", "drivers": ["d"]},
     "unfalsifiable"),
])
def test_verdict_must_commit(verdict, expected):
    findings = _score(verdict=verdict)["findings"]
    assert any(expected in f for f in findings)


def test_risks_need_grades_and_volume():
    thin = [{"risk": "one", "likelihood": "high", "impact": "high"}]
    assert any("at least 3" in f for f in _score(risks=thin)["findings"])
    ungraded = [{"risk": n} for n in ("a", "b", "c")]
    assert any("likelihood/impact" in f for f in _score(risks=ungraded)["findings"])


def test_catalysts_need_timing():
    assert any("no catalysts" in f for f in _score(catalysts=[])["findings"])
    untimed = [{"catalyst": "results", "direction": "positive"}]
    assert any("no timing" in f for f in _score(catalysts=untimed)["findings"])


def test_generic_bear_pass_is_caught(run, lex_home_tmp):
    report = run("compounder", passes={
        "bear": "Equities can fall. Competition is a risk. Markets are volatile."})
    assert any("boilerplate" in f for f in report["quality"]["findings"])


def test_engaged_bear_pass_passes(run, lex_home_tmp):
    assert run("compounder")["quality"]["findings"] == []


def test_scorer_survives_a_malformed_report():
    score = quality.score_report({"sections": {"risks": "not a list",
                                               "verdict": "not a dict"}})
    assert score["score"] < 1.0 and score["findings"]


# ── regression: a second look must be a delta ──────────────────────────────

def test_second_run_is_an_update_not_a_rewrite(run, lex_home_tmp):
    first = run("cyclical")
    assert first["delta_of"] is None

    update = json.dumps(dict(_BY_ID["cyclical"]["sections"], verdict={
        "stance": "bearish", "confidence": "medium",
        "drivers": ["spreads recovered since the last report"],
        "what_would_change_my_mind": ["a second quarter of spread recovery"],
        "summary": "Changed since July: spreads bottomed, so the verdict softens."}))
    second = run("cyclical", synthesis=update)

    assert second["delta_of"] == first["generated_at"]
    assert second["quality"]["findings"] == []
    assert len(reports.history("TATASTEEL")) == 2
    # the prior verdict has to actually reach the new run's passes
    assert "bearish" in reports.delta_context("TATASTEEL")[0]


def test_update_that_ignores_the_past_is_caught(run, lex_home_tmp):
    run("boring_psu")
    second = run("boring_psu")
    assert second["delta_of"] is not None
    assert any("never says what changed" in f
               for f in second["quality"]["findings"])
