"""
Unified fundamental data service for Indian stocks.

Orchestrates Screener (primary) and yfinance (fallback) for metrics,
cash flow, balance sheet. Adds analyst estimates.

Results are cached in a Postgres-backed L1/L2 cache so repeated runs
(trigger, cron, retry) within the same day never re-hit Screener.in.
"""

import logging
from typing import Any

from services.indian_data.screener_service import get_financials
from services.indian_data.analyst_estimates import get_analyst_estimates
from services.fundamental_cache_service import get_fundamental_cache

logger = logging.getLogger(__name__)

_ENRICHED_CACHE_TTL_DAYS = 1


def get_enriched_fundamentals(symbol: str, use_cache: bool = True) -> dict[str, Any]:
    """
    Get enriched fundamental data for a symbol.

    Merges:
    - Metrics, cash flow, balance sheet from Screener or yfinance
    - Analyst estimates from yfinance

    Returns:
        Dict with metrics, cash_flow, balance_sheet, analyst_estimates, source
    """
    cache = get_fundamental_cache()

    if use_cache:
        cached = cache.get(symbol)
        if cached:
            logger.debug(f"Enriched fundamentals cache hit for {symbol}")
            return cached

    financials = get_financials(symbol)
    if not financials:
        result = {
            "metrics": {},
            "cash_flow": None,
            "balance_sheet": None,
            "analyst_estimates": None,
            "source": "yfinance",
        }
        return result

    estimates = get_analyst_estimates(symbol)

    result = {
        "metrics": financials.get("metrics", {}),
        "cash_flow": financials.get("cash_flow"),
        "balance_sheet": financials.get("balance_sheet"),
        "analyst_estimates": estimates,
        "source": financials.get("source", "yfinance"),
    }

    cache.set(symbol, result, ttl_days=_ENRICHED_CACHE_TTL_DAYS)
    return result
