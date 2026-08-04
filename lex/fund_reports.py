"""Saved mutual fund research reports: one directory per scheme under lex_home().

Mirrors lex/reports.py — see that module's docstring for the flat-file
rationale. The fund section schema has no list-valued sections (no
risks/catalysts array the way an equity report does — risk considerations
live in the risk_exit_load markdown section instead), so rendering is a
subset of reports.py's.
"""
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from lex.fund_research import SECTIONS
from lex.paths import lex_home

logger = logging.getLogger(__name__)

_UNSAFE = re.compile(r"[^A-Z0-9._-]")


def _dir(scheme_code: str) -> Path:
    d = lex_home() / "mf_research" / _UNSAFE.sub("_", str(scheme_code).strip().upper())
    d.mkdir(parents=True, exist_ok=True)
    return d


def save(report: dict) -> Path:
    stem = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S%f")
    d = _dir(report["scheme_code"])
    path = d / f"{stem}.json"
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    (d / f"{stem}.md").write_text(render(report), encoding="utf-8")
    return path


def _files(scheme_code: str) -> list[Path]:
    return sorted(_dir(scheme_code).glob("*.json"), reverse=True)


def load(scheme_code: str, n: int = 1) -> dict | None:
    """The nth most recent report for `scheme_code` (1 = latest), or None."""
    files = _files(scheme_code)
    if n < 1 or n > len(files):
        return None
    try:
        return json.loads(files[n - 1].read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.exception("unreadable fund report %s", files[n - 1])
        return None


def history(scheme_code: str, limit: int = 10) -> list[dict]:
    """Newest-first digest of past reports: date, depth, verdict."""
    out = []
    for i in range(1, min(limit, len(_files(scheme_code))) + 1):
        report = load(scheme_code, i)
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


def researched_schemes() -> list[str]:
    """Scheme codes with at least one saved report, alphabetically."""
    root = lex_home() / "mf_research"
    if not root.is_dir():
        return []
    return sorted(d.name for d in root.iterdir()
                  if d.is_dir() and any(d.glob("*.json")))


def delta_context(scheme_code: str) -> tuple[str, str | None]:
    """Prompt context asking what changed since the last report, if there is one."""
    prior = load(scheme_code)
    if prior is None:
        return "", None
    verdict = prior.get("sections", {}).get("verdict") or {}
    when = prior.get("generated_at") or "an earlier session"
    lines = [f"You already researched scheme {scheme_code} on {when}. That report "
             f"concluded: {verdict.get('stance', 'unknown')} "
             f"(confidence {verdict.get('confidence', 'unknown')})."]
    if verdict.get("summary"):
        lines.append(f"Its summary was: {verdict['summary']}")
    watch = verdict.get("what_would_change_my_mind") or []
    if watch:
        lines.append("It said these would change the view: " + "; ".join(map(str, watch)))
    lines.append("This run is an UPDATE. Concentrate on what has changed since then — "
                 "NAV/performance moves, manager or mandate changes, expense-ratio "
                 "revisions, whether any of the above triggers fired — and say "
                 "explicitly whether the verdict moves and why. Re-state settled "
                 "background only where it is needed to make the change legible.")
    return "\n".join(lines), prior.get("generated_at")


_TITLES = {"category": "Category", "expense_ratio": "Expense ratio",
           "performance": "Performance", "portfolio_composition": "Portfolio composition",
           "fund_manager": "Fund manager", "risk_exit_load": "Risk & exit load"}

# Same fact-vs-interpretation split as reports.py, adapted to the fund schema:
# category/expense_ratio/portfolio_composition are close to what a tool
# returned; performance/fund_manager/risk_exit_load lean on judgement.
_GROUPS = (("Facts", ("category", "expense_ratio", "portfolio_composition")),
           ("Interpretation", ("performance", "fund_manager", "risk_exit_load")))


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
    out = [f"# {report.get('scheme_code', '?')} — {stance} (confidence: {confidence})"]

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

    judgement = _judgement(verdict, mode)
    if judgement:
        if mode != "brief":
            out += ["", "## Judgement"]
        out += judgement

    if sections.get("_raw"):
        out += ["", "## Unstructured synthesis output", str(sections["_raw"])]
    return "\n".join(out).strip() + "\n"


def _judgement(verdict: dict, mode: str) -> list[str]:
    heading = "##" if mode == "brief" else "###"
    out: list[str] = []
    if mode != "brief":
        drivers = verdict.get("drivers") or []
        if drivers:
            out += ["", f"{heading} Verdict drivers"] + [f"- {d}" for d in drivers]
    watch = verdict.get("what_would_change_my_mind") or []
    if watch:
        out += ["", f"{heading} What would change my mind"] + [f"- {w}" for w in watch]
    return out


def handle_fund_research_history(args: dict) -> str:
    from lex.tools.common import _ok, _err
    try:
        scheme_code = str(args["scheme_code"]).strip()
        return _ok({"scheme_code": scheme_code, "reports": history(scheme_code)})
    except Exception as e:
        return _err(e)


def handle_fund_research_get(args: dict) -> str:
    from lex.tools.common import _ok, _err
    try:
        scheme_code = str(args["scheme_code"]).strip()
        report = load(scheme_code, int(args.get("n", 1)))
        if report is None:
            return _err(f"no saved report #{args.get('n', 1)} for {scheme_code}")
        return _ok({k: v for k, v in report.items() if k != "passes"})
    except Exception as e:
        return _err(e)
