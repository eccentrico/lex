# Lex

A conversational research agent for the Indian stock market. Ask it about a
stock, your watchlist, or what's moving the market, and it answers — right
in the terminal.

## Features

- **Market pulse** — NIFTY/SENSEX/BANK levels, sector moves, FII/DII flows, with macro context pulled in from the web
- **Deep research** — `/research TCS` runs a facts pass, a narrative pass, and an adversarial bear pass, then synthesizes a sourced verdict with a confidence level
- **Watchlist** — track symbols, check what changed since last time on demand
- **Portfolio view** — live holdings and P&L, joined with your own thesis notes
- **Memory** — remembers durable facts about you (risk profile, preferences) across sessions


> **Disclaimer:** Lex is an educational/research project, not financial
> advice. It is not intended for making real trading or investment
> decisions — output may be inaccurate, outdated, or incomplete.

See `CLAUDE.md` for the full architecture, layout, and gotchas.

## Setup

```bash
python -m venv .venv
.venv/bin/pip install -e .

# One-time / when the Kite token expires: headless login (TOTP-based)
.venv/bin/python -m services.kite_auth        # writes ~/.lex/kite_tokens.json (mode 600)
```

`.env` holds Kite credentials (`KITE_API_KEY`, `KITE_SECRET_KEY`,
`KITE_USER_ID`, `KITE_PASSWORD`, `KITE_TOTP_SECRET`) and the LLM endpoint
(`OPENAI_API_KEY`/`OPENAI_BASE_URL`).

`GROWW_API_KEY`/`GROWW_API_SECRET` (from Groww's API Keys dashboard, see
`services/groww_auth.py`) are optional — Groww is a secondary, explicit-only
data source and the app works fully without them.

## Run

```bash
.venv/bin/python -m lex
# or, after `pip install -e .`:
lex
```

Ask it anything about the Indian market, or drive research directly:

```
/research TCS          # facts -> narrative -> bear -> synthesis, then the answer
/research TCS --full   # every section, not just the verdict
/research TCS --quick  # skip the narrative pass
/reports               # symbols you've researched
/reports TCS           # that symbol's history, with verdicts and ages
/reports TCS -n 2      # print the second most recent report
```

Reports are saved under `~/.lex/research/<SYMBOL>/` as JSON plus rendered
markdown, and a repeat run on the same symbol comes back as an update —
what changed since last time — rather than a fresh essay.

## Test

```bash
pytest
```

## License

MIT — see `LICENSE`.
