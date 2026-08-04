# Mutual fund support — design

## Context

Lex currently covers NSE equities only: quotes, fundamentals, technicals,
peer comparison, ownership signals, corporate actions, a stock-shaped
`deep_research` pipeline, and an equity-symbol watchlist. This adds a
parallel, independent surface for mutual funds: lookup/quote, a watchlist,
and a fund-shaped research pipeline. Portfolio holdings integration, SIP/
order placement, and quality scoring for fund reports are explicitly out of
scope for this pass (see "Out of scope").

Chosen up front (via brainstorming):
- Scope: fund lookup/quote, watchlist, and a fund research pipeline. Not
  portfolio holdings.
- NAV history source: AMFI's daily/history NAV feed (free, official,
  matches the no-paid-vendor pattern already used for NSE/BSE data).
- Research shape: funds get their own pipeline and section schema, not the
  equity one — a fund has no moat/promoter/peer-multiple concepts.
- Tool surface: new `fund_*`/`mf_*` tools, existing equity tools untouched.

## Data layer — `services/indian_data/mutual_funds.py`

Same shape as the other `indian_data/` modules (function-based, no classes,
following `symbol_mapping.py`/`screener_service.py` conventions):

- **Scheme master + latest NAV**: Kite's `mf_instruments()` (already
  authenticated via the existing `services/kite_auth.py`/`kite_data.py` —
  no new credentials). Its `tradingsymbol` field is assumed to be the AMFI
  scheme code; this is verified against a live pull during implementation,
  not assumed silently — if it turns out to differ, the join key changes
  but nothing else in this design does.
- **NAV history**: AMFI's NAV history endpoint, fetched by scheme code +
  date range. Cached on disk the way `fundamental_cache_service.py` already
  caches screener pulls, to avoid re-fetching AMFI on every call.
- **Name → scheme_code lookup**: fuzzy match over the `mf_instruments()`
  dump, same idea as `symbol_mapping.py` for equities.

## Tools — `lex/tools/mutual_funds.py`

Registered into the existing flat `TOOLS` dict in `lex/tools/__init__.py`.
Parallel to the equity tools; nothing existing is modified.

- `fund_search(query, limit?)` — name/AMC → scheme_code + scheme name.
  Mirrors `symbol_search`.
- `fund_quote(scheme_codes)` — latest NAV, NAV date, AMC, category, plan
  (direct/regular) for up to N schemes.
- `mf_watchlist_add(scheme_code, note?)` / `mf_watchlist_remove(scheme_code)`
  / `mf_watchlist_status()` — separate `mf_watchlist.json` (mirrors
  `watchlist.json`), diffing latest NAV against a stored baseline the same
  way equity diffs LTP. No announcements field — AMFI has no equivalent
  feed, so the field is simply absent from a fund entry rather than
  reported as "unknown" (that distinction matters: "unknown" means a
  pillar exists but the data source failed; here the pillar doesn't exist).
- `fund_research(scheme_code, brief?, depth?)` — the research tool
  (below).

## Research pipeline — `lex/fund_research.py`

Mirrors `lex/research.py`'s three-pass shape (facts → bear → synthesis for
`brief`, facts → narrative → bear → synthesis for `full`) but with its own
section schema — reusing the equity schema would leave every fund report
with permanent "unknown — not applicable" business/moat/ownership
sections, which is worse than a fund-specific schema:

```
category, expense_ratio, performance   (vs benchmark + category average)
portfolio_composition                  (top holdings/sectors, concentration)
fund_manager                           (tenure, other funds run)
risk_exit_load, verdict
```

- Facts pass gathers the above pillars via `fund_quote` + `fund_history` +
  `web_search`/`web_fetch` for fund-house factsheets.
- Narrative pass (full depth only) builds the "why hold this fund" case on
  top of the facts pass.
- Bear pass attacks specifically: closet indexing, manager churn,
  high-turnover tax drag, category-relative underperformance — same rule
  as equity's bear pass, attack the SPECIFIC claims made above, no generic
  risk boilerplate.
- Synthesis adjudicates into the fixed schema above, no tools, same as
  equity's `_synthesize`.

New prompts in `lex/prompt.py`: `FUND_FACTS_PASS`, `FUND_NARRATIVE_PASS`,
`FUND_BEAR_PASS`, `FUND_SYNTHESIS_PROMPT`, sharing `EVIDENCE_RULES` with
the equity passes.

New restricted tool set in `lex/delegate.py`:

```python
RESEARCH_FUND_TOOL_NAMES = frozenset({
    "fund_search", "fund_quote", "web_search", "web_fetch"})
```

Same isolation rationale as equity's `RESEARCH_TOOL_NAMES`: read-only data
and web only, no watchlist/memory, no recursive delegation, no reading
prior saved fund reports from inside a pass.

## Reports — `lex/fund_reports.py`

Mirrors `lex/reports.py` (`save`/`load`/`history`/`render`/`delta_context`)
against the fund section schema, saved under
`~/.lex/mf_research/<SCHEME_CODE>/` as JSON + rendered Markdown, same
update-mode behavior (`delta_context` feeds the prior verdict into a
re-run so it leads with what changed).

Duplicated rather than generalizing `reports.py` to take a schema
parameter — the section schemas genuinely differ in shape (list-valued
sections, grouping into Facts/Interpretation) and equity code stays
untouched per the tool-surface decision above.

## Prompt integration — `lex/prompt.py`

New `MUTUAL_FUNDS` playbook block, same pattern as `RESEARCH`:
- Full analysis of a fund, or comparison of 2+ → `fund_research` (one call
  per scheme).
- Quick factual lookup (current NAV, category, expense ratio) → answer
  inline with `fund_quote`/`fund_search`; never run `fund_research` for a
  single number.
- `mf_watchlist_status` only on an explicit ask, same rule as equity's
  `watchlist_status`.

`build_system_prompt` includes this block unconditionally, same as
`MARKET_PULSE`/`RESEARCH` today.

## Out of scope (this pass)

- **Portfolio holdings integration** — `portfolio_status` stays
  equity-only; `mf_holdings()` is not wired in.
- **SIP/order placement or viewing** — `mf_orders()`/`mf_sips()`/
  `place_mf_order`/`place_mf_sip` are not exposed as tools. Lex places no
  orders, equity or fund, by design (see CLAUDE.md: "No trade execution
  path").
- **Quality scoring for fund reports** — `lex/quality.py` stays
  equity-only; `fund_research` reports are not scored.

## Testing

- Unit tests per new tool handler (`fund_search`, `fund_quote`,
  `mf_watchlist_*`), same style as existing `tests/lex/` coverage —
  mocked Kite/AMFI responses, no network.
- One end-to-end `fund_research` run against a scripted LLM (same harness
  pattern as `test_quality_harness.py` uses for equity, minus the quality
  scoring step since that's out of scope).
