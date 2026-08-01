import logging

import pytest
from rich.markdown import Markdown

from lex import repl, reports, sessions


def _state(lex_home_tmp):
    return repl.State(session=sessions.new_session(), messages=[])


def _text(reply):
    return reply.markup if isinstance(reply, Markdown) else reply


def test_slash_new_resets(lex_home_tmp):
    s = _state(lex_home_tmp)
    old = s.session
    s.messages.append({"role": "user", "content": "x"})
    assert repl.handle_command("/new", s)
    assert s.session != old and s.messages == []


def test_slash_resume(lex_home_tmp):
    s = _state(lex_home_tmp)
    sessions.append(s.session, {"role": "user", "content": "remember me"})
    s2 = _state(lex_home_tmp)
    assert repl.handle_command("/resume 2", s2)  # 1 = s2's own (empty), 2 = s
    assert any(m["content"] == "remember me" for m in s2.messages)


def test_slash_resume_bad_arg_does_not_crash(lex_home_tmp):
    s = _state(lex_home_tmp)
    reply = repl.handle_command("/resume abc", s)
    assert reply is not None and "number" in reply.lower()


def test_slash_memory_and_help(lex_home_tmp):
    s = _state(lex_home_tmp)
    assert "memory.md" in repl.handle_command("/memory", s)
    assert "/resume" in repl.handle_command("/help", s)
    assert "/research" in repl.handle_command("/help", s)
    assert repl.handle_command("/unknown", s)  # unknown -> help text, not crash
    assert repl.handle_command("hello", s) is None


_REPORT = {
    "symbol": "TCS", "depth": "full", "generated_at": "2026-07-20T09:00:00+00:00",
    "sections": {
        "business": "IT services", "financials": "flat revenue",
        "risks": [{"risk": "client concentration", "likelihood": "medium",
                   "impact": "high"}],
        "verdict": {"stance": "bearish", "confidence": "high",
                    "summary": "Cheap for a reason.",
                    "what_would_change_my_mind": ["order book recovery"]},
    },
}


@pytest.fixture
def fake_pipeline(monkeypatch):
    """Stand in for the LLM passes; record how /research invoked them."""
    seen = {}

    def run_and_save(symbol, brief="", depth="full", progress=None):
        seen.update(symbol=symbol, depth=depth)
        seen["stages"] = []
        for stage in ("facts", "narrative", "bear", "synthesis"):
            if progress:
                progress(stage)
                seen["stages"].append(stage)
        report = dict(_REPORT, symbol=symbol, depth=depth)
        reports.save(report)
        return report

    monkeypatch.setattr("lex.research.run_and_save", run_and_save)
    return seen


def test_research_needs_a_symbol(lex_home_tmp):
    assert "Usage" in repl.handle_command("/research", _state(lex_home_tmp))


def test_research_renders_the_brief_by_default(lex_home_tmp, fake_pipeline):
    out = _text(repl.handle_command("/research tcs", _state(lex_home_tmp)))
    assert fake_pipeline["symbol"] == "TCS" and fake_pipeline["depth"] == "full"
    assert "Cheap for a reason." in out and "bearish" in out
    assert "### Financials" not in out  # brief stops at the answer
    assert "/reports TCS -n 1 --full" in out


def test_research_full_flag_prints_every_section(lex_home_tmp, fake_pipeline):
    out = _text(repl.handle_command("/research TCS --full", _state(lex_home_tmp)))
    assert "### Financials" in out and "## Judgement" in out


def test_research_quick_flag_shortens_the_run(lex_home_tmp, fake_pipeline):
    repl.handle_command("/research TCS --quick", _state(lex_home_tmp))
    assert fake_pipeline["depth"] == "brief"


def test_research_reports_progress_for_each_pass(lex_home_tmp, fake_pipeline):
    repl.handle_command("/research TCS", _state(lex_home_tmp))
    assert fake_pipeline["stages"] == ["facts", "narrative", "bear", "synthesis"]


def test_research_failure_does_not_kill_the_repl(lex_home_tmp, monkeypatch):
    monkeypatch.setattr("lex.research.run_and_save",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("LLM down")))
    out = repl.handle_command("/research TCS", _state(lex_home_tmp))
    assert "LLM down" in out and "nothing was saved" in out


def test_reports_lists_symbols_then_history(lex_home_tmp, fake_pipeline):
    s = _state(lex_home_tmp)
    assert "No saved reports yet" in repl.handle_command("/reports", s)

    repl.handle_command("/research TCS", s)
    repl.handle_command("/research INFY", s)
    assert repl.handle_command("/reports", s).splitlines() == ["INFY (1)", "TCS (1)"]

    history = repl.handle_command("/reports tcs", s)
    assert history.startswith("1. 2026-07-20T09:00:00+00:00")
    assert "bearish / high" in history


def test_reports_prints_a_stored_report(lex_home_tmp, fake_pipeline):
    s = _state(lex_home_tmp)
    repl.handle_command("/research TCS", s)
    assert "Cheap for a reason." in _text(repl.handle_command("/reports TCS -n", s))
    assert "### Financials" in _text(repl.handle_command("/reports TCS -n 1 --full", s))


def test_reports_for_an_unknown_symbol(lex_home_tmp):
    s = _state(lex_home_tmp)
    assert "No saved reports for ZZZZ" in repl.handle_command("/reports ZZZZ", s)
    assert "No saved report #4" in repl.handle_command("/reports ZZZZ -n 4", s)


def test_configure_logging_is_file_only(lex_home_tmp):
    repl.configure_logging()
    root = logging.getLogger()
    file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
    assert file_handlers, "expected a FileHandler on the root logger"
    assert file_handlers[0].baseFilename == str(lex_home_tmp / "lex.log")
    # a plain StreamHandler (console output) must never be attached — not by
    # us, and not by services/kite_data.py's own basicConfig(level=INFO) call
    # at import time, which must be a no-op since we already claimed the root
    # logger's handlers here.
    assert not any(type(h) is logging.StreamHandler for h in root.handlers)
    logging.basicConfig(level=logging.INFO)
    assert not any(type(h) is logging.StreamHandler for h in root.handlers)
