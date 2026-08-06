"""Research subagents: nested agent.run() over a restricted tool dict.

Restriction rationale (carried from the old design): research verdicts must
not anchor on current holdings, and fetched web content must never reach
anything stateful — so no portfolio, memory, watchlist, or nested delegation.
"""
RESEARCH_TOOL_NAMES = frozenset({
    "symbol_search", "market_quote", "price_history", "market_overview",
    "fundamentals", "market_events", "web_search", "web_fetch",
    # read-only market data, same as the rest: they expose company filings and
    # public market structure, nothing about the user. Saved-research tools
    # (research_history/research_get) stay out on purpose — a pass that can
    # read our previous verdict stops being an independent look.
    "corporate_actions", "ownership_signals", "peer_comparison", "technicals",
    # Groww is a second read-only market-data source, same rationale as
    # market_quote/price_history above — it exposes no user-specific state.
    "groww_quote", "groww_price_history"})

# Same isolation rationale, fund side: read-only fund/web data only, no
# mf_watchlist, no fund_research_history/get, no recursive delegation.
RESEARCH_FUND_TOOL_NAMES = frozenset({
    "fund_search", "fund_quote", "fund_history", "web_search", "web_fetch"})


def run_pass(brief: str, pass_type: str, tools: frozenset = RESEARCH_TOOL_NAMES) -> str:
    """Run one research-pipeline pass over `tools`; return its report text."""
    from lex import agent, llm, prompt
    from lex.tools import TOOLS
    sub_tools = {k: v for k, v in TOOLS.items() if k in tools}
    messages = [
        {"role": "system", "content": prompt.subagent_prompt(pass_type)},
        {"role": "user", "content": brief},
    ]
    return agent.run(llm.make_client(), llm.default_model(), messages, sub_tools)
