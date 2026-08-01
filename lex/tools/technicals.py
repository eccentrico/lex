"""Price structure derived from OHLCV: returns, trend, drawdown, volume, RS.

Deliberately not a charting library — no oscillators, no patterns. This exists
so a research pass can say "the market has already re-rated this 40% in six
months" instead of discussing fundamentals in a price vacuum.
"""
import logging
from datetime import date, timedelta

from lex.tools.common import _ok, _err

logger = logging.getLogger(__name__)

_WINDOWS = {"1w": 5, "1m": 21, "3m": 63, "6m": 126, "1y": 252}
_LOOKBACK_DAYS = 420  # ~14 months of calendar days to clear 252 sessions


def _pct(new, old):
    if not old:
        return None
    return round((new / old - 1) * 100, 2)


def _returns(closes: list[float]) -> dict:
    last = closes[-1]
    out = {}
    for name, sessions in _WINDOWS.items():
        out[name] = _pct(last, closes[-(sessions + 1)]) if len(closes) > sessions else None
    return out


def _trend(closes: list[float]) -> dict:
    def sma(n):
        return round(sum(closes[-n:]) / n, 2) if len(closes) >= n else None

    last, sma50, sma200 = closes[-1], sma(50), sma(200)
    trend = {"sma_50": sma50, "sma_200": sma200,
             "above_sma_50": last > sma50 if sma50 else None,
             "above_sma_200": last > sma200 if sma200 else None,
             "cross": None}
    if sma50 and sma200 and len(closes) >= 205:
        prior50 = round(sum(closes[-55:-5]) / 50, 2)
        prior200 = round(sum(closes[-205:-5]) / 200, 2)
        now_above, was_above = sma50 > sma200, prior50 > prior200
        if now_above != was_above:
            trend["cross"] = "golden_cross" if now_above else "death_cross"
        else:
            trend["cross"] = "50dma_above_200dma" if now_above else "50dma_below_200dma"
    return trend


def _drawdown(highs: list[float], lows: list[float], closes: list[float]) -> dict:
    window = min(len(closes), _WINDOWS["1y"])
    high_52w, low_52w = max(highs[-window:]), min(lows[-window:])
    last = closes[-1]
    return {"high_52w": round(high_52w, 2), "low_52w": round(low_52w, 2),
            "from_52w_high_pct": _pct(last, high_52w),
            "off_52w_low_pct": _pct(last, low_52w),
            "sessions_used": window}


def _volume(volumes: list[float]) -> dict:
    if len(volumes) < 30 or not any(volumes):
        return {"avg_5d": None, "avg_30d": None, "surge_ratio": None}
    avg5 = sum(volumes[-5:]) / 5
    avg30 = sum(volumes[-30:]) / 30
    return {"avg_5d": round(avg5), "avg_30d": round(avg30),
            "surge_ratio": round(avg5 / avg30, 2) if avg30 else None}


def _relative_strength(symbol: str, stock_returns: dict) -> dict:
    """Stock 3m/6m return minus its sector index's, when NSE will give us one."""
    from services.indian_data import sector_indices
    label = sector_indices.get_sector_for_symbol(symbol)
    out = {"sector": label, "index_returns": {}, "excess_returns": {},
           "note": None}
    if not label:
        out["note"] = (f"unknown — {symbol} is not in the sector map, so there is "
                       "no index to compare against")
        return out
    try:
        history = sector_indices.get_index_history(label)
    except Exception as e:
        logger.warning("index history failed for %s: %s", label, e)
        history = []
    if not history:
        out["note"] = ("unknown — NSE index history unavailable, relative "
                       "strength not computed")
        return out
    closes = [row["close"] for row in history]
    index_returns = _returns(closes)
    out["index_returns"] = index_returns
    for window in ("1m", "3m", "6m", "1y"):
        stock, index = stock_returns.get(window), index_returns.get(window)
        out["excess_returns"][window] = (round(stock - index, 2)
                                         if stock is not None and index is not None
                                         else None)
    return out


def handle_technicals(args: dict) -> str:
    try:
        from services.kite_data import kite_data
        symbol = args["symbol"].strip().upper()
        today = date.today()
        df = kite_data.get_historical_data(
            symbol, str(today - timedelta(days=_LOOKBACK_DAYS)), str(today),
            interval="day")
        if df is None or not len(df):
            return _err(f"no price history for {symbol}")
        closes = [float(c) for c in df["close"].tolist()]
        highs = [float(h) for h in df["high"].tolist()] if "high" in df else closes
        lows = [float(low) for low in df["low"].tolist()] if "low" in df else closes
        volumes = [float(v) for v in df["volume"].tolist()] if "volume" in df else []
        returns = _returns(closes)
        return _ok({
            "symbol": symbol,
            "last_close": round(closes[-1], 2),
            "sessions": len(closes),
            "as_of": str(df.index[-1]) if len(df.index) else None,
            "returns_pct": returns,
            "trend": _trend(closes),
            "range": _drawdown(highs, lows, closes),
            "volume": _volume(volumes),
            "relative_strength": _relative_strength(symbol, returns),
        })
    except Exception as e:
        return _err(e)
