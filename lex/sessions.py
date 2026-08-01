"""Conversation history: one JSONL file per session, append-only."""
import json
import uuid
from datetime import datetime
from pathlib import Path

from lex.paths import lex_home


def _dir() -> Path:
    d = lex_home() / "sessions"
    d.mkdir(exist_ok=True)
    return d


def new_session() -> Path:
    # microsecond resolution (not just seconds) so sessions created back-to-back
    # still sort newest-first by filename; uuid suffix remains as a tiebreak.
    name = datetime.now().strftime("%Y%m%d-%H%M%S%f") + f"-{uuid.uuid4().hex[:6]}.jsonl"
    p = _dir() / name
    p.touch()
    return p


def append(path: Path, msg: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(msg, default=str) + "\n")


def load(path: Path) -> list:
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def list_sessions(n: int = 10) -> list:
    return sorted(_dir().glob("*.jsonl"), reverse=True)[:n]
