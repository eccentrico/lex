"""Saved research reports: one directory per symbol under lex_home().

Flat files, same reasoning as sessions/watchlist — single user, greppable, and
the rendered .md sibling means a report is readable without going through Lex.
"""
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from lex.paths import lex_home
from lex.research import SECTIONS

logger = logging.getLogger(__name__)

_UNSAFE = re.compile(r"[^A-Z0-9._-]")


def _dir(symbol: str) -> Path:
    # M&M, BAJAJ-AUTO, 360ONE: symbols are not all path-safe, so fold anything
    # outside the safe set to "_". Deterministic, so lookups still find them.
    d = lex_home() / "research" / _UNSAFE.sub("_", symbol.strip().upper())
    d.mkdir(parents=True, exist_ok=True)
    return d


def save(report: dict) -> Path:
    stem = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S%f")
    d = _dir(report["symbol"])
    path = d / f"{stem}.json"
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    (d / f"{stem}.md").write_text(render(report), encoding="utf-8")
    return path


def _files(symbol: str) -> list[Path]:
    return sorted(_dir(symbol).glob("*.json"), reverse=True)


def load(symbol: str, n: int = 1) -> dict | None:
    """The nth most recent report for `symbol` (1 = latest), or None."""
    files = _files(symbol)
    if n < 1 or n > len(files):
        return None
    try:
        return json.loads(files[n - 1].read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.exception("unreadable report %s", files[n - 1])
        return None


def history(symbol: str, limit: int = 10) -> list[dict]:
    """Newest-first digest of past reports: date, depth, verdict."""
    out = []
    for i in range(1, min(limit, len(_files(symbol))) + 1):
        report = load(symbol, i)
        if report is None:
            continue
        verdict = report.get("sections", {}).get("verdict") or {}
        out.append({"n": i, "generated_at": report.get("generated_at"),
                    "age": age(report.get("generated_at")),
                    "depth": report.get("depth"),
                    "stance": verdict.get("stance"),
                    "confidence": verdict.get("confidence"),
                    "summary": verdict.get("summary")})
    return out


def researched_symbols() -> list[str]:
    """Symbols with at least one saved report, alphabetically."""
    root = lex_home() / "research"
    if not root.is_dir():
        return []
    return sorted(d.name for d in root.iterdir()
                  if d.is_dir() and any(d.glob("*.json")))


def delta_context(symbol: str) -> tuple[str, str | None]:
    """Prompt context asking what changed since the last report, if there is one.

    Returns (context, prior_generated_at); ("", None) on a first look at a symbol.
    """
    prior = load(symbol)
    if prior is None:
        return "", None
    verdict = prior.get("sections", {}).get("verdict") or {}
    when = prior.get("generated_at") or "an earlier session"
    lines = [f"You already researched {symbol} on {when}. That report concluded: "
             f"{verdict.get('stance', 'unknown')} "
             f"(confidence {verdict.get('confidence', 'unknown')})."]
    if verdict.get("summary"):
        lines.append(f"Its summary was: {verdict['summary']}")
    watch = verdict.get("what_would_change_my_mind") or []
    if watch:
        lines.append("It said these would change the view: " + "; ".join(map(str, watch)))
    lines.append("This run is an UPDATE. Concentrate on what has changed since "
                 "then — new filings and events, estimate revisions, the price "
                 "move, whether any of the above triggers fired — and say "
                 "explicitly whether the verdict moves and why. Re-state settled "
                 "background only where it is needed to make the change legible.")
    return "\n".join(lines), prior.get("generated_at")


_TITLES = {"business": "Business & moat", "financials": "Financials",
           "filings_events": "Filings & events", "news": "News",
           "valuation": "Valuation", "technicals": "Technicals",
           "ownership": "Ownership & flows"}

# A reader has to be able to tell what was fetched from what was concluded, so
# the sections are grouped by how much they lean on judgement rather than data.
_GROUPS = (("Facts", ("financials", "filings_events", "ownership", "technicals")),
           ("Interpretation", ("business", "valuation", "news")))


def age(generated_at: str | None) -> str | None:
    """"today" / "3 days ago" — how stale the thing you're reading is."""
    if not generated_at:
        return None
    try:
        then = datetime.fromisoformat(str(generated_at))
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    days = (datetime.now(timezone.utc) - then).days
    if days < 0:
        return None
    return "today" if days == 0 else "1 day ago" if days == 1 else f"{days} days ago"


def render(report: dict, mode: str = "full") -> str:
    """Markdown for a stored report. brief = the answer; full = the whole file."""
    sections = report.get("sections") or {}
    verdict = sections.get("verdict") or {}
    stance = verdict.get("stance", "no verdict")
    confidence = verdict.get("confidence", "unknown")
    out = [f"# {report.get('symbol', '?')} — {stance} (confidence: {confidence})"]

    meta = [m for m in (report.get("generated_at"), age(report.get("generated_at")),
                        f"update on {report['delta_of']}" if report.get("delta_of") else None,
                        f"{report['depth']} run" if report.get("depth") else None)
            if m]
    if meta:
        out.append("*" + " · ".join(str(m) for m in meta) + "*")
    if verdict.get("summary"):
        out += ["", verdict["summary"]]

    if mode != "brief":
        for group, names in _GROUPS:
            bodies = [(name, sections.get(name)) for name in names if sections.get(name)]
            if not bodies:
                continue
            out += ["", f"## {group}"]
            for name, body in bodies:
                out += ["", f"### {_TITLES[name]}", str(body)]

    judgement = _judgement(sections, verdict, mode)
    if judgement:
        if mode != "brief":
            out += ["", "## Judgement"]
        out += judgement

    if sections.get("_raw"):
        out += ["", "## Unstructured synthesis output", str(sections["_raw"])]
    return "\n".join(out).strip() + "\n"


def _judgement(sections: dict, verdict: dict, mode: str) -> list[str]:
    brief = mode == "brief"
    heading = "##" if brief else "###"
    out: list[str] = []

    risks = sections.get("risks") or []
    if risks:
        out += ["", f"{heading} {'Top risks' if brief else 'Risks'}"]
        out += [_bullet_risk(r) for r in (risks[:3] if brief else risks)]

    if not brief:
        catalysts = sections.get("catalysts") or []
        if catalysts:
            out += ["", f"{heading} Catalysts"] + [_bullet_catalyst(c) for c in catalysts]
        drivers = verdict.get("drivers") or []
        if drivers:
            out += ["", f"{heading} Verdict drivers"] + [f"- {d}" for d in drivers]

    watch = verdict.get("what_would_change_my_mind") or []
    if watch:
        out += ["", f"{heading} What would change my mind"] + [f"- {w}" for w in watch]
    return out


def _bullet_risk(r) -> str:
    if not isinstance(r, dict):
        return f"- {r}"
    return (f"- {r.get('risk', 'unknown')} "
            f"(likelihood {r.get('likelihood', '?')}, impact {r.get('impact', '?')})")


def _bullet_catalyst(c) -> str:
    if not isinstance(c, dict):
        return f"- {c}"
    return (f"- {c.get('catalyst', 'unknown')} — {c.get('timing', 'timing unknown')} "
            f"({c.get('direction', 'direction unknown')})")


def handle_research_history(args: dict) -> str:
    from lex.tools.common import _ok, _err
    try:
        symbol = args["symbol"].strip().upper()
        return _ok({"symbol": symbol, "reports": history(symbol)})
    except Exception as e:
        return _err(e)


def handle_research_get(args: dict) -> str:
    from lex.tools.common import _ok, _err
    try:
        symbol = args["symbol"].strip().upper()
        report = load(symbol, int(args.get("n", 1)))
        if report is None:
            return _err(f"no saved report #{args.get('n', 1)} for {symbol}")
        # the raw pass transcripts are large and already distilled into the
        # sections — the model gets the report, not the working notes.
        return _ok({k: v for k, v in report.items() if k != "passes"})
    except Exception as e:
        return _err(e)
