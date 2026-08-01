import json

import pytest

from lex import reports, research

_VERDICT = {"stance": "bearish", "confidence": "high", "drivers": ["margin squeeze"],
            "what_would_change_my_mind": ["order book recovery"],
            "summary": "Cheap for a reason."}


def _report(symbol="TCS", **over):
    report = {
        "symbol": symbol, "brief": "", "depth": "full",
        "generated_at": "2026-07-20T09:00:00+00:00",
        "sections": {
            "business": "IT services", "financials": "flat revenue",
            "filings_events": "Q1 filed", "news": "quiet",
            "valuation": "22x", "technicals": "below 200 DMA",
            "ownership": "promoter stake flat",
            "risks": [{"risk": "client concentration", "likelihood": "medium", "impact": "high"},
                      {"risk": "wage inflation", "likelihood": "high", "impact": "medium"},
                      {"risk": "rupee", "likelihood": "low", "impact": "low"},
                      {"risk": "attrition", "likelihood": "low", "impact": "medium"}],
            "catalysts": [{"catalyst": "Q2 print", "timing": "Oct", "direction": "negative"}],
            "verdict": dict(_VERDICT),
        },
        "passes": {"facts": "F", "narrative": "N", "bear": "B"},
    }
    report.update(over)
    return report


def test_save_writes_json_and_markdown(lex_home_tmp):
    path = reports.save(_report())
    assert path.parent == lex_home_tmp / "research" / "TCS"
    assert json.loads(path.read_text(encoding="utf-8"))["symbol"] == "TCS"
    md = path.with_suffix(".md").read_text(encoding="utf-8")
    assert "bearish" in md and "Cheap for a reason." in md


def test_unsafe_symbols_get_a_safe_directory(lex_home_tmp):
    reports.save(_report(symbol="M&M"))
    assert (lex_home_tmp / "research" / "M_M").is_dir()
    assert reports.load("m&m")["symbol"] == "M&M"


def test_load_is_newest_first_and_bounds_checked(lex_home_tmp):
    reports.save(_report(generated_at="first"))
    reports.save(_report(generated_at="second"))
    assert reports.load("TCS")["generated_at"] == "second"
    assert reports.load("TCS", 2)["generated_at"] == "first"
    assert reports.load("TCS", 3) is None
    assert reports.load("TCS", 0) is None
    assert reports.load("NOSUCH") is None


def test_history_digest(lex_home_tmp):
    reports.save(_report())
    reports.save(_report())
    hist = reports.history("TCS")
    assert len(hist) == 2
    assert hist[0]["stance"] == "bearish" and hist[0]["confidence"] == "high"
    assert hist[0]["summary"] == "Cheap for a reason."
    assert reports.history("NOSUCH") == []


def test_corrupt_report_is_skipped_not_fatal(lex_home_tmp):
    reports.save(_report())
    (lex_home_tmp / "research" / "TCS" / "99999999-000000000000.json").write_text(
        "{not json", encoding="utf-8")
    assert reports.load("TCS") is None  # newest file is the broken one
    assert len(reports.history("TCS")) == 1  # ... but history skips past it


def test_delta_context_empty_on_first_look(lex_home_tmp):
    assert reports.delta_context("TCS") == ("", None)


def test_delta_context_carries_prior_verdict(lex_home_tmp):
    reports.save(_report())
    context, prior_at = reports.delta_context("TCS")
    assert prior_at == "2026-07-20T09:00:00+00:00"
    assert "bearish" in context and "Cheap for a reason." in context
    assert "order book recovery" in context
    assert "UPDATE" in context and "changed" in context


def test_render_brief_is_short_and_leads_with_the_answer(lex_home_tmp):
    md = reports.render(_report(), mode="brief")
    assert md.startswith("# TCS — bearish (confidence: high)")
    assert "Cheap for a reason." in md
    assert md.count("- ") <= 4  # 3 risks + 1 change-my-mind
    assert "## Business & moat" not in md
    assert "attrition" not in md  # only the top 3 risks


def test_render_full_has_every_section(lex_home_tmp):
    md = reports.render(_report())
    for title in ("Business & moat", "Financials", "Filings & events", "News",
                  "Valuation", "Technicals", "Ownership & flows", "Risks",
                  "Catalysts", "Verdict drivers", "What would change my mind"):
        assert f"### {title}" in md
    assert "Q2 print — Oct (negative)" in md


def test_render_full_separates_facts_from_judgement(lex_home_tmp):
    md = reports.render(_report())
    facts, interpretation, judgement = (md.index("## Facts"),
                                        md.index("## Interpretation"),
                                        md.index("## Judgement"))
    assert facts < interpretation < judgement
    # what was fetched sits above what was concluded from it
    assert md.index("### Financials") < interpretation
    assert md.index("### Business & moat") < judgement


def test_render_skips_empty_groups(lex_home_tmp):
    md = reports.render({"symbol": "X", "sections": {"business": "just this"}})
    assert "## Facts" not in md and "## Interpretation" in md


def test_render_stamps_freshness(lex_home_tmp):
    from datetime import datetime, timedelta, timezone
    two_days = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    assert "2 days ago" in reports.render(_report(generated_at=two_days))
    assert "full run" in reports.render(_report())


@pytest.mark.parametrize("delta,expected", [
    ({"days": 0}, "today"), ({"days": 1}, "1 day ago"), ({"days": 9}, "9 days ago"),
])
def test_age(delta, expected):
    from datetime import datetime, timedelta, timezone
    stamp = (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()
    assert reports.age(stamp) == expected


def test_age_of_nonsense_is_none():
    assert reports.age(None) is None and reports.age("last tuesday") is None


def test_researched_symbols(lex_home_tmp):
    assert reports.researched_symbols() == []
    reports.save(_report())
    reports.save(_report(symbol="INFY"))
    assert reports.researched_symbols() == ["INFY", "TCS"]


def test_render_survives_a_half_empty_report(lex_home_tmp):
    md = reports.render({"symbol": "X", "sections": {"_raw": "model rambled"}})
    assert "no verdict" in md and "model rambled" in md


def test_render_notes_an_update(lex_home_tmp):
    assert "update on 2026-07-01" in reports.render(_report(delta_of="2026-07-01"))


def test_history_and_get_handlers(lex_home_tmp):
    reports.save(_report())
    hist = json.loads(reports.handle_research_history({"symbol": "tcs"}))
    assert hist["success"] and hist["data"]["reports"][0]["stance"] == "bearish"

    got = json.loads(reports.handle_research_get({"symbol": "TCS"}))
    assert got["success"] and got["data"]["sections"]["verdict"]["stance"] == "bearish"
    assert "passes" not in got["data"]  # working notes stay on disk

    missing = json.loads(reports.handle_research_get({"symbol": "TCS", "n": 9}))
    assert not missing["success"]


@pytest.fixture
def stub_passes(monkeypatch):
    monkeypatch.setattr("lex.delegate.run_pass", lambda brief, pass_type="general": "X")
    monkeypatch.setattr("lex.llm.make_client", lambda: object())
    monkeypatch.setattr("lex.llm.default_model", lambda: "m")
    seen = {}

    def fake_run(client, model, messages, tools, **kw):
        seen["messages"] = messages
        return json.dumps({"verdict": _VERDICT})

    monkeypatch.setattr("lex.agent.run", fake_run)
    return seen


def test_deep_research_saves_and_then_runs_in_update_mode(lex_home_tmp, stub_passes):
    first = json.loads(research.handle_deep_research({"symbol": "TCS"}))["data"]
    assert first["delta_of"] is None
    assert first["saved_to"].endswith(".json")
    assert "passes" not in first

    second = json.loads(research.handle_deep_research({"symbol": "TCS"}))["data"]
    assert second["delta_of"] == first["generated_at"]
    assert "UPDATE" in stub_passes["messages"][-1]["content"]
    assert len(reports.history("TCS")) == 2


def test_deep_research_survives_an_unwritable_home(lex_home_tmp, stub_passes, monkeypatch):
    monkeypatch.setattr("lex.reports.save",
                        lambda r: (_ for _ in ()).throw(OSError("disk full")))
    out = json.loads(research.handle_deep_research({"symbol": "TCS"}))
    assert out["success"] and out["data"]["saved_to"] is None
    assert "disk full" in out["data"]["save_error"]
