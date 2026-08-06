from lex import prompt


def test_build_system_prompt_includes_fund_playbook():
    text = prompt.build_system_prompt("")
    assert "fund_research" in text
    assert "mf_watchlist_status" in text


def test_subagent_prompt_resolves_fund_passes():
    assert prompt.FUND_FACTS_PASS in prompt.subagent_prompt("fund_facts")
    assert prompt.EVIDENCE_RULES in prompt.subagent_prompt("fund_facts")
    assert prompt.FUND_NARRATIVE_PASS in prompt.subagent_prompt("fund_narrative")
    assert prompt.FUND_BEAR_PASS in prompt.subagent_prompt("fund_bear")


def test_equity_passes_still_resolve():
    assert prompt.FACTS_PASS in prompt.subagent_prompt("facts")
    assert prompt.BEAR_PASS in prompt.subagent_prompt("bear")


def test_build_system_prompt_includes_groww_guidance():
    text = prompt.build_system_prompt("")
    assert "groww_quote" in text
    assert "groww_price_history" in text


def test_persona_distinguishes_kite_and_groww_auth_errors():
    assert "kite_auth" in prompt.PERSONA
    assert "Groww" in prompt.PERSONA
