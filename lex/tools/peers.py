"""Side-by-side fundamentals for a symbol and its sector peers.

"22x earnings" means nothing on its own — it means something against the peer
group's 15x or 40x. This is the cheapest way to stop a research pass from
judging a valuation in isolation.
"""
import logging

from lex.tools.common import _ok, _err

logger = logging.getLogger(__name__)

# Screener serialises one company's ~11 requests at a time behind a semaphore,
# so a wide peer set turns into a very slow tool call. Four is enough to place
# a stock in its group.
_MAX_PEERS = 4

_FIELDS = ("currentPrice", "marketCap", "trailingPE", "priceToBook",
           "returnOnEquity", "roce", "dividendYield", "profitMargins",
           "revenueGrowth", "debtToEquity")


def _metrics(symbol: str) -> dict:
    from services.indian_data import fundamental_service
    data = fundamental_service.get_enriched_fundamentals(symbol) or {}
    metrics = data.get("metrics") or {}
    row = {f: metrics.get(f) for f in _FIELDS if metrics.get(f) is not None}
    row["source"] = data.get("source")
    return row


def _median(values: list[float]):
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    value = (ordered[mid] if len(ordered) % 2
             else (ordered[mid - 1] + ordered[mid]) / 2)
    return round(value, 4)


def handle_peer_comparison(args: dict) -> str:
    try:
        from services.indian_data import sector_indices
        symbol = args["symbol"].strip().upper()
        requested = [p.strip().upper() for p in (args.get("peers") or []) if p.strip()]
        peers = [p for p in requested if p != symbol][:_MAX_PEERS]
        source = "caller"
        if not peers:
            peers = sector_indices.peers_in_sector(symbol)[:_MAX_PEERS]
            source = "sector_map"

        out = {"symbol": symbol, "sector": sector_indices.get_sector_for_symbol(symbol),
               "peer_source": source, "rows": {}, "peer_median": {}, "errors": []}
        if not peers:
            out["errors"].append(
                f"no peers known for {symbol} — it is not in the sector map; "
                "pass an explicit peers list to compare it against names you choose")

        for name in [symbol] + peers:
            try:
                out["rows"][name] = _metrics(name)
            except Exception as e:  # one dead peer must not sink the comparison
                logger.warning("peer metrics failed for %s: %s", name, e)
                out["errors"].append(f"{name}: {e}")

        for field in _FIELDS:
            values = [row[field] for name, row in out["rows"].items()
                      if name != symbol and isinstance(row.get(field), (int, float))]
            if values:
                out["peer_median"][field] = _median(values)

        subject = out["rows"].get(symbol, {})
        out["vs_peer_median"] = {
            field: round(subject[field] - median, 4)
            for field, median in out["peer_median"].items()
            if isinstance(subject.get(field), (int, float))
        }
        return _ok(out)
    except Exception as e:
        return _err(e)
