"""Watchlist with on-demand "what changed" — the reactive alert mechanism."""
import json
import time
from datetime import datetime

from lex.paths import lex_home
from lex.tools.common import _ok, _err

_FILE = "watchlist.json"


def _load() -> list:
    p = lex_home() / _FILE
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def _save(items: list) -> None:
    (lex_home() / _FILE).write_text(
        json.dumps(items, indent=2), encoding="utf-8")


def handle_watchlist_add(args: dict) -> str:
    try:
        sym = args["symbol"].upper()
        items = _load()
        if any(i["symbol"] == sym for i in items):
            return _err(f"{sym} already on watchlist")
        items.append({"symbol": sym, "note": args.get("note", ""),
                      "added": datetime.now().date().isoformat(),
                      "last_checked_ts": None, "last_price": None})
        _save(items)
        return _ok({"watching": [i["symbol"] for i in items]})
    except Exception as e:
        return _err(e)


def handle_watchlist_remove(args: dict) -> str:
    try:
        sym = args["symbol"].upper()
        items = _load()
        kept = [i for i in items if i["symbol"] != sym]
        if len(kept) == len(items):
            return _err(f"{sym} not on watchlist")
        _save(kept)
        return _ok({"watching": [i["symbol"] for i in kept]})
    except Exception as e:
        return _err(e)


def handle_watchlist_status(args: dict) -> str:
    """Diff each watched symbol against its last-checked baseline."""
    try:
        items = _load()
        if not items:
            return _ok({"entries": []})
        from services.kite_data import kite_data
        from lex.tools.market import _fetch_nse_announcements
        quotes = kite_data.get_quotes([i["symbol"] for i in items])
        now = time.time()
        entries = []
        for i in items:
            q = quotes.get(i["symbol"]) or {}
            price = q.get("last_price")
            e = {"symbol": i["symbol"], "note": i["note"], "price": price,
                 "change_pct": None, "days_since_check": None, "announcements": []}
            if price and i.get("last_price"):
                e["change_pct"] = round((price / i["last_price"] - 1) * 100, 2)
            if i.get("last_checked_ts"):
                days = max(1, int((now - i["last_checked_ts"]) / 86400))
                e["days_since_check"] = days
                try:
                    e["announcements"] = _fetch_nse_announcements(
                        i["symbol"], min(days, 30)) or []
                except Exception as ex:
                    e["announcements_error"] = str(ex)
            if price:
                i["last_price"], i["last_checked_ts"] = price, now
            entries.append(e)
        _save(items)
        return _ok({"entries": entries})
    except Exception as e:
        return _err(e)
