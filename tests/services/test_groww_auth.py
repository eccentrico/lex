import json

import pytest

from services import groww_auth


def test_get_or_renew_access_token_requires_credentials(monkeypatch):
    monkeypatch.setattr(groww_auth, "GROWW_API_KEY", None)
    monkeypatch.setattr(groww_auth, "GROWW_API_SECRET", None)
    with pytest.raises(RuntimeError, match="not set"):
        groww_auth.get_or_renew_access_token()


def test_get_or_renew_access_token_saves_token_file(monkeypatch, tmp_path):
    monkeypatch.setattr(groww_auth, "GROWW_API_KEY", "key123")
    monkeypatch.setattr(groww_auth, "GROWW_API_SECRET", "secret123")
    seen = {}

    def fake_get_access_token(api_key, secret):
        seen["api_key"] = api_key
        seen["secret"] = secret
        return "fake-token-abc"

    monkeypatch.setattr(groww_auth.GrowwAPI, "get_access_token",
                        staticmethod(fake_get_access_token))
    token_path = tmp_path / "groww_tokens.json"
    monkeypatch.setenv("GROWW_TOKENS_PATH", str(token_path))

    token = groww_auth.get_or_renew_access_token()

    assert token == "fake-token-abc"
    assert seen == {"api_key": "key123", "secret": "secret123"}
    saved = json.loads(token_path.read_text())
    assert saved["access_token"] == "fake-token-abc"
    assert "saved_at" in saved
    assert oct(token_path.stat().st_mode)[-3:] == "600"
