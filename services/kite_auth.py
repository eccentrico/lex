"""
Kite Connect Authentication Module

Authenticates with Zerodha Kite Connect using direct HTTP requests —
no browser or Selenium dependency required.

Flow:
  1. POST /api/login  (user_id + password)  → request_id
  2. POST /api/twofa  (request_id + TOTP)   → redirect URL with request_token
  3. kite.generate_session(request_token)   → access_token  (saved to kite_tokens.json
     under the Lex home directory — see _token_file())

Usage:
    from services.kite_auth import get_or_renew_access_token
    access_token = get_or_renew_access_token()

    # Or get a fully authenticated KiteConnect instance:
    from services.kite_auth import get_kite_instance
    kite = get_kite_instance()
    profile = kite.profile()
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pyotp
import requests
from dotenv import load_dotenv
from kiteconnect import KiteConnect

from services.paths import lex_home

load_dotenv()

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────
KITE_API_KEY    = os.getenv("KITE_API_KEY")
KITE_SECRET_KEY = os.getenv("KITE_SECRET_KEY")
KITE_USER_ID    = os.getenv("KITE_USER_ID")
KITE_PASSWORD   = os.getenv("KITE_PASSWORD")
KITE_TOTP_SECRET = os.getenv("KITE_TOTP_SECRET")


def _token_file() -> str:
    """Resolve the Kite token file path under the Lex home directory.

    Read dynamically (not cached at import time) so KITE_TOKENS_PATH / the
    Lex home override can vary per-call — required for test isolation.
    """
    return os.environ.get(
        "KITE_TOKENS_PATH", str(lex_home() / "kite_tokens.json")
    )


kite = KiteConnect(api_key=KITE_API_KEY)

# ── HTTP login ────────────────────────────────────────────────────────────────

def _http_login() -> str:
    """
    Log in to Zerodha Kite using direct HTTP requests.

    The session must carry the API key context (established by visiting the
    Kite Connect login URL first) so that Zerodha issues a request_token
    redirect rather than treating this as a plain web login.

    Flow:
      1. GET  /connect/login?api_key=...   → sets session cookie with API context
      2. POST /api/login  (credentials)    → request_id
      3. POST /api/twofa  (TOTP)           → redirects to callback URL?request_token=...
         redirect is NOT followed; request_token extracted from Location header

    Returns:
        str: request_token

    Raises:
        RuntimeError: if any step fails
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "X-Kite-Version": "3",
        "Referer": "https://kite.zerodha.com/",
    })

    # ── Step 1: GET login URL → extract sess_id ───────────────────────────────
    # /connect/login?api_key=xxx redirects to itself + &sess_id=yyy.
    # sess_id is Zerodha's OAuth session handle — it must be threaded through
    # the subsequent /connect/finish call to issue the request_token.
    login_url = kite.login_url()  # https://kite.zerodha.com/connect/login?api_key=...&v=3
    resp = session.get(login_url, allow_redirects=True, timeout=15)
    sess_id = parse_qs(urlparse(resp.url).query).get("sess_id", [None])[0]
    if not sess_id:
        raise RuntimeError(f"Could not extract sess_id from login URL: {resp.url}")
    logger.info("Kite OAuth session established (sess_id obtained)")

    # ── Step 2: POST credentials ──────────────────────────────────────────────
    resp = session.post(
        "https://kite.zerodha.com/api/login",
        data={"user_id": KITE_USER_ID, "password": KITE_PASSWORD},
        timeout=15,
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("status") != "success":
        raise RuntimeError(f"Kite login failed: {body.get('message', body)}")
    request_id = body["data"]["request_id"]
    logger.info("Kite step 2: credentials accepted (request_id obtained)")

    # ── Step 3: POST TOTP ─────────────────────────────────────────────────────
    totp_code = pyotp.TOTP(KITE_TOTP_SECRET).now()
    resp = session.post(
        "https://kite.zerodha.com/api/twofa",
        data={
            "user_id":     KITE_USER_ID,
            "request_id":  request_id,
            "twofa_value": totp_code,
            "twofa_type":  "totp",
        },
        timeout=15,
    )
    resp.raise_for_status()
    if resp.json().get("status") != "success":
        raise RuntimeError(f"Kite 2FA failed: {resp.json().get('message', resp.text)}")
    logger.info("Kite step 3: TOTP accepted")

    # ── Step 4: GET /connect/login (now authenticated) → /connect/finish ──────
    # After 2FA succeeds, the Kite SPA redirects to /connect/login?sess_id=...
    # which Zerodha processes and redirects to /connect/finish, which in turn
    # redirects to the app's registered callback URL with ?request_token=xxx.
    resp = session.get(
        f"https://kite.zerodha.com/connect/login"
        f"?api_key={KITE_API_KEY}&sess_id={sess_id}",
        allow_redirects=False,
        timeout=15,
    )
    finish_url = resp.headers.get("Location", "")
    if not finish_url:
        raise RuntimeError(f"No /connect/finish redirect after auth (status={resp.status_code})")

    # ── Step 5: GET /connect/finish → callback URL with request_token ─────────
    resp = session.get(finish_url, allow_redirects=False, timeout=15)
    callback_url = resp.headers.get("Location", "")
    request_token = _token_from_url(callback_url)

    if not request_token:
        raise RuntimeError(
            f"request_token not found in callback URL: {callback_url!r} "
            f"(finish status={resp.status_code})"
        )

    logger.info("Kite step 5: request_token obtained")
    return request_token


def _token_from_url(url: str) -> str:
    """Extract request_token query parameter from a URL string. Returns '' if absent."""
    if not url or "request_token" not in url:
        return ""
    return parse_qs(urlparse(url).query).get("request_token", [""])[0]


def _do_full_login() -> str:
    """HTTP login → generate_session → save token file. Returns access_token."""
    request_token = _http_login()
    data = kite.generate_session(request_token, api_secret=KITE_SECRET_KEY)
    kite.set_access_token(data["access_token"])

    IST = timezone(timedelta(hours=5, minutes=30))
    token_path = _token_file()
    with open(token_path, "w") as f:
        json.dump(
            {
                "access_token": data["access_token"],
                "saved_at": datetime.now(IST).isoformat(),  # e.g. 2026-03-27T08:00:00+05:30
            },
            f,
        )
    os.chmod(token_path, 0o600)

    logger.info(f"Access token saved to {token_path}")
    return data["access_token"]


# ── Public API ────────────────────────────────────────────────────────────────

def get_or_renew_access_token(max_retries: int = 3) -> str:
    """
    Perform a fresh Kite login and return a valid access_token.

    Zerodha does not support refresh tokens for standard API accounts —
    every session requires the full credentials + TOTP flow. This function
    retries up to max_retries times to handle transient failures (network
    blip, TOTP at 30-second boundary, temporary Kite API error).

    Returns:
        str: access_token

    Raises:
        RuntimeError: if all attempts fail
    """
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            token = _do_full_login()
            logger.info(f"Authentication succeeded on attempt {attempt}/{max_retries}")
            return token
        except Exception as e:
            last_error = e
            logger.warning(
                f"Login attempt {attempt}/{max_retries} failed: "
                f"{type(e).__name__}: {e}"
            )
            if attempt < max_retries:
                wait = attempt * 3   # 3s, 6s between retries
                logger.info(f"Retrying in {wait}s …")
                time.sleep(wait)

    raise RuntimeError(
        f"Kite authentication failed after {max_retries} attempts: {last_error}"
    )


def get_kite_instance() -> KiteConnect:
    """
    Return a fully authenticated KiteConnect instance.

    Performs a fresh login on every call (token is always re-fetched).
    For the daily workflow use get_or_renew_access_token() once at startup
    and reuse the token via kite_data.set_access_token().
    """
    get_or_renew_access_token()
    return kite


def invalidate_session() -> None:
    """Invalidate the current session and delete the saved token file."""
    token_path = _token_file()
    if os.path.exists(token_path):
        try:
            with open(token_path, "r") as f:
                tokens = json.load(f)
            access_token = tokens.get("access_token")
            if access_token:
                kite.invalidate_access_token(access_token)
        except Exception:
            pass
        os.remove(token_path)
        logger.info("Session invalidated and token file deleted")


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    print("=" * 50)
    print("Kite Connect HTTP Authentication Test")
    print("=" * 50)

    missing = [
        name for name, val in [
            ("KITE_API_KEY",     KITE_API_KEY),
            ("KITE_SECRET_KEY",  KITE_SECRET_KEY),
            ("KITE_USER_ID",     KITE_USER_ID),
            ("KITE_PASSWORD",    KITE_PASSWORD),
            ("KITE_TOTP_SECRET", KITE_TOTP_SECRET),
        ]
        if not val
    ]
    if missing:
        print(f"Missing env vars: {', '.join(missing)}")
        raise SystemExit(1)

    print("\nStarting HTTP-based authentication...")
    try:
        access_token = get_or_renew_access_token()
        profile = kite.profile()
        print(f"\n✓ Authentication successful!")
        print(f"  User:    {profile.get('user_name')}")
        print(f"  User ID: {profile.get('user_id')}")
        print(f"  Email:   {profile.get('email')}")
        print(f"  Token saved to: {_token_file()}")
    except Exception as e:
        print(f"\n✗ Authentication failed: {e}")
        raise SystemExit(1)
