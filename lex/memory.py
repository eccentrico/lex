"""Persistent memory: one markdown file, read into the system prompt."""
from datetime import date

from lex.paths import lex_home


def memory_path():
    return lex_home() / "memory.md"


def read() -> str:
    p = memory_path()
    return p.read_text(encoding="utf-8") if p.exists() else ""


def handle_memory_save(args: dict) -> str:
    # lazy import: lex.tools.common is under lex/tools, whose __init__ imports
    # handle_memory_save from this module — a top-level import here would cycle.
    from lex.tools.common import _ok, _err
    try:
        note = args["note"].strip()
        with memory_path().open("a", encoding="utf-8") as f:
            f.write(f"- {date.today().isoformat()}: {note}\n")
        return _ok({"saved": note})
    except Exception as e:
        return _err(e)
