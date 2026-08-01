"""
Analyst estimates for Indian stocks via FinancialModelingPrep (FMP).

Replaces yfinance which had stale/missing data for most Indian stocks.
FMP provides: consensus price target, forward EPS, analyst count,
recommendation key — all sourced from live sell-side data.

Coverage is best for Nifty 100 stocks; returns None for mid/small caps
where analyst coverage is thin.
"""

import logging
from typing import Any, Optional

# fmp_service.py (the FMP-backed estimates client) isn't included in this
# repo, so analyst estimates are unavailable. get_analyst_estimates() below
# degrades gracefully to None, matching its documented behavior when FMP has
# no coverage for a symbol.
# from services.indian_data.fmp_service import get_analyst_estimates as _fmp_estimates


def _fmp_estimates(symbol: str) -> Optional[dict[str, Any]]:
    return None


logger = logging.getLogger(__name__)


def get_analyst_estimates(symbol: str) -> Optional[dict[str, Any]]:
    """
    Get analyst estimates for a symbol.

    Args:
        symbol: NSE symbol (e.g. RELIANCE, TCS)

    Returns:
        Dict with forwardEps, targetMeanPrice, recommendationMean,
        numberOfAnalystOpinions, recommendationKey — or None when unavailable.
    """
    try:
        result = _fmp_estimates(symbol)
        if not result:
            return None

        # Normalise to the same keys the rest of the system expects
        out: dict[str, Any] = {}

        if result.get("forwardEps") is not None:
            out["forwardEps"] = float(result["forwardEps"])
        if result.get("targetMeanPrice") is not None:
            out["targetMeanPrice"] = float(result["targetMeanPrice"])
        if result.get("targetHighPrice") is not None:
            out["targetHighPrice"] = float(result["targetHighPrice"])
        if result.get("targetLowPrice") is not None:
            out["targetLowPrice"] = float(result["targetLowPrice"])
        if result.get("recommendationMean") is not None:
            out["recommendationMean"] = float(result["recommendationMean"])
        if result.get("recommendationKey"):
            out["recommendationKey"] = str(result["recommendationKey"])
        if result.get("numberOfAnalystOpinions") is not None:
            out["numberOfAnalystOpinions"] = int(result["numberOfAnalystOpinions"])

        return out if out else None

    except Exception as e:
        logger.debug(f"Analyst estimates for {symbol}: {e}")
        return None
