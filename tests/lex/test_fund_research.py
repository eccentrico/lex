import json

import pytest

from lex import fund_research

_SYNTH = json.dumps({
    "category": "Flexi Cap",
    "expense_ratio": "0.62% direct",
    "performance": "18% CAGR 3y vs 14% category average",
    "portfolio_composition": "top 10 holdings 45% of AUM, financials-heavy",
    "fund_manager": "Rajeev Thakkar, 12y tenure",
    "risk_exit_load": "1% exit load within 365 days",
    "verdict": {"stance": "bullish", "confidence": "medium", "drivers": ["manager tenure"],
                "what_would_change_my_mind": ["manager exit"], "summary": "solid flexi cap pick"},
})


@pytest.fixture
def passes(monkeypatch):
    """Record every subagent pass; return canned text per pass type."""
    seen = []

    def fake_pass(brief, pass_type="general", tools=None):
        seen.append({"pass_type": pass_type, "brief": brief, "tools": tools})
        return f"{pass_type.upper()} OUTPUT"

    monkeypatch.setattr("lex.delegate.run_pass", fake_pass)
    return seen


@pytest.fixture
def synth(monkeypatch):
    seen = {}

    def fake_run(client, model, messages, tools, **kw):
        seen["messages"], seen["tools"] = messages, tools
        return seen.get("reply", _SYNTH)

    monkeypatch.setattr("lex.agent.run", fake_run)
    monkeypatch.setattr("lex.llm.make_client", lambda: object())
    monkeypatch.setattr("lex.llm.default_model", lambda: "m")
    return seen


def test_full_depth_runs_three_passes_with_fund_tool_set(passes, synth):
    from lex import delegate
    report = fund_research.run_research("120503")
    assert [p["pass_type"] for p in passes] == ["fund_facts", "fund_narrative", "fund_bear"]
    assert all(p["tools"] == delegate.RESEARCH_FUND_TOOL_NAMES for p in passes)
    assert report["scheme_code"] == "120503" and report["depth"] == "full"
    assert report["passes"]["bear"] == "FUND_BEAR OUTPUT"


def test_brief_depth_skips_narrative(passes, synth):
    report = fund_research.run_research("120503", depth="brief")
    assert [p["pass_type"] for p in passes] == ["fund_facts", "fund_bear"]
    assert report["depth"] == "brief"


def test_later_passes_receive_earlier_output(passes, synth):
    fund_research.run_research("120503", brief="expense ratio")
    facts, narrative, bear = passes
    assert "FUND_FACTS OUTPUT" not in facts["brief"]
    assert "FUND_FACTS OUTPUT" in narrative["brief"]
    assert "FUND_FACTS OUTPUT" in bear["brief"] and "FUND_NARRATIVE OUTPUT" in bear["brief"]
    assert all("expense ratio" in p["brief"] for p in passes)


def test_synthesis_sees_all_passes_and_no_tools(passes, synth):
    fund_research.run_research("120503")
    body = synth["messages"][-1]["content"]
    assert "FACTS PASS" in body and "BEAR PASS" in body
    assert synth["tools"] == {}


def test_synthesis_uses_fund_synthesis_prompt(passes, synth):
    from lex import prompt
    fund_research.run_research("120503")
    assert synth["messages"][0]["content"] == prompt.FUND_SYNTHESIS_PROMPT


def test_sections_parsed_from_synthesis(passes, synth):
    sections = fund_research.run_research("120503")["sections"]
    assert set(fund_research.SECTIONS) <= set(sections)
    assert sections["verdict"]["stance"] == "bullish"
    assert sections["category"] == "Flexi Cap"


def test_missing_keys_become_unknown_not_invented(passes, synth):
    synth["reply"] = json.dumps({"category": "Flexi Cap"})
    sections = fund_research.run_research("120503")["sections"]
    assert sections["category"] == "Flexi Cap"
    assert sections["expense_ratio"].startswith("unknown")
    assert sections["verdict"] == {}


def test_unparseable_synthesis_keeps_raw_text(passes, synth):
    synth["reply"] = "I could not produce JSON, sorry."
    sections = fund_research.run_research("120503")["sections"]
    assert sections["_raw"] == "I could not produce JSON, sorry."
    assert sections["verdict"] == {}


def test_progress_reports_every_stage(passes, synth):
    stages = []
    fund_research.run_research("120503", progress=stages.append)
    assert stages == ["facts", "narrative", "bear", "synthesis"]


def test_run_and_save_delegates_to_fund_reports(passes, synth, monkeypatch):
    calls = {}
    monkeypatch.setattr("lex.fund_reports.delta_context", lambda code: ("", None))
    monkeypatch.setattr("lex.fund_reports.save", lambda report: calls.setdefault("saved", report) and __import__("pathlib").Path("/tmp/x.json"))
    report = fund_research.run_and_save("120503")
    assert report["saved_to"] == "/tmp/x.json"
    assert report["delta_of"] is None
    assert calls["saved"]["scheme_code"] == "120503"


def test_handler_envelopes_success_and_failure(passes, synth, monkeypatch):
    monkeypatch.setattr("lex.fund_reports.delta_context", lambda code: ("", None))
    monkeypatch.setattr("lex.fund_reports.save", lambda report: __import__("pathlib").Path("/tmp/x.json"))
    out = json.loads(fund_research.handle_fund_research({"scheme_code": "120503"}))
    assert out["success"] and out["data"]["sections"]["verdict"]["stance"] == "bullish"
    assert "passes" not in out["data"]

    monkeypatch.setattr("lex.delegate.run_pass",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no key")))
    assert not json.loads(fund_research.handle_fund_research({"scheme_code": "120503"}))["success"]
