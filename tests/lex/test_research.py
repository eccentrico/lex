import json

import pytest

from lex import research

_SYNTH = json.dumps({
    "business": "IT services",
    "financials": "revenue +8%",
    "filings_events": "Q1 filed",
    "news": "nothing material",
    "valuation": "22x vs 24x history",
    "technicals": "above 200 DMA",
    "ownership": "promoter stake flat",
    "risks": [{"risk": "client concentration", "likelihood": "medium", "impact": "high"}],
    "catalysts": [{"catalyst": "Q2 print", "timing": "Oct", "direction": "positive"}],
    "verdict": {"stance": "neutral", "confidence": "medium", "drivers": ["margin"],
                "what_would_change_my_mind": ["deal wins"], "summary": "fairly priced"},
})


@pytest.fixture
def passes(monkeypatch):
    """Record every subagent pass; return canned text per pass type."""
    seen = []

    def fake_pass(brief, pass_type="general"):
        seen.append({"pass_type": pass_type, "brief": brief})
        return f"{pass_type.upper()} OUTPUT"

    monkeypatch.setattr("lex.delegate.run_pass", fake_pass)
    return seen


@pytest.fixture
def synth(monkeypatch):
    """Stub the parent synthesis call; return the recorded messages list."""
    seen = {}

    def fake_run(client, model, messages, tools, **kw):
        seen["messages"], seen["tools"] = messages, tools
        return seen.get("reply", _SYNTH)

    monkeypatch.setattr("lex.agent.run", fake_run)
    monkeypatch.setattr("lex.llm.make_client", lambda: object())
    monkeypatch.setattr("lex.llm.default_model", lambda: "m")
    return seen


def test_full_depth_runs_three_passes_in_order(passes, synth):
    report = research.run_research("tcs")
    assert [p["pass_type"] for p in passes] == ["facts", "narrative", "bear"]
    assert report["symbol"] == "TCS" and report["depth"] == "full"
    assert report["passes"]["bear"] == "BEAR OUTPUT"


def test_brief_depth_skips_narrative(passes, synth):
    report = research.run_research("INFY", depth="brief")
    assert [p["pass_type"] for p in passes] == ["facts", "bear"]
    assert report["depth"] == "brief"


def test_unknown_depth_falls_back_to_full(passes, synth):
    assert research.run_research("INFY", depth="sideways")["depth"] == "full"


def test_later_passes_receive_earlier_output(passes, synth):
    research.run_research("TCS", brief="margins")
    facts, narrative, bear = passes
    assert "FACTS OUTPUT" not in facts["brief"]
    assert "FACTS OUTPUT" in narrative["brief"]
    assert "FACTS OUTPUT" in bear["brief"] and "NARRATIVE OUTPUT" in bear["brief"]
    assert all("margins" in p["brief"] for p in passes)


def test_synthesis_sees_all_passes_and_no_tools(passes, synth):
    research.run_research("TCS")
    body = synth["messages"][-1]["content"]
    assert "FACTS PASS" in body and "BEAR PASS" in body
    assert synth["tools"] == {}


def test_context_reaches_passes_and_synthesis(passes, synth):
    research.run_research("TCS", context="Prior verdict: bearish (2026-07-01).")
    assert all("Prior verdict" in p["brief"] for p in passes)
    assert "Prior verdict" in synth["messages"][-1]["content"]


def test_sections_parsed_from_synthesis(passes, synth):
    sections = research.run_research("TCS")["sections"]
    assert set(research.SECTIONS) <= set(sections)
    assert sections["verdict"]["stance"] == "neutral"
    assert sections["risks"][0]["risk"] == "client concentration"


def test_fenced_json_is_parsed(passes, synth):
    synth["reply"] = f"Here you go:\n```json\n{_SYNTH}\n```"
    assert research.run_research("TCS")["sections"]["business"] == "IT services"


def test_missing_keys_become_unknown_not_invented(passes, synth):
    synth["reply"] = json.dumps({"business": "IT services"})
    sections = research.run_research("TCS")["sections"]
    assert sections["business"] == "IT services"
    assert sections["financials"].startswith("unknown")
    assert sections["risks"] == [] and sections["verdict"] == {}


def test_unparseable_synthesis_keeps_raw_text(passes, synth):
    synth["reply"] = "I could not produce JSON, sorry."
    sections = research.run_research("TCS")["sections"]
    assert sections["_raw"] == "I could not produce JSON, sorry."
    assert sections["verdict"] == {}


def test_progress_reports_every_stage(passes, synth):
    stages = []
    research.run_research("TCS", progress=stages.append)
    assert stages == ["facts", "narrative", "bear", "synthesis"]


def test_broken_progress_callback_does_not_kill_the_run(passes, synth):
    def boom(_stage):
        raise RuntimeError("terminal exploded")
    assert research.run_research("TCS", progress=boom)["symbol"] == "TCS"


def test_handler_envelopes_success_and_failure(passes, synth, monkeypatch):
    out = json.loads(research.handle_deep_research({"symbol": "TCS"}))
    assert out["success"] and out["data"]["sections"]["verdict"]["stance"] == "neutral"

    monkeypatch.setattr("lex.delegate.run_pass",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no key")))
    assert not json.loads(research.handle_deep_research({"symbol": "TCS"}))["success"]
