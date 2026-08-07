"""
Groww Trade API Authentication Module

Auth flow (api_key + secret, per https://groww.in/trade-api/docs/python-sdk,
verified against growwapi 1.5.0):
    GrowwAPI.get_access_token(api_key=..., secret=...) -> access_token (str)

Unlike Kite, there's no HTTP login/2FA dance — one call exchanges the
key+secret pair (from Groww's API Keys dashboard) for a token. That key+secret
flow needs periodic manual re-approval in Groww's dashboard (Groww's
alternative TOTP-secret flow avoids that, but the user already generated
key+secret credentials, so this module only implements that path).

Usage:
    from services.groww_auth import get_or_renew_access_token
    access_token = get_or_renew_access_token()
"""
import json
import logging
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from growwapi import GrowwAPI

from services.paths import lex_home

load_dotenv()

logger = logging.getLogger(__name__)

GROWW_API_KEY = os.getenv("GROWW_API_KEY")
GROWW_API_SECRET = os.getenv("GROWW_API_SECRET")


def _token_file() -> str:
    """Resolve the Groww token file path under the Lex home directory.

    Read dynamically (not cached at import time) so LEX_HOME/GROWW_TOKENS_PATH
    can vary per-call — required for test isolation, mirroring
    services/kite_auth.py's _token_file().
    """
    return os.environ.get(
        "GROWW_TOKENS_PATH", str(lex_home() / "groww_tokens.json")
    )


def get_or_renew_access_token() -> str:
    """
    Exchange GROWW_API_KEY + GROWW_API_SECRET for an access token and save it
    to disk.

    ponytail: single attempt, no retry loop — unlike Kite's login (which
    retries through a TOTP timing window and a multi-step HTTP flow), this is
    one POST; a failure means the key/secret pair is invalid or not approved
    in Groww's dashboard, which retrying won't fix. Add retries if Groww's
    endpoint turns out to be flaky in practice.

    Returns:
        str: access_token

    Raises:
        RuntimeError: if GROWW_API_KEY / GROWW_API_SECRET are not set.
        growwapi.groww.exceptions.GrowwAPIException: if the exchange itself fails.
    """
    if not GROWW_API_KEY or not GROWW_API_SECRET:
        raise RuntimeError(
            "GROWW_API_KEY / GROWW_API_SECRET not set — Groww integration is not configured."
        )

    access_token = GrowwAPI.get_access_token(api_key=GROWW_API_KEY, secret=GROWW_API_SECRET)

    token_path = _token_file()
    with open(token_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "access_token": access_token,
                "saved_at": datetime.now(timezone.utc).isoformat(),
            },
            f,
        )
    os.chmod(token_path, 0o600)

    logger.info(f"Groww access token saved to {token_path}")
    return access_token
