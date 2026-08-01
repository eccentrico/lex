"""
Sector WACC ranges for Indian stocks.

Used for DCF valuation. Risk-free rate = 10Y G-Sec (~7%).
Equity risk premium for India ~6%.
"""

# Sector -> (min_wacc, max_wacc) as decimal e.g. 0.08 = 8%
SECTOR_WACC_RANGE: dict[str, tuple[float, float]] = {
    "IT": (0.10, 0.13),
    "Pharma": (0.09, 0.12),
    "Banking": (0.10, 0.12),
    "Financial Services": (0.10, 0.12),
    "Energy": (0.10, 0.12),
    "FMCG": (0.08, 0.10),
    "Auto": (0.09, 0.11),
    "Metals": (0.10, 0.12),
    "Telecom": (0.09, 0.11),
    "Infrastructure": (0.10, 0.12),
    "Healthcare": (0.09, 0.11),
}

# Default when sector unknown
DEFAULT_WACC = 0.11  # 11%

# India-specific
RISK_FREE_RATE = 0.07  # 10Y G-Sec
TERMINAL_GROWTH = 0.025  # 2.5% GDP proxy


def get_sector_wacc(sector: str) -> float:
    """Get midpoint WACC for a sector."""
    r = SECTOR_WACC_RANGE.get(sector, (DEFAULT_WACC, DEFAULT_WACC))
    return (r[0] + r[1]) / 2
