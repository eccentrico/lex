"""Structural quality checks on a finished research report.

This does not judge whether a verdict is *right* — nothing offline can. It
judges whether the report is the shape a usable report has: every pillar
either filled or explicitly declared missing, claims carrying source tags, a
bear pass that engaged with what the earlier passes actually said, and a
verdict that commits to a stance, a confidence and a falsifier.

Cheap enough to run on every report, so `run_and_save` does — findings land in
lex.log, where a pattern of the same gap week after week is visible.
"""
import re

from lex.research import SECTIONS

_PROSE = tuple(s for s in SECTIONS if s not in ("risks", "catalysts", "verdict"))
_LEVELS = {"high", "medium", "low"}
_STANCES = {"bullish", "neutral", "bearish"}

_SOURCE_TAG = re.compile(r"\[[^\]]{2,}\]")
# 2+ digit runs, optionally decimal/percent: the kind of number a bear pass has
# to engage with. Single digits match too much prose ("3 risks", "Q1").
_FIGURE = re.compile(r"\d[\d,]*\.?\d*%?")
_DELTA_WORDS = ("chang", "since", "update", "unchanged", "new ", "no longer",
                "previously", "last time", "moved")
_MIN_SOURCE_TAGS = 3
_MIN_RISKS = 3


def score_report(report: dict) -> dict:
    """{"score", "checks_passed", "checks_total", "findings"} — never raises."""
    findings: list[str] = []
    checks = (_check_sections, _check_evidence, _check_risks, _check_catalysts,
              _check_verdict, _check_bear_engaged, _check_delta_shape)
    passed = 0
    for check in checks:
        try:
            problems = check(report) or []
        except Exception as e:  # a scorer bug must never break a research run
            problems = [f"{check.__name__} could not run: {e}"]
        if problems:
            findings += problems
        else:
            passed += 1
    return {"score": round(passed / len(checks), 2), "checks_passed": passed,
            "checks_total": len(checks), "findings": findings}


def _sections(report: dict) -> dict:
    return report.get("sections") or {}


def _check_sections(report) -> list[str]:
    sections = _sections(report)
    missing = [name for name in _PROSE
               if not str(sections.get(name) or "").strip()]
    if missing:
        return [f"sections absent entirely (not even declared unknown): "
                f"{', '.join(missing)}"]
    if sections.get("_raw"):
        return ["synthesis did not return structured output"]
    return []


def _check_evidence(report) -> list[str]:
    sections = _sections(report)
    tagged = sum(len(_SOURCE_TAG.findall(str(sections.get(name) or "")))
                 for name in _PROSE)
    if tagged < _MIN_SOURCE_TAGS:
        return [f"only {tagged} source-tagged claims across the report — "
                f"expected at least {_MIN_SOURCE_TAGS}"]
    unsourced = [name for name in _PROSE
                 if _FIGURE.search(str(sections.get(name) or ""))
                 and not _SOURCE_TAG.search(str(sections.get(name) or ""))
                 and not str(sections.get(name) or "").strip().lower().startswith("unknown")]
    if unsourced:
        return [f"sections quoting numbers with no source tag: {', '.join(unsourced)}"]
    return []


def _check_risks(report) -> list[str]:
    risks = _sections(report).get("risks") or []
    if len(risks) < _MIN_RISKS:
        return [f"only {len(risks)} risks listed — expected at least {_MIN_RISKS}"]
    ungraded = [r.get("risk", "?") for r in risks if not isinstance(r, dict)
                or str(r.get("likelihood", "")).lower() not in _LEVELS
                or str(r.get("impact", "")).lower() not in _LEVELS]
    if ungraded:
        return [f"risks without a likelihood/impact grade: {ungraded}"]
    return []


def _check_catalysts(report) -> list[str]:
    catalysts = _sections(report).get("catalysts") or []
    if not catalysts:
        return ["no catalysts — nothing said about what could move this"]
    untimed = [c.get("catalyst", "?") for c in catalysts
               if not isinstance(c, dict) or not str(c.get("timing", "")).strip()]
    return [f"catalysts with no timing: {untimed}"] if untimed else []


def _check_verdict(report) -> list[str]:
    verdict = _sections(report).get("verdict") or {}
    problems = []
    if str(verdict.get("stance", "")).lower() not in _STANCES:
        problems.append(f"verdict stance is not one of {sorted(_STANCES)}")
    if str(verdict.get("confidence", "")).lower() not in _LEVELS:
        problems.append("verdict carries no confidence level")
    if not str(verdict.get("summary", "")).strip():
        problems.append("verdict has no one-paragraph summary")
    if not (verdict.get("drivers") or []):
        problems.append("verdict names no drivers")
    if not (verdict.get("what_would_change_my_mind") or []):
        problems.append("verdict is unfalsifiable — nothing would change its mind")
    return problems


def _check_bear_engaged(report) -> list[str]:
    """The bear pass has to attack this company, not listed equities in general."""
    passes = report.get("passes") or {}
    bear = str(passes.get("bear") or "")
    if not bear:
        return []  # brief runs and reloaded reports keep no transcripts
    earlier = " ".join(str(passes.get(name) or "")
                       for name in ("facts", "narrative"))
    if not earlier.strip():
        return []
    figures = {f for f in _FIGURE.findall(earlier) if len(f) > 2}
    if figures and not (figures & set(_FIGURE.findall(bear))):
        return ["bear pass cites none of the figures the earlier passes "
                "established — likely generic risk boilerplate"]
    return []


def _check_delta_shape(report) -> list[str]:
    if not report.get("delta_of"):
        return []
    sections = _sections(report)
    verdict = sections.get("verdict") or {}
    # only prose the model wrote — stringifying the dicts would match on their
    # own key names ("what_would_change_my_mind" contains "chang")
    parts = [str(sections.get(name) or "") for name in _PROSE]
    parts.append(str(verdict.get("summary") or ""))
    parts += [str(d) for d in (verdict.get("drivers") or [])]
    parts += [str(r.get("risk", "")) if isinstance(r, dict) else str(r)
              for r in (sections.get("risks") or [])]
    text = " ".join(parts).lower()
    if not any(word in text for word in _DELTA_WORDS):
        return ["update run reads like a fresh report — it never says what changed"]
    return []
