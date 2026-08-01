"""Multi-pass equity research: facts -> narrative -> bear -> parent synthesis.

One `delegate_research` call gave one subagent one shot at everything, so the
bear case was written by the same context that had just argued the bull case.
Splitting it means the bear pass reads the earlier passes as *text it has to
attack*, and the parent adjudicates instead of averaging.
"""
import json
import logging
import re
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

SECTIONS = ("business", "financials", "filings_events", "news", "valuation",
            "technicals", "ownership", "risks", "catalysts", "verdict")

_LIST_SECTIONS = ("risks", "catalysts")

# "brief" drops the narrative pass — two LLM passes to answer "is this worth my
# attention", three when the answer feeds a real decision.
DEPTH_PASSES = {"brief": ("facts", "bear"),
                "full": ("facts", "narrative", "bear")}


def run_research(symbol: str, brief: str = "", depth: str = "full",
                 progress=None, context: str = "") -> dict:
    """Run the passes for `symbol` and return a structured report dict.

    `progress` is called with each stage name ("facts", ..., "synthesis") before
    that stage starts, so a caller can show what's happening during a slow run.
    `context` is prepended to every pass brief (Phase 2 uses it for the prior
    report's verdict when asking what changed).
    """
    from lex import delegate
    symbol = symbol.strip().upper()
    stages = DEPTH_PASSES.get(depth) or DEPTH_PASSES["full"]
    passes: dict[str, str] = {}
    for stage in stages:
        _note(progress, stage)
        passes[stage] = delegate.run_pass(
            _pass_brief(stage, symbol, brief, context, passes), stage)
    _note(progress, "synthesis")
    sections = _synthesize(symbol, brief, context, passes)
    return {
        "symbol": symbol,
        "brief": brief,
        "depth": "brief" if stages == DEPTH_PASSES["brief"] else "full",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sections": sections,
        "passes": passes,
    }


def _note(progress, stage: str) -> None:
    if progress is None:
        return
    try:
        progress(stage)
    except Exception:  # a progress display must never kill a research run
        logger.exception("progress callback failed for stage %s", stage)


def _pass_brief(stage: str, symbol: str, brief: str, context: str,
                passes: dict) -> str:
    parts = [f"Symbol: {symbol} (NSE)."]
    if brief:
        parts.append(f"The user specifically wants: {brief}")
    if context:
        parts.append(context)
    for earlier in ("facts", "narrative"):
        if stage != earlier and earlier in passes:
            parts.append(f"### {earlier.upper()} PASS OUTPUT\n{passes[earlier]}")
    return "\n\n".join(parts)


def _synthesize(symbol: str, brief: str, context: str, passes: dict) -> dict:
    from lex import agent, llm, prompt
    body = "\n\n".join(f"### {k.upper()} PASS\n{v}" for k, v in passes.items())
    user = f"Symbol: {symbol} (NSE)."
    if brief:
        user += f"\nThe user specifically wants: {brief}"
    if context:
        user += f"\n{context}"
    messages = [{"role": "system", "content": prompt.SYNTHESIS_PROMPT},
                {"role": "user", "content": f"{user}\n\n{body}"}]
    # no tools: everything this call needs is already in the passes above, and a
    # synthesis that goes fetching is a synthesis that skipped reading them.
    text = agent.run(llm.make_client(), llm.default_model(), messages, {})
    return _parse_sections(text)


def _parse_sections(text: str) -> dict:
    """Coerce the synthesis reply into the section schema; never raise."""
    parsed = _json_object(text)
    if parsed is None:
        logger.warning("synthesis did not return JSON — keeping raw text")
        sections = {s: "unknown — synthesis returned unstructured output"
                    for s in SECTIONS}
        sections.update({s: [] for s in _LIST_SECTIONS})
        sections["verdict"] = {}
        sections["_raw"] = text
        return sections
    sections = {}
    for name in SECTIONS:
        value = parsed.get(name)
        if name in _LIST_SECTIONS:
            sections[name] = value if isinstance(value, list) else []
        elif name == "verdict":
            sections[name] = value if isinstance(value, dict) else {}
        else:
            sections[name] = value if isinstance(value, str) and value.strip() \
                else "unknown — not established by the research passes"
    return sections


def _json_object(text: str):
    """First JSON object in `text`, tolerating ``` fences and surrounding prose."""
    if not text:
        return None
    candidate = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)```", candidate, re.S)
    if fenced:
        candidate = fenced.group(1).strip()
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(candidate[start:end + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def run_and_save(symbol: str, brief: str = "", depth: str = "full",
                 progress=None) -> dict:
    """run_research, but picking up where the last report on this symbol left
    off and writing the result to disk. Shared by the tool and the REPL."""
    from lex import reports
    from lex import quality
    context, prior_at = reports.delta_context(symbol)
    report = run_research(symbol, brief=brief, depth=depth, progress=progress,
                          context=context)
    report["delta_of"] = prior_at
    report["quality"] = quality.score_report(report)
    for finding in report["quality"]["findings"]:
        logger.warning("research quality (%s): %s", report["symbol"], finding)
    try:
        report["saved_to"] = str(reports.save(report))
    except OSError as e:  # a full disk shouldn't throw away a finished run
        logger.exception("could not save report for %s", symbol)
        report["saved_to"] = None
        report["save_error"] = str(e)
    return report


def handle_deep_research(args: dict) -> str:
    from lex.tools.common import _ok, _err
    try:
        report = run_and_save(args["symbol"], brief=args.get("brief", ""),
                              depth=args.get("depth", "full"))
        # the pass transcripts are on disk; the model gets the distilled report
        return _ok({k: v for k, v in report.items() if k != "passes"})
    except Exception as e:
        return _err(e)
