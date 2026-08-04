"""Multi-pass mutual fund research: facts -> narrative -> bear -> parent synthesis.

Mirrors lex/research.py's pipeline shape (see that module's docstring for why
the bear pass is split from the narrative pass). Funds get their own section
schema here — there is no moat/promoter/peer-multiple analogue for a fund; see
docs/superpowers/specs/2026-08-04-mutual-fund-support-design.md.
"""
import json
import logging
import re
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

SECTIONS = ("category", "expense_ratio", "performance", "portfolio_composition",
            "fund_manager", "risk_exit_load", "verdict")

DEPTH_PASSES = {"brief": ("facts", "bear"),
                "full": ("facts", "narrative", "bear")}


def run_research(scheme_code: str, brief: str = "", depth: str = "full",
                 progress=None, context: str = "") -> dict:
    """Run the passes for `scheme_code` and return a structured report dict.

    Mirrors lex.research.run_research; see that function's docstring for the
    `progress`/`context` contract. Each stage is dispatched through
    delegate.run_pass with a "fund_" prefixed pass_type (fund_facts,
    fund_narrative, fund_bear) so lex.prompt resolves the fund-specific
    prompts, but is stored in `passes` under the unprefixed stage name
    ("facts", "narrative", "bear") so _pass_brief's earlier-pass lookup stays
    identical to the equity pipeline's.
    """
    from lex import delegate
    scheme_code = str(scheme_code).strip()
    stages = DEPTH_PASSES.get(depth) or DEPTH_PASSES["full"]
    passes: dict[str, str] = {}
    for stage in stages:
        _note(progress, stage)
        passes[stage] = delegate.run_pass(
            _pass_brief(stage, scheme_code, brief, context, passes), f"fund_{stage}",
            tools=delegate.RESEARCH_FUND_TOOL_NAMES)
    _note(progress, "synthesis")
    sections = _synthesize(scheme_code, brief, context, passes)
    return {
        "scheme_code": scheme_code,
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


def _pass_brief(stage: str, scheme_code: str, brief: str, context: str,
                passes: dict) -> str:
    parts = [f"Scheme code: {scheme_code} (AMFI)."]
    if brief:
        parts.append(f"The user specifically wants: {brief}")
    if context:
        parts.append(context)
    for earlier in ("facts", "narrative"):
        if stage != earlier and earlier in passes:
            parts.append(f"### {earlier.upper()} PASS OUTPUT\n{passes[earlier]}")
    return "\n\n".join(parts)


def _synthesize(scheme_code: str, brief: str, context: str, passes: dict) -> dict:
    from lex import agent, llm, prompt
    body = "\n\n".join(f"### {k.upper()} PASS\n{v}" for k, v in passes.items())
    user = f"Scheme code: {scheme_code} (AMFI)."
    if brief:
        user += f"\nThe user specifically wants: {brief}"
    if context:
        user += f"\n{context}"
    messages = [{"role": "system", "content": prompt.FUND_SYNTHESIS_PROMPT},
                {"role": "user", "content": f"{user}\n\n{body}"}]
    text = agent.run(llm.make_client(), llm.default_model(), messages, {})
    return _parse_sections(text)


def _parse_sections(text: str) -> dict:
    """Coerce the synthesis reply into the section schema; never raise."""
    parsed = _json_object(text)
    if parsed is None:
        logger.warning("fund synthesis did not return JSON — keeping raw text")
        sections = {s: "unknown — synthesis returned unstructured output"
                    for s in SECTIONS}
        sections["verdict"] = {}
        sections["_raw"] = text
        return sections
    sections = {}
    for name in SECTIONS:
        value = parsed.get(name)
        if name == "verdict":
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


def run_and_save(scheme_code: str, brief: str = "", depth: str = "full",
                 progress=None) -> dict:
    """run_research, but picking up where the last report on this scheme left
    off and writing the result to disk. Shared by the tool handler."""
    from lex import fund_reports
    context, prior_at = fund_reports.delta_context(scheme_code)
    report = run_research(scheme_code, brief=brief, depth=depth, progress=progress,
                          context=context)
    report["delta_of"] = prior_at
    try:
        report["saved_to"] = str(fund_reports.save(report))
    except OSError as e:  # a full disk shouldn't throw away a finished run
        logger.exception("could not save fund report for %s", scheme_code)
        report["saved_to"] = None
        report["save_error"] = str(e)
    return report


def handle_fund_research(args: dict) -> str:
    from lex.tools.common import _ok, _err
    try:
        report = run_and_save(args["scheme_code"], brief=args.get("brief", ""),
                              depth=args.get("depth", "full"))
        return _ok({k: v for k, v in report.items() if k != "passes"})
    except Exception as e:
        return _err(e)
