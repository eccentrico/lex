"""Single tool registry: {name: {"schema": ..., "handler": ...}}. No classes."""
from lex.memory import handle_memory_save
from lex.tools.market import (
    handle_symbol_search, handle_market_quote, handle_price_history,
    handle_market_overview, handle_fundamentals, handle_market_events)
from lex.tools.ownership import (
    handle_ownership_signals, handle_corporate_actions)
from lex.tools.peers import handle_peer_comparison
from lex.tools.portfolio import (
    handle_portfolio_status, handle_order_book, handle_thesis_note)
from lex.tools.technicals import handle_technicals
from lex.tools.watchlist import (
    handle_watchlist_add, handle_watchlist_remove, handle_watchlist_status)
from lex.tools.mutual_funds import (
    handle_fund_search, handle_fund_quote, handle_fund_history)
from lex.tools.mf_watchlist import (
    handle_mf_watchlist_add, handle_mf_watchlist_remove, handle_mf_watchlist_status)
from lex.tools.web import (
    handle_web_search, handle_web_fetch)
from lex.tools.groww import handle_groww_quote, handle_groww_price_history


def _lazy_deep_research(args):
    """Lazy import: lex.research reaches back into this module for the tool dict."""
    from lex.research import handle_deep_research
    return handle_deep_research(args)


def _lazy_research_history(args):
    from lex.reports import handle_research_history
    return handle_research_history(args)


def _lazy_research_get(args):
    from lex.reports import handle_research_get
    return handle_research_get(args)


def _lazy_fund_research(args):
    """Lazy import: lex.fund_research reaches back into this module's TOOLS dict."""
    from lex.fund_research import handle_fund_research
    return handle_fund_research(args)


def _lazy_fund_research_history(args):
    from lex.fund_reports import handle_fund_research_history
    return handle_fund_research_history(args)


def _lazy_fund_research_get(args):
    from lex.fund_reports import handle_fund_research_get
    return handle_fund_research_get(args)

TOOLS = {
    "symbol_search": {
        "schema": {
            "name": "symbol_search",
            "description": (
                "Look up NSE trading symbols by company name or partial symbol. "
                "Use before any tool that needs an exact tradingsymbol "
                "(e.g. 'tata motors' -> TATAMOTORS)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Company name or partial symbol"},
                    "limit": {"type": "integer", "description": "Max results (default 5)"},
                },
                "required": ["query"],
            },
        },
        "handler": handle_symbol_search,
    },
    "fund_search": {
        "schema": {
            "name": "fund_search",
            "description": (
                "Look up mutual fund schemes by name or AMC. Use before any tool "
                "that needs an exact scheme_code (e.g. 'parag parikh flexi cap "
                "direct' -> scheme_code)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Scheme name or AMC"},
                    "limit": {"type": "integer", "description": "Max results (default 5)"},
                },
                "required": ["query"],
            },
        },
        "handler": handle_fund_search,
    },
    "market_quote": {
        "schema": {
            "name": "market_quote",
            "description": "Live quotes (LTP, OHLC, volume, circuit limits) for up to 25 NSE symbols.",
            "parameters": {"type": "object", "properties": {
                "symbols": {"type": "array", "items": {"type": "string"},
                            "description": "Exact NSE tradingsymbols"}},
                "required": ["symbols"]},
        },
        "handler": handle_market_quote,
    },
    "fund_quote": {
        "schema": {
            "name": "fund_quote",
            "description": "Latest NAV, AMC, plan and scheme type for up to 25 mutual fund scheme_codes.",
            "parameters": {"type": "object", "properties": {
                "scheme_codes": {"type": "array", "items": {"type": "string"},
                                 "description": "Exact AMFI scheme codes (from fund_search)"}},
                "required": ["scheme_codes"]},
        },
        "handler": handle_fund_quote,
    },
    "fund_history": {
        "schema": {
            "name": "fund_history",
            "description": "Daily NAV history for one mutual fund scheme_code between two dates (YYYY-MM-DD), sourced from AMFI.",
            "parameters": {"type": "object", "properties": {
                "scheme_code": {"type": "string"},
                "from_date": {"type": "string"}, "to_date": {"type": "string"}},
                "required": ["scheme_code", "from_date", "to_date"]},
        },
        "handler": handle_fund_history,
    },
    "mf_watchlist_add": {
        "schema": {
            "name": "mf_watchlist_add",
            "description": "Add a mutual fund scheme to your watchlist for tracking NAV changes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scheme_code": {"type": "string", "description": "AMFI scheme code (from fund_search)"},
                    "note": {"type": "string", "description": "Optional note"},
                },
                "required": ["scheme_code"],
            },
        },
        "handler": handle_mf_watchlist_add,
    },
    "mf_watchlist_remove": {
        "schema": {
            "name": "mf_watchlist_remove",
            "description": "Remove a mutual fund scheme from your watchlist.",
            "parameters": {
                "type": "object",
                "properties": {"scheme_code": {"type": "string"}},
                "required": ["scheme_code"],
            },
        },
        "handler": handle_mf_watchlist_remove,
    },
    "mf_watchlist_status": {
        "schema": {
            "name": "mf_watchlist_status",
            "description": ("What changed on the fund watchlist since last check: NAV moves "
                            "vs baseline. Call only when the user explicitly asks about their "
                            "fund watchlist — never on a bare greeting."),
            "parameters": {"type": "object", "properties": {}},
        },
        "handler": handle_mf_watchlist_status,
    },
    "fund_research": {
        "schema": {
            "name": "fund_research",
            "description": ("Full multi-pass research on one mutual fund scheme: a facts "
                            "pass, a narrative pass, then an adversarial bear pass, "
                            "synthesised into a sectioned report with a verdict and "
                            "confidence. This is the tool for 'analyse this fund' / "
                            "comparisons (one call per scheme_code). It is slow and "
                            "expensive — never use it for a single NAV or ratio."),
            "parameters": {
                "type": "object",
                "properties": {
                    "scheme_code": {"type": "string", "description": "Exact AMFI scheme code"},
                    "brief": {"type": "string", "description": "Optional extra focus, e.g. 'the user cares about the expense ratio and manager tenure'"},
                    "depth": {"type": "string", "description": "full (default, 3 passes) | brief (facts + bear only, faster)"},
                },
                "required": ["scheme_code"],
            },
        },
        "handler": _lazy_fund_research,
    },
    "fund_research_history": {
        "schema": {
            "name": "fund_research_history",
            "description": ("Past fund_research reports saved for a scheme, newest first, "
                            "with their date and verdict. Check this before running "
                            "fund_research so you can talk about what changed instead of "
                            "starting from scratch."),
            "parameters": {"type": "object", "properties": {
                "scheme_code": {"type": "string", "description": "Exact AMFI scheme code"}},
                "required": ["scheme_code"]},
        },
        "handler": _lazy_fund_research_history,
    },
    "fund_research_get": {
        "schema": {
            "name": "fund_research_get",
            "description": ("Read back one saved fund research report in full (sections + "
                            "verdict). n=1 is the most recent; use fund_research_history to "
                            "see what exists."),
            "parameters": {"type": "object", "properties": {
                "scheme_code": {"type": "string", "description": "Exact AMFI scheme code"},
                "n": {"type": "integer", "description": "1 = latest (default)"}},
                "required": ["scheme_code"]},
        },
        "handler": _lazy_fund_research_get,
    },
    "price_history": {
        "schema": {
            "name": "price_history",
            "description": "Daily OHLCV history for one NSE symbol between two dates (YYYY-MM-DD).",
            "parameters": {"type": "object", "properties": {
                "symbol": {"type": "string"},
                "from_date": {"type": "string"}, "to_date": {"type": "string"},
                "interval": {"type": "string", "description": "day (default), week"}},
                "required": ["symbol", "from_date", "to_date"]},
        },
        "handler": handle_price_history,
    },
    "groww_quote": {
        "schema": {
            "name": "groww_quote",
            "description": ("Live quotes for up to 25 NSE symbols via Groww's Trade API — an "
                            "explicit secondary source, separate from Kite's market_quote. Use "
                            "only when the user asks to cross-check a price against Groww or "
                            "says Kite's feed looks off; never as a silent fallback."),
            "parameters": {"type": "object", "properties": {
                "symbols": {"type": "array", "items": {"type": "string"},
                            "description": "Exact NSE tradingsymbols"}},
                "required": ["symbols"]},
        },
        "handler": handle_groww_quote,
    },
    "groww_price_history": {
        "schema": {
            "name": "groww_price_history",
            "description": ("Daily OHLC candle data for one NSE symbol between two dates "
                            "(YYYY-MM-DD) via Groww's Trade API — an explicit secondary source, "
                            "separate from Kite's price_history. Use only when the user asks to "
                            "cross-check against Groww; never as a silent fallback. Only the "
                            "'day' interval is supported."),
            "parameters": {"type": "object", "properties": {
                "symbol": {"type": "string"},
                "from_date": {"type": "string"}, "to_date": {"type": "string"},
                "interval": {"type": "string",
                            "description": "day (default) — the only supported value"}},
                "required": ["symbol", "from_date", "to_date"]},
        },
        "handler": handle_groww_price_history,
    },
    "market_overview": {
        "schema": {
            "name": "market_overview",
            "description": ("Composite Indian market snapshot: NIFTY 50 / BANK / SENSEX quotes, "
                            "sectoral index moves, and latest FII/DII provisional flows. "
                            "Start here for any 'how is the market doing?' question; combine with "
                            "web_search for macro news context."),
            "parameters": {"type": "object", "properties": {}},
        },
        "handler": handle_market_overview,
    },
    "fundamentals": {
        "schema": {
            "name": "fundamentals",
            "description": ("Structured fundamentals for an NSE symbol: valuation ratios, "
                            "profitability, growth, financial statements. Sourced from "
                            "screener.in with yfinance fallback; cached ~7 days."),
            "parameters": {"type": "object", "properties": {
                "symbol": {"type": "string", "description": "Exact NSE tradingsymbol"}},
                "required": ["symbol"]},
        },
        "handler": handle_fundamentals,
    },
    "market_events": {
        "schema": {
            "name": "market_events",
            "description": ("Company event feed for a symbol: regulatory filings (NSE+BSE), "
                            "corporate actions (dividends, splits, bonus, rights, buybacks), "
                            "board meetings, insider/PIT trades, and earnings-calendar entries. "
                            "Omit the symbol for just the upcoming earnings calendar."),
            "parameters": {"type": "object", "properties": {
                "symbol": {"type": "string"},
                "days_back": {"type": "integer", "description": "Lookback window (default 14)"}},
                "required": []},
        },
        "handler": handle_market_events,
    },
    "corporate_actions": {
        "schema": {
            "name": "corporate_actions",
            "description": ("Dividends, splits, bonus issues, rights, buybacks and scheme "
                            "filings for one symbol, plus scheduled board meetings (the "
                            "earliest warning of a results date). Sourced from NSE, with a "
                            "BSE-announcement fallback — check the 'source' field."),
            "parameters": {"type": "object", "properties": {
                "symbol": {"type": "string", "description": "Exact NSE tradingsymbol"},
                "days_back": {"type": "integer", "description": "Lookback window (default 90)"}},
                "required": ["symbol"]},
        },
        "handler": handle_corporate_actions,
    },
    "ownership_signals": {
        "schema": {
            "name": "ownership_signals",
            "description": ("Who is buying and selling: insider/PIT disclosures (with promoter "
                            "and pledge flags), bulk deals and block deals. Pass a symbol to "
                            "filter. An empty list means the feed had no data, not that nothing "
                            "happened — NSE's bulk-deals endpoint is currently down."),
            "parameters": {"type": "object", "properties": {
                "symbol": {"type": "string", "description": "Exact NSE tradingsymbol"},
                "period": {"type": "string", "description": "lastday | lastweek | lastmonth (default)"}},
                "required": []},
        },
        "handler": handle_ownership_signals,
    },
    "peer_comparison": {
        "schema": {
            "name": "peer_comparison",
            "description": ("Side-by-side valuation, profitability and growth for a symbol "
                            "against its sector peers, with the peer median and the symbol's "
                            "gap to it. Use before calling any stock cheap or expensive. Pass "
                            "an explicit peers list for names outside the built-in sector map."),
            "parameters": {"type": "object", "properties": {
                "symbol": {"type": "string", "description": "Exact NSE tradingsymbol"},
                "peers": {"type": "array", "items": {"type": "string"},
                          "description": "Optional explicit peer symbols (max 4 used)"}},
                "required": ["symbol"]},
        },
        "handler": handle_peer_comparison,
    },
    "technicals": {
        "schema": {
            "name": "technicals",
            "description": ("Price structure from daily OHLCV: 1w/1m/3m/6m/1y returns, 50/200 "
                            "DMA and cross state, distance from the 52-week high and low, "
                            "volume surge versus the 30-day average, and excess return over the "
                            "sector index. Context for whether the market has already moved on "
                            "the story — not a trading signal."),
            "parameters": {"type": "object", "properties": {
                "symbol": {"type": "string", "description": "Exact NSE tradingsymbol"}},
                "required": ["symbol"]},
        },
        "handler": handle_technicals,
    },
    "portfolio_status": {
        "schema": {
            "name": "portfolio_status",
            "description": ("Current holdings with live P&L, available cash, total portfolio "
                            "value — each position joined with the user's thesis note."),
            "parameters": {"type": "object", "properties": {}},
        },
        "handler": handle_portfolio_status,
    },
    "order_book": {
        "schema": {
            "name": "order_book",
            "description": "Today's orders with statuses, plus active GTT stop/target triggers.",
            "parameters": {"type": "object", "properties": {}},
        },
        "handler": handle_order_book,
    },
    "thesis_note": {
        "schema": {
            "name": "thesis_note",
            "description": ("Save/update your investment thesis for a symbol; shown alongside "
                            "the position in portfolio_status."),
            "parameters": {"type": "object", "properties": {
                "symbol": {"type": "string"},
                "thesis": {"type": "string", "description": "empty string removes the note"}},
                "required": ["symbol", "thesis"]},
        },
        "handler": handle_thesis_note,
    },
    "watchlist_add": {
        "schema": {
            "name": "watchlist_add",
            "description": "Add a symbol to your watchlist for tracking price changes and NSE filings.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "NSE trading symbol"},
                    "note": {"type": "string", "description": "Optional note (e.g., 'IT recovery play')"},
                },
                "required": ["symbol"],
            },
        },
        "handler": handle_watchlist_add,
    },
    "watchlist_remove": {
        "schema": {
            "name": "watchlist_remove",
            "description": "Remove a symbol from your watchlist.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "NSE trading symbol"},
                },
                "required": ["symbol"],
            },
        },
        "handler": handle_watchlist_remove,
    },
    "watchlist_status": {
        "schema": {
            "name": "watchlist_status",
            "description": ("What changed on the watchlist since last check: price moves vs baseline, "
                            "new NSE filings. Call only when the user explicitly asks about their "
                            "watchlist (e.g. 'what's new', 'anything to report') — never on a bare "
                            "greeting."),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
        "handler": handle_watchlist_status,
    },
    "web_search": {
        "schema": {
            "name": "web_search",
            "description": ("Search the web via DuckDuckGo for news and macro context. Results "
                            "come back ranked by source quality — exchanges and regulators "
                            "first, then mainstream financial press, then everything else, "
                            "forums and social last — each tagged with domain, source_tier and "
                            "a date_hint where one could be recovered. Weight your claims by "
                            "that tier."),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "limit": {"type": "integer", "description": "Max results (default 5)"},
                    "days": {"type": "integer", "description": "Only results from the last N days — use it, most stock news goes stale fast"},
                },
                "required": ["query"],
            },
        },
        "handler": handle_web_search,
    },
    "web_fetch": {
        "schema": {
            "name": "web_fetch",
            "description": ("Fetch a URL and return readable text plus its domain, source_tier "
                            "and published date when the page declares one."),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to fetch"},
                },
                "required": ["url"],
            },
        },
        "handler": handle_web_fetch,
    },
    "memory_save": {
        "schema": {
            "name": "memory_save",
            "description": ("Save a durable fact about the user (risk profile, preferences, goals). "
                            "Use sparingly, only for things worth remembering across sessions."),
            "parameters": {
                "type": "object",
                "properties": {
                    "note": {"type": "string", "description": "Fact to remember"},
                },
                "required": ["note"],
            },
        },
        "handler": handle_memory_save,
    },
    "deep_research": {
        "schema": {
            "name": "deep_research",
            "description": ("Full multi-pass research on one NSE symbol: a facts pass, a narrative "
                            "pass, then an adversarial bear pass that attacks both, synthesised "
                            "into a sectioned report with a verdict and confidence. This is the "
                            "tool for 'analyse X' / 'should I look at X' / comparisons (one call "
                            "per symbol). It is slow and expensive — never use it for a single "
                            "quote or ratio."),
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Exact NSE tradingsymbol"},
                    "brief": {"type": "string", "description": "Optional extra focus, e.g. 'the user cares about the margin trend and promoter pledges'"},
                    "depth": {"type": "string", "description": "full (default, 3 passes) | brief (facts + bear only, faster)"},
                },
                "required": ["symbol"],
            },
        },
        "handler": _lazy_deep_research,
    },
    "research_history": {
        "schema": {
            "name": "research_history",
            "description": ("Past deep_research reports saved for a symbol, newest first, with "
                            "their date and verdict. Check this before running deep_research so "
                            "you can talk about what changed instead of starting from scratch."),
            "parameters": {"type": "object", "properties": {
                "symbol": {"type": "string", "description": "Exact NSE tradingsymbol"}},
                "required": ["symbol"]},
        },
        "handler": _lazy_research_history,
    },
    "research_get": {
        "schema": {
            "name": "research_get",
            "description": ("Read back one saved research report in full (sections + verdict). "
                            "n=1 is the most recent; use research_history to see what exists."),
            "parameters": {"type": "object", "properties": {
                "symbol": {"type": "string", "description": "Exact NSE tradingsymbol"},
                "n": {"type": "integer", "description": "1 = latest (default)"}},
                "required": ["symbol"]},
        },
        "handler": _lazy_research_get,
    },
}
