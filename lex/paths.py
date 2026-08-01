"""Lex home directory. All state (memory, sessions, watchlist, theses) lives here."""
import os
from pathlib import Path


def lex_home() -> Path:
    p = Path(os.environ.get("LEX_HOME", "~/.lex")).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p
