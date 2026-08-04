import json

import pytest

from lex import fund_reports, fund_research

_VERDICT = {"stance": "bullish", "confidence": "medium", "drivers": ["manager tenure"],
            "what_would_change_my_mind": ["manager exit"],
            "summary": "Solid flexi cap pick."}


def _report(scheme_code="120503", **over):
    report = {
        "scheme_code": scheme_code, "brief": "", "depth": "full",
        "generated_at": "2026-07-20T09:00:00+00:00",
        "sections": {
            "category": "Flexi Cap", "expense_ratio": "0.62% direct",
            "performance": "18% CAGR 3y", "portfolio_composition": "top 10 = 45% AUM",
            "fund_manager": "Rajeev Thakkar, 12y", "risk_exit_load": "1% exit load <365d",
            "verdict": dict(_VERDICT),
        },
        "passes": {"facts": "F", "narrative": "N", "bear": "B"},
    }
    report.update(over)
    return report


def test_save_writes_json_and_markdown(lex_home_tmp):
    path = fund_reports.save(_report())
    assert path.parent == lex_home_tmp / "mf_research" / "120503"
    assert json.loads(path.read_text(encoding="utf-8"))["scheme_code"] == "120503"
    md = path.with_suffix(".md").read_text(encoding="utf-8")
    assert "bullish" in md and "Solid flexi cap pick." in md


def test_load_is_newest_first_and_bounds_checked(lex_home_tmp):
    fund_reports.save(_report(generated_at="first"))
    fund_reports.save(_report(generated_at="second"))
    assert fund_reports.load("120503")["generated_at"] == "second"
    assert fund_reports.load("120503", 2)["generated_at"] == "first"
    assert fund_reports.load("120503", 3) is None
    assert fund_reports.load("NOSUCH") is None


def test_history_digest(lex_home_tmp):
    fund_reports.save(_report())
    fund_reports.save(_report())
    hist = fund_reports.history("120503")
    assert len(hist) == 2
    assert hist[0]["stance"] == "bullish" and hist[0]["confidence"] == "medium"
    assert hist[0]["summary"] == "Solid flexi cap pick."
    assert fund_reports.history("NOSUCH") == []


def test_delta_context_empty_on_first_look(lex_home_tmp):
    assert fund_reports.delta_context("120503") == ("", None)


def test_delta_context_carries_prior_verdict(lex_home_tmp):
    fund_reports.save(_report())
    context, prior_at = fund_reports.delta_context("120503")
    assert prior_at == "2026-07-20T09:00:00+00:00"
    assert "bullish" in context and "Solid flexi cap pick." in context
    assert "manager exit" in context
    assert "UPDATE" in context and "changed" in context


def test_render_brief_is_short_and_leads_with_the_answer(lex_home_tmp):
    md = fund_reports.render(_report(), mode="brief")
    assert md.startswith("# 120503 — bullish (confidence: medium)")
    assert "Solid flexi cap pick." in md
    assert "## Category" not in md


def test_render_full_has_every_section(lex_home_tmp):
    md = fund_reports.render(_report())
    for title in ("Category", "Expense ratio", "Performance", "Portfolio composition",
                  "Fund manager", "Risk & exit load", "Verdict drivers",
                  "What would change my mind"):
        assert f"### {title}" in md


def test_render_separates_facts_from_judgement(lex_home_tmp):
    md = fund_reports.render(_report())
    facts, interpretation, judgement = (md.index("## Facts"),
                                        md.index("## Interpretation"),
                                        md.index("## Judgement"))
    assert facts < interpretation < judgement
    assert md.index("### Category") < interpretation
    assert md.index("### Performance") < judgement


def test_render_survives_a_half_empty_report(lex_home_tmp):
    md = fund_reports.render({"scheme_code": "X", "sections": {"_raw": "model rambled"}})
    assert "no verdict" in md and "model rambled" in md


def test_researched_schemes(lex_home_tmp):
    assert fund_reports.researched_schemes() == []
    fund_reports.save(_report())
    fund_reports.save(_report(scheme_code="100001"))
    assert fund_reports.researched_schemes() == ["100001", "120503"]


def test_history_and_get_handlers(lex_home_tmp):
    fund_reports.save(_report())
    hist = json.loads(fund_reports.handle_fund_research_history({"scheme_code": "120503"}))
    assert hist["success"] and hist["data"]["reports"][0]["stance"] == "bullish"

    got = json.loads(fund_reports.handle_fund_research_get({"scheme_code": "120503"}))
    assert got["success"] and got["data"]["sections"]["verdict"]["stance"] == "bullish"
    assert "passes" not in got["data"]

    missing = json.loads(fund_reports.handle_fund_research_get({"scheme_code": "120503", "n": 9}))
    assert not missing["success"]


@pytest.fixture
def stub_passes(monkeypatch):
    monkeypatch.setattr("lex.delegate.run_pass", lambda brief, pass_type="fund_facts", tools=None: "X")
    monkeypatch.setattr("lex.llm.make_client", lambda: object())
    monkeypatch.setattr("lex.llm.default_model", lambda: "m")

    def fake_run(client, model, messages, tools, **kw):
        return json.dumps({"verdict": _VERDICT})

    monkeypatch.setattr("lex.agent.run", fake_run)


def test_fund_research_saves_and_then_runs_in_update_mode(lex_home_tmp, stub_passes):
    first = json.loads(fund_research.handle_fund_research({"scheme_code": "120503"}))["data"]
    assert first["delta_of"] is None
    assert first["saved_to"].endswith(".json")
    assert "passes" not in first

    second = json.loads(fund_research.handle_fund_research({"scheme_code": "120503"}))["data"]
    assert second["delta_of"] == first["generated_at"]
    assert len(fund_reports.history("120503")) == 2


def test_tools_dict_registers_fund_research_tools():
    from lex.tools import TOOLS
    for name in ("fund_research", "fund_research_history", "fund_research_get"):
        assert TOOLS[name]["schema"]["name"] == name
