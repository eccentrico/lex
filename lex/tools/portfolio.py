"""Portfolio, order-book, and thesis-note tools.

Thesis notes are a flat JSON file under lex_home() (no SQLite): nothing here
needs relational queries, so a dict keyed by symbol is the simplest thing
that works.
"""
import json
import threading
from datetime import datetime, timezone

from lex.paths import lex_home
from lex.tools.common import _ok, _err

_lock = threading.Lock()
_kite = None
_kite_lock = threading.Lock()


def _get_kite():
    global _kite
    with _kite_lock:
        if _kite is None:
            from services.kite_auth import get_kite_instance
            _kite = get_kite_instance()
        return _kite


def _theses_path():
    return lex_home() / "theses.json"


def _load_theses() -> dict:
    path = _theses_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_theses(theses: dict) -> None:
    _theses_path().write_text(json.dumps(theses, indent=2))


def get_thesis(symbol: str) -> str | None:
    entry = _load_theses().get(symbol.upper())
    return entry["thesis"] if entry else None


def handle_thesis_note(args: dict, **kwargs) -> str:
    try:
        symbol = args["symbol"].upper()
        thesis = args["thesis"]
        with _lock:
            theses = _load_theses()
            if thesis:
                theses[symbol] = {
                    "thesis": thesis,
                    "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
            else:
                theses.pop(symbol, None)
            _save_theses(theses)
        return _ok({"symbol": symbol, "thesis": thesis or None})
    except Exception as e:
        return _err(e)


def handle_portfolio_status(args: dict, **kwargs) -> str:
    try:
        from services.kite_data import kite_data
        holdings = kite_data.get_holdings() or []
        out, total_holdings = [], 0.0
        for h in holdings:
            qty = int(h.get("quantity", 0))
            avg, ltp = float(h.get("average_price", 0)), float(h.get("last_price", 0))
            value = qty * ltp
            pnl = round((ltp - avg) * qty, 2)
            pnl_pct = round((ltp - avg) / avg * 100, 2) if avg else 0.0
            out.append({**h, "value": value, "pnl": pnl, "pnl_pct": pnl_pct,
                        "thesis": get_thesis(h["tradingsymbol"])})
            total_holdings += value
        cash = 0.0
        try:
            m = _get_kite().margins()
            cash = float(m["equity"]["available"]["live_balance"])
        except Exception as e:
            return _err(f"margins fetch failed (fail-closed on cash): {e}")
        return _ok({"holdings": out, "cash": cash,
                    "holdings_value": round(total_holdings, 2),
                    "total_value": round(cash + total_holdings, 2)})
    except Exception as e:
        return _err(e)


def handle_order_book(args: dict, **kwargs) -> str:
    data = {"orders": [], "gtts": [], "errors": []}
    try:
        data["orders"] = _get_kite().orders()
    except Exception as e:
        data["errors"].append(f"orders: {e}")
    try:
        data["gtts"] = _get_kite().get_gtts()
    except Exception as e:
        data["errors"].append(f"gtts: {e}")
    return _ok(data)
