"""
DCF valuation for Indian stocks.

Estimates fair value per share using free cash flow projections.
India-specific: 10Y G-Sec as risk-free rate, sector WACC.

Price, shares outstanding, debt, and cash are sourced from FMP.
FCF history is sourced from Screener.in (primary) with FMP as fallback.
"""

import logging
from dataclasses import dataclass
from typing import Any, Optional

from services.valuation.sector_wacc_india import get_sector_wacc, TERMINAL_GROWTH
from services.indian_data.fundamental_service import get_enriched_fundamentals

logger = logging.getLogger(__name__)

# Sector mapping (reuse from portfolio_manager)
SECTOR_MAPPING = {
    "HDFCBANK": "Banking", "ICICIBANK": "Banking", "SBIN": "Banking",
    "KOTAKBANK": "Banking", "AXISBANK": "Banking", "INDUSINDBK": "Banking",
    "BAJFINANCE": "Financial Services", "BAJAJFINSV": "Financial Services",
    "HDFC": "Financial Services",
    "TCS": "IT", "INFY": "IT", "WIPRO": "IT", "HCLTECH": "IT", "TECHM": "IT",
    "RELIANCE": "Energy", "ONGC": "Energy", "IOC": "Energy", "BPCL": "Energy",
    "GAIL": "Energy", "NTPC": "Energy", "POWERGRID": "Energy",
    "SUNPHARMA": "Pharma", "DRREDDY": "Pharma", "CIPLA": "Pharma",
    "DIVISLAB": "Pharma", "APOLLOHOSP": "Healthcare",
    "HINDUNILVR": "FMCG", "ITC": "FMCG", "NESTLEIND": "FMCG",
    "BRITANNIA": "FMCG", "DABUR": "FMCG", "MARICO": "FMCG",
    "MARUTI": "Auto", "TATAMOTORS": "Auto", "M&M": "Auto",
    "BAJAJ-AUTO": "Auto", "HEROMOTOCO": "Auto", "EICHERMOT": "Auto",
    "TATASTEEL": "Metals", "JSWSTEEL": "Metals", "HINDALCO": "Metals",
    "VEDL": "Metals", "COALINDIA": "Metals",
    "BHARTIARTL": "Telecom",
    "LARSEN": "Infrastructure", "ULTRACEMCO": "Infrastructure",
    "GRASIM": "Infrastructure", "ADANIPORTS": "Infrastructure",
}


@dataclass
class DCFResult:
    fair_value_per_share: float
    upside_pct: float
    assumptions: dict[str, Any]
    error: Optional[str] = None


def _extract_fcf_from_fmp(symbol: str) -> list[tuple[str, float]]:
    """Extract FCF history from FMP cash flow statements."""
    try:
        from services.indian_data.fmp_service import _get, _ns, _f
    except ImportError:
        # lex: not vendored (fmp) — degrade gracefully. No FMP FCF history in v1;
        # get_fcf_history() already treats [] as "insufficient data".
        logger.debug(f"fmp_service not vendored; no FMP FCF history for {symbol}")
        return []
    try:
        data = _get(f"/v3/cash-flow-statement/{_ns(symbol)}", params={"limit": "10"})
        if not data or not isinstance(data, list):
            return []
        result = []
        for row in data:
            # FMP provides freeCashFlow directly
            fcf = _f(row.get("freeCashFlow"))
            if fcf is None:
                # Compute: operatingCashFlow - capitalExpenditure
                ocf = _f(row.get("operatingCashFlow")) or 0.0
                capex = abs(_f(row.get("capitalExpenditure")) or 0.0)
                fcf = ocf - capex if ocf else None
            date_str = str(row.get("date", ""))[:10]
            if fcf and fcf > 0 and date_str:
                result.append((date_str, fcf))
        return sorted(result, key=lambda x: x[0])[-10:]
    except Exception as e:
        logger.debug(f"FMP FCF extraction for {symbol}: {e}")
        return []


def _extract_fcf_from_screener(cash_flow: dict) -> list[tuple[str, float]]:
    """Extract FCF from Screener cash flow structure. API returns {date: value} or {date: {metric: value}}."""
    result = []
    operating = cash_flow.get("operating") or {}
    for date_str, val in operating.items():
        fcf_val = None
        if isinstance(val, (int, float)) and val > 0:
            fcf_val = float(val)
        elif isinstance(val, dict):
            for k, v in val.items():
                if isinstance(v, (int, float)) and v > 0:
                    fcf_val = float(v)
                    break
        elif isinstance(val, str):
            try:
                fcf_val = float(val.replace(",", ""))
            except ValueError:
                pass
        if fcf_val is not None and fcf_val > 0:
            result.append((str(date_str)[:10], fcf_val))
    return sorted(result, key=lambda x: x[0])[-10:] if result else []


def get_fcf_history(symbol: str) -> list[tuple[str, float]]:
    """
    Get FCF history for DCF (last 5+ years preferred).

    Returns:
        List of (date_str, fcf_value) sorted by date
    """
    enriched = get_enriched_fundamentals(symbol)
    cf = enriched.get("cash_flow")
    if cf:
        fcf = _extract_fcf_from_screener(cf)
        if len(fcf) >= 3:
            return fcf
    return _extract_fcf_from_fmp(symbol)


def estimate_dcf(
    symbol: str,
    growth_rate: Optional[float] = None,
    wacc: Optional[float] = None,
    terminal_growth: float = TERMINAL_GROWTH,
    current_price: Optional[float] = None,
) -> Optional[DCFResult]:
    """
    Estimate fair value per share using DCF.

    Args:
        symbol: NSE symbol
        growth_rate: FCF growth rate (decimal). If None, derived from history.
        wacc: Discount rate. If None, uses sector WACC.
        terminal_growth: Terminal growth rate (default 2.5%)
        current_price: Current stock price. If None, fetched from FMP.

    Returns:
        DCFResult or None if insufficient data
    """
    try:
        fcf_history = get_fcf_history(symbol)
        if len(fcf_history) < 3:
            return DCFResult(0, 0, {}, error="Insufficient FCF history")

        # Get current price and shares from FMP
        try:
            from services.indian_data.fmp_service import get_dcf_inputs
        except ImportError:
            # lex: not vendored (fmp) — degrade gracefully. Without FMP there is
            # no price/shares source at this layer, so DCF is unavailable in v1
            # unless a caller supplies current_price AND a future source for shares.
            return DCFResult(0, 0, {}, error="fmp_service not vendored (no price/shares source)")
        fmp_inputs = get_dcf_inputs(symbol)
        if current_price is None:
            current_price = fmp_inputs.get("currentPrice")
        if current_price is None or current_price <= 0:
            return DCFResult(0, 0, {}, error="No current price")

        shares = fmp_inputs.get("sharesOutstanding")
        if not shares or shares <= 0:
            return DCFResult(0, 0, {}, error="No shares outstanding")

        # Growth rate: 5-year CAGR; allow up to -5% for deteriorating businesses
        if growth_rate is None:
            vals = [v for _, v in fcf_history]
            if len(vals) >= 2 and vals[0] > 0:
                cagr = (vals[-1] / vals[0]) ** (1 / (len(vals) - 1)) - 1
                growth_rate = min(0.15, max(-0.05, cagr))
            else:
                growth_rate = 0.08
        growth_rate = min(0.15, max(-0.05, growth_rate))

        # WACC from sector
        if wacc is None:
            sector = SECTOR_MAPPING.get(symbol.upper(), "")
            wacc = get_sector_wacc(sector)

        # Base FCF (latest); flag if only positive year in declining series
        vals = [v for _, v in fcf_history]
        positive_years = sum(1 for v in vals if v > 0)
        base_fcf = fcf_history[-1][1]
        if base_fcf <= 0:
            return DCFResult(0, 0, {}, error="Negative or zero FCF")
        assumptions_extra: dict = {}
        if positive_years == 1 and len(vals) > 1:
            assumptions_extra["fcf_warning"] = "base_fcf_only_positive_year"

        # Project 5 years with decay
        pv_sum = 0
        fcf = base_fcf
        decay = 0.95
        for year in range(1, 6):
            fcf *= (1 + growth_rate * (decay ** (year - 1)))
            pv_sum += fcf / ((1 + wacc) ** year)

        # Terminal value
        terminal_fcf = fcf * (1 + terminal_growth)
        terminal_value = terminal_fcf / (wacc - terminal_growth)
        pv_terminal = terminal_value / ((1 + wacc) ** 5)

        enterprise_value = pv_sum + pv_terminal

        # Net debt from FMP balance sheet
        total_debt = fmp_inputs.get("totalDebt") or 0
        cash = fmp_inputs.get("cash") or 0
        try:
            total_debt = float(total_debt) if total_debt else 0
            cash = float(cash) if cash else 0
        except (ValueError, TypeError):
            total_debt, cash = 0, 0
        net_debt = total_debt - cash

        equity_value = enterprise_value - net_debt
        fair_value = equity_value / shares if shares > 0 else 0

        upside = ((fair_value - current_price) / current_price * 100) if current_price > 0 else 0

        assumptions = {
            "growth_rate_pct": round(growth_rate * 100, 1),
            "wacc_pct": round(wacc * 100, 1),
            "terminal_growth_pct": round(terminal_growth * 100, 1),
            "base_fcf": round(base_fcf, 0),
            "years_projected": 5,
            **assumptions_extra,
        }
        return DCFResult(
            fair_value_per_share=round(fair_value, 2),
            upside_pct=round(upside, 2),
            assumptions=assumptions,
        )
    except Exception as e:
        logger.warning(f"DCF for {symbol}: {e}")
        return DCFResult(0, 0, {}, error=str(e))
