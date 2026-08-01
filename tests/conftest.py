"""Hermetic test env: no real creds, deterministic TZ, isolated LEX_HOME."""
import os

import pytest

_CRED_SUFFIXES = ("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD", "_SECRET_KEY")

os.environ["TZ"] = "UTC"


@pytest.fixture(autouse=True)
def _no_real_creds(monkeypatch, tmp_path):
    for k in list(os.environ):
        if k.endswith(_CRED_SUFFIXES) or k.startswith("KITE_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("LEX_HOME", str(tmp_path / "lexhome"))
