# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Lex is a conversational research/watchlist agent for the Indian financial
market — market pulse briefings, ask-me-anything market Q&A, security
research via subagents, portfolio view with thesis notes, and a watchlist
with on-demand "what changed" alerts. It's reactive: everything runs in
response to a question asked of it, in the turn it was asked. Lex researches
and advises; you place orders yourself in Kite.

`lex/` is the whole agent: a small, self-contained package with its own
REPL, agent loop, LLM client, prompt, memory, sessions, and subagent
delegation. It imports only `services/`, itself, and a short list of
third-party packages.

## Running Lex locally

```bash
# One-time / when token expires: headless Kite login (TOTP-based)
.venv/bin/python -m services.kite_auth        # writes ~/.lex/kite_tokens.json (mode 600)

# Start the REPL
.venv/bin/python -m lex
# or, once installed (pip install -e .):
lex
```

`.env` holds Kite creds (`KITE_API_KEY`, `KITE_SECRET_KEY`, `KITE_USER_ID`,
`KITE_PASSWORD`, `KITE_TOTP_SECRET`) and the LLM endpoint
(`OPENAI_API_KEY`/`OPENAI_BASE_URL`) — `lex/llm.py` talks to whatever
OpenAI-compatible endpoint `OPENAI_BASE_URL` points at (DeepSeek today).

## Tests

`pip install -e ".[dev]"` gets you `pytest`+`ruff` (not installed by a plain
`pip install -e .`).

```bash
pytest                                  # whole suite
pytest tests/lex/                       # the only suite there is
pytest tests/lex/test_repl.py -v        # single file
pytest -k 'pattern'
```

`tests/conftest.py` blanks any env var ending `_API_KEY`/`_TOKEN`/`_SECRET`/
`_PASSWORD`/`_SECRET_KEY` or starting `KITE_`, and points `LEX_HOME` at a
per-test tmp dir — no test can touch real credentials or `~/.lex`.
`tests/lex/conftest.py` adds one convenience fixture, `lex_home_tmp`, that
just reads back the `LEX_HOME` the root conftest already set.

Lint (only `PLW1514` unspecified-encoding is enforced):

```bash
ruff check .
```

## Layout

```
lex/
  __main__.py      # python -m lex -> REPL entry point
  repl.py          # readline loop, rich markdown, /new /resume /memory /sessions
                   #   /research /reports /quit
  agent.py         # messages -> LLM -> tool calls -> results -> repeat
  llm.py           # openai SDK client on OPENAI_BASE_URL
  prompt.py        # persona + market-pulse/research/analysis playbooks
  memory.py        # ~/.lex/memory.md read into the system prompt; memory_save tool
  sessions.py      # one JSONL file per session under ~/.lex/sessions/; list/resume
  paths.py         # lex_home() -- the one place LEX_HOME (default ~/.lex) is resolved
  delegate.py      # nested agent.run() over a restricted tool set (research subagents)
  research.py      # deep_research: facts -> narrative -> bear passes -> parent synthesis
  reports.py       # saved reports under ~/.lex/research/<SYMBOL>/ + brief/full renderers
  quality.py       # structural score for a finished report; findings go to lex.log
  tools/
    __init__.py    # TOOLS = {name: {"schema": ..., "handler": ...}} -- plain dict, no registry
    market.py      # symbol search, quotes, price history, market overview, fundamentals, events
    technicals.py  # returns, 50/200 DMA + cross, drawdown, volume surge, excess return
    peers.py       # peer_comparison -- valuation/profitability vs sector peers + median
    ownership.py   # ownership_signals (insider/bulk/block) + corporate_actions
    portfolio.py   # portfolio/order-book view + thesis notes (flat JSON, no SQLite)
    watchlist.py   # add/remove/status -- the reactive "what changed" mechanism
    web.py         # keyless web_search (DuckDuckGo) + fetch-as-text
    common.py      # shared JSON envelope (_ok/_err) for tool handlers
services/          # finance data layer -- Kite auth/data, NSE/BSE feeds, valuation
tests/lex/         # the test suite
```

## Architecture

Two layers:
1. **`lex/`** — the agent: REPL, agent loop, LLM client, prompt, memory,
   sessions, delegation, tools. No registry/toolsets indirection —
   `lex/tools/__init__.py` builds one plain `TOOLS` dict directly.
2. **`services/`** — the finance data layer it calls into:
   - `services/indian_data/` — NSE/BSE sessions, announcements, events,
     earnings calendar, FII/DII flows, sector indices, screener/fundamental
     service, symbol mapping, analyst estimates
   - `services/valuation/dcf.py` (+ `sector_wacc_india.py`) — DCF valuation
     recipe used by the `finance-analysis` playbook
   - `services/kite_auth.py`, `services/kite_data.py` — Kite Connect auth
     (TOTP, non-interactive) and data access
   - `services/market_calendar.py` — NSE trading calendar
   - `services/paths.py` — `lex_home()`, duplicated (not imported) from
     `lex/paths.py`: `services/` deliberately doesn't depend on the `lex/`
     package, keeping the dependency direction one-way (`lex/tools` ->
     `services`, never the reverse).

## Research pipeline

`deep_research` (and `/research`) is three subagent passes plus a parent
synthesis: **facts** gathers the record, **narrative** builds the business
picture on top of it, **bear** receives both as text it must attack, and the
synthesis runs with no tools and adjudicates them into the fixed section
schema in `lex/research.py`. Reports persist to
`~/.lex/research/<SYMBOL>/` as JSON plus rendered markdown; a second run on
the same symbol automatically becomes an update, with the prior verdict fed
into every pass. `lex/quality.py` scores each finished report for structure
(pillars filled or declared unknown, source tags present, bear pass engaged
with the earlier passes, verdict falsifiable) and logs findings to
`~/.lex/lex.log`. `tests/lex/test_quality_harness.py` runs five archetypes
(compounder, cyclical, contested governance, recent blow-up, boring PSU)
through the real pipeline against a scripted LLM — no network, no Kite.

Subagents spawned via `delegate_research` (`lex/delegate.py`) get a
restricted tool set — `RESEARCH_TOOL_NAMES` in `lex/delegate.py` — read-only
market data only (symbol search, quotes, history, overview, fundamentals,
events, corporate actions, ownership signals, peers, technicals, web
search/fetch): no portfolio/watchlist tools, no memory writes, no saved
research (a pass that can read our previous verdict is not an independent
look), no recursive delegation. This is what keeps prompt injection via a
subagent's web research from reaching anything stateful. If you add a new
read-only market tool, decide *consciously* whether research subagents
should get it too — `RESEARCH_TOOL_NAMES` doesn't grow automatically with
`TOOLS`.

## Notable gotchas

- `market_events` "corporate_actions" is a real corporate-actions feed
  (`services/indian_data/corporate_actions.py`, NSE with a BSE-announcement
  fallback — check each row's `source`); insider (PIT) trades live under
  their own `insider_trades` key.
- Index history (`sector_indices.get_index_history`) comes from NSE, not
  Kite: `kite_data._symbol_to_token` filters `instrument_type == "EQ"`, so
  no index resolves through the instruments dump. An empty list means the
  endpoint was unavailable — `technicals` reports that as "unknown" rather
  than treating it as a flat index.
- Research subagents are tool-restricted by `RESEARCH_TOOL_NAMES` in
  `lex/delegate.py` — extend that set consciously, not automatically.
- No trade execution path — Lex is a research tool, not a broker client.
