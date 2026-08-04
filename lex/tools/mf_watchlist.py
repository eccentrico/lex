"""Mutual fund watchlist — the same on-demand "what changed" shape as
lex/tools/watchlist.py, diffing NAV instead of LTP. A separate JSON store: a
scheme_code is not an NSE symbol, and AMFI has no announcements feed to merge
in the way equity's watchlist_status merges NSE filings.
"""
import json
import time
from datetime import datetime

from lex.paths import lex_home
from lex.tools.common import _ok, _err

_FILE = "mf_watchlist.json"


def _load() -> list:
    p = lex_home() / _FILE
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def _save(items: list) -> None:
    (lex_home() / _FILE).write_text(json.dumps(items, indent=2), encoding="utf-8")


def handle_mf_watchlist_add(args: dict) -> str:
    try:
        code = str(args["scheme_code"])
        items = _load()
        if any(i["scheme_code"] == code for i in items):
            return _err(f"{code} already on the fund watchlist")
        items.append({"scheme_code": code, "note": args.get("note", ""),
                      "added": datetime.now().date().isoformat(),
                      "last_checked_ts": None, "last_nav": None})
        _save(items)
        return _ok({"watching": [i["scheme_code"] for i in items]})
    except Exception as e:
        return _err(e)


def handle_mf_watchlist_remove(args: dict) -> str:
    try:
        code = str(args["scheme_code"])
        items = _load()
        kept = [i for i in items if i["scheme_code"] != code]
        if len(kept) == len(items):
            return _err(f"{code} not on the fund watchlist")
        _save(kept)
        return _ok({"watching": [i["scheme_code"] for i in kept]})
    except Exception as e:
        return _err(e)


def handle_mf_watchlist_status(args: dict) -> str:
    """Diff each watched scheme's NAV against its last-checked baseline."""
    try:
        items = _load()
        if not items:
            return _ok({"entries": []})
        from services.kite_data import kite_data
        quotes = kite_data.get_mf_quote([i["scheme_code"] for i in items])
        now = time.time()
        entries = []
        for i in items:
            q = quotes.get(i["scheme_code"]) or {}
            nav = q.get("nav")
            e = {"scheme_code": i["scheme_code"], "note": i["note"], "nav": nav,
                 "change_pct": None, "days_since_check": None}
            if nav and i.get("last_nav"):
                e["change_pct"] = round((nav / i["last_nav"] - 1) * 100, 2)
            if i.get("last_checked_ts"):
                e["days_since_check"] = max(1, int((now - i["last_checked_ts"]) / 86400))
            if nav:
                i["last_nav"], i["last_checked_ts"] = nav, now
            entries.append(e)
        _save(items)
        return _ok({"entries": entries})
    except Exception as e:
        return _err(e)
