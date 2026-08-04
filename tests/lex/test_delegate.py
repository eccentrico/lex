from lex import delegate


def test_restricted_tool_set_is_read_only_market_and_web():
    from lex.tools import TOOLS
    allowed = delegate.RESEARCH_TOOL_NAMES
    assert allowed <= set(TOOLS)
    forbidden = {"portfolio_status", "order_book", "thesis_note", "memory_save",
                 "watchlist_add", "watchlist_remove", "watchlist_status",
                 "deep_research"}
    assert not (allowed & forbidden)
    assert {"symbol_search", "market_quote", "fundamentals",
            "market_events", "web_search", "web_fetch"} <= allowed


def test_run_pass_uses_restricted_tools_and_pass_specific_prompt(monkeypatch):
    from lex import prompt
    seen = {}
    def fake_run(client, model, messages, tools, **kw):
        seen["tools"] = set(tools)
        seen["brief"] = messages[-1]["content"]
        seen["system"] = messages[0]["content"]
        return "REPORT"
    monkeypatch.setattr("lex.agent.run", fake_run)
    monkeypatch.setattr("lex.llm.make_client", lambda: object())

    out = delegate.run_pass("Research INFY", "bear")

    assert out == "REPORT"
    assert seen["brief"] == "Research INFY"
    assert seen["tools"] == set(delegate.RESEARCH_TOOL_NAMES)
    assert prompt.BEAR_PASS in seen["system"]
    assert prompt.EVIDENCE_RULES in seen["system"]


def test_fund_tool_set_is_read_only_fund_and_web():
    from lex.tools import TOOLS
    allowed = delegate.RESEARCH_FUND_TOOL_NAMES
    assert allowed <= set(TOOLS)
    forbidden = {"mf_watchlist_add", "mf_watchlist_remove", "mf_watchlist_status",
                 "fund_research", "portfolio_status", "memory_save"}
    assert not (allowed & forbidden)
    assert {"fund_search", "fund_quote", "fund_history", "web_search", "web_fetch"} <= allowed


def test_run_pass_accepts_a_tool_set_override(monkeypatch):
    from lex import prompt
    seen = {}

    def fake_run(client, model, messages, tools, **kw):
        seen["tools"] = set(tools)
        seen["system"] = messages[0]["content"]
        return "REPORT"

    monkeypatch.setattr("lex.agent.run", fake_run)
    monkeypatch.setattr("lex.llm.make_client", lambda: object())

    out = delegate.run_pass("Research 120503", "fund_bear", tools=delegate.RESEARCH_FUND_TOOL_NAMES)

    assert out == "REPORT"
    assert seen["tools"] == set(delegate.RESEARCH_FUND_TOOL_NAMES)
    assert prompt.FUND_BEAR_PASS in seen["system"]


def test_run_pass_default_tool_set_is_unchanged(monkeypatch):
    """The existing 2-arg call sites (fund_research aside) must keep working."""
    seen = {}

    def fake_run(client, model, messages, tools, **kw):
        seen["tools"] = set(tools)
        return "REPORT"

    monkeypatch.setattr("lex.agent.run", fake_run)
    monkeypatch.setattr("lex.llm.make_client", lambda: object())

    delegate.run_pass("Research INFY", "bear")
    assert seen["tools"] == set(delegate.RESEARCH_TOOL_NAMES)
