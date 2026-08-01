"""Lex home directory, as seen from services/.

Duplicated (not imported) from lex/paths.py: services/ must stay
self-contained and not depend on the lex/ package (services is the lower
layer; lex/tools imports services, not the reverse). Both must resolve to
the same directory since kite tokens, the finance cache DB, etc. are
shared state.
"""
import os
from pathlib import Path


def lex_home() -> Path:
    p = Path(os.environ.get("LEX_HOME", "~/.lex")).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p
