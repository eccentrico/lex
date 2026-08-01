"""
NSE HTTP session manager.

NSE's API requires browser-like cookies obtained by first visiting the main page.
This module manages a shared requests.Session, auto-refreshing cookies every
5 minutes to prevent 401 / empty-response failures.

Usage:
    from services.indian_data.nse_session import get_nse_session, nse_get

    data = nse_get("/api/bulk-deals")   # returns parsed JSON or None
"""

import time
import logging
from datetime import datetime, timedelta
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

NSE_BASE = "https://www.nseindia.com"

# Headers that mimic a real Chrome browser visiting NSE
_NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://www.nseindia.com/",
    "X-Requested-With": "XMLHttpRequest",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}

_SESSION_TTL_SECS = 300  # Refresh cookies every 5 minutes

_session: Optional[requests.Session] = None
_session_created_at: Optional[datetime] = None


def _warm_session() -> requests.Session:
    """
    Create a new requests.Session and warm it up by visiting NSE pages
    to obtain valid session cookies. Sleeps briefly to appear human.

    NSE requires two warm-up steps for full cookie coverage:
      1. Homepage  — sets nsit, nseappid, bm_sv (Akamai bot-mgmt)
      2. Market-data page — sets additional cookies required by data APIs
         like /api/corporates-pit; without this step those endpoints return
         an HTML bot-check page instead of JSON.
    """
    sess = requests.Session()
    sess.headers.update(_NSE_HEADERS)

    warm_pages = [
        NSE_BASE,
        f"{NSE_BASE}/market-data/securities-available-for-trading",
    ]
    for url in warm_pages:
        try:
            sess.get(url, timeout=20)  # best-effort; NSE may 403 on server IPs
            time.sleep(0.5)
        except requests.RequestException:
            pass  # continue — API calls may still work without full cookie set

    logger.debug(f"NSE session warmed up — cookies: {list(sess.cookies.keys())}")
    return sess


def get_nse_session() -> requests.Session:
    """
    Return a live, cookie-bearing requests.Session for NSE API calls.
    Automatically refreshes the session if cookies are older than SESSION_TTL.
    """
    global _session, _session_created_at

    now = datetime.now()
    needs_refresh = (
        _session is None
        or _session_created_at is None
        or (now - _session_created_at).total_seconds() > _SESSION_TTL_SECS
    )

    if needs_refresh:
        logger.debug("Creating/refreshing NSE session cookies")
        _session = _warm_session()
        _session_created_at = now

    return _session


def nse_get(
    path: str,
    params: Optional[dict] = None,
    retries: int = 2,
    delay: float = 1.5,
) -> Optional[Any]:
    """
    Make a GET request to the NSE API.

    Handles session refresh on 401/403, retries on transient errors, and
    returns parsed JSON or None on failure.

    Args:
        path:    API path, e.g. "/api/bulk-deals"
        params:  Optional query parameters dict
        retries: Number of retry attempts on failure
        delay:   Seconds to wait between retries

    Returns:
        Parsed JSON (dict or list) on success, None on failure.
    """
    url = f"{NSE_BASE}{path}"

    for attempt in range(retries + 1):
        sess = get_nse_session()
        try:
            resp = sess.get(url, params=params, timeout=20)

            # 404 means the endpoint doesn't exist — no point retrying
            if resp.status_code == 404:
                logger.warning(f"NSE 404 for {path} — endpoint may have moved")
                return None

            # NSE returns 401/403 when cookies are stale — force refresh and retry
            if resp.status_code in (401, 403):
                logger.warning(f"NSE auth error {resp.status_code} on {path}, refreshing session")
                global _session_created_at
                _session_created_at = None  # Force re-warm on next call
                if attempt < retries:
                    time.sleep(delay)
                    continue
                return None

            resp.raise_for_status()

            # NSE sometimes returns empty body on rate-limit (HTTP 200 but no JSON)
            if not resp.content:
                logger.warning(f"NSE returned empty body for {path}")
                if attempt < retries:
                    time.sleep(delay * (attempt + 1))
                    continue
                return None

            return resp.json()

        except requests.exceptions.JSONDecodeError:
            # JSON decode error usually means NSE returned an HTML bot-check page.
            # Force session re-warm so next attempt gets fresh cookies.
            logger.warning(f"NSE JSON decode error for {path} (attempt {attempt + 1}) — forcing session refresh")
            _session_created_at = None
        except requests.RequestException as e:
            logger.warning(f"NSE request error for {path} (attempt {attempt + 1}): {e}")

        if attempt < retries:
            time.sleep(delay * (attempt + 1))

    logger.error(f"NSE API call failed after {retries + 1} attempts: {path}")
    return None


def invalidate_session() -> None:
    """Force the next call to re-create the session (useful after persistent failures)."""
    global _session, _session_created_at
    _session = None
    _session_created_at = None
    logger.info("NSE session invalidated")
