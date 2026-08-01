"""
Symbol mapping: NSE (Kite) <-> BSE (Screener).

Screener.in and BSE API use BSE security tokens.
Kite uses NSE symbols (RELIANCE, TCS, etc.).
"""

import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Static NSE -> BSE token mapping for common Nifty 50 / liquid stocks.
# BSE token = numeric security code from BSE.
# Source: BSE website, Angel Broking OpenAPIScripMaster, or manual lookup.
# Format: NSE_SYMBOL -> BSE_TOKEN (string)
NSE_TO_BSE_TOKEN: dict[str, str] = {
    "RELIANCE": "500325",
    "TCS": "532540",
    "HDFCBANK": "500180",
    "INFY": "500209",
    "ICICIBANK": "532174",
    "HINDUNILVR": "500696",
    "ITC": "500875",
    "SBIN": "500112",
    "BHARTIARTL": "532454",
    "KOTAKBANK": "500247",
    "LT": "500510",
    "AXISBANK": "532215",
    "ASIANPAINT": "500820",
    "MARUTI": "532500",
    "TITAN": "500114",
    "WIPRO": "507685",
    "HCLTECH": "532281",
    "SUNPHARMA": "524715",
    "BAJFINANCE": "500034",
    "ULTRACEMCO": "532538",
    "NESTLEIND": "500790",
    "TMPV": "500570",  # Tata Motors (renamed from TATAMOTORS)
    "POWERGRID": "532898",
    "NTPC": "532555",
    "ONGC": "500312",
    "INDUSINDBK": "532187",
    "BAJAJFINSV": "532978",
    "TECHM": "532755",
    "DRREDDY": "500124",
    "BRITANNIA": "500825",
    "CIPLA": "500087",
    "GRASIM": "500300",
    "ADANIPORTS": "532921",
    "EICHERMOT": "505200",
    "HEROMOTOCO": "500182",
    "DIVISLAB": "532488",
    "COALINDIA": "533278",
    "JSWSTEEL": "500228",
    "TATASTEEL": "500470",
    "APOLLOHOSP": "508869",
    "M&M": "500520",
    "HINDALCO": "500440",
    "VEDL": "500295",
    "DABUR": "500096",
    "MARICO": "531642",
    "BPCL": "500547",
    "IOC": "530965",
    "GAIL": "532155",
    # --- Nifty 200 stocks beyond Nifty 50 ---
    "ACC": "500410",
    "ADANIENT": "512599",
    "ADANIGREEN": "541450",
    "AMBUJACEM": "500425",
    "ASHOKLEY": "500477",
    "AUROPHARMA": "524804",
    "BAJAJ-AUTO": "532977",
    "BANKBARODA": "532134",
    "BEL": "500049",
    "BIOCON": "532523",
    "BOSCHLTD": "500530",
    "CANBK": "532483",
    "CHOLAFIN": "511243",
    "COLPAL": "500830",
    "CONCOR": "531344",
    "CUMMINSIND": "500480",
    "DELHIVERY": "543529",
    "DLF": "532868",
    "ESCORTS": "500495",
    "FEDERALBNK": "500469",
    "GLENMARK": "532296",
    "GODREJCP": "532424",
    "GODREJPROP": "533150",
    "HAL": "541154",
    "HAVELLS": "517354",
    "HINDPETRO": "500104",
    "ICICIPRULI": "540716",
    "IDFCFIRSTB": "539437",
    "IGL": "532514",
    "INDHOTEL": "500850",
    "IRCTC": "542830",
    "JINDALSTEL": "532286",
    "JUBLFOOD": "533155",
    "LICI": "543526",
    "LTTS": "540115",
    "LUPIN": "500257",
    "MANAPPURAM": "531213",
    "MCDOWELL-N": "532432",
    "MPHASIS": "526299",
    "MUTHOOTFIN": "533398",
    "NAUKRI": "532777",
    "NMDC": "526371",
    "OBEROIRLTY": "533273",
    "OFSS": "532466",
    "PAGEIND": "532827",
    "PERSISTENT": "533179",
    "PETRONET": "532522",
    "PFC": "532810",
    "PIDILITIND": "500331",
    "PIIND": "523642",
    "PNB": "532461",
    "POLYCAB": "542652",
    "RECLTD": "532955",
    "SBICARD": "543066",
    "SBILIFE": "540719",
    "SHREECEM": "500387",
    "SIEMENS": "500550",
    "SRF": "503806",
    "TORNTPHARM": "500420",
    "TRENT": "500251",
    "TVSMOTOR": "532343",
    "UNIONBANK": "532477",
    "UNITDSPR": "532432",
    "UPL": "512070",
    "VOLTAS": "500575",
    "ZYDUSLIFE": "532321",
    # --- Nifty 200 stocks missing BSE token ---
    "AUBANK": "540611",       # AU Small Finance Bank
    "BANDHANBNK": "541153",   # Bandhan Bank
    "BALKRISIND": "502355",   # Balkrishna Industries
    "SAIL": "500113",         # Steel Authority of India
    "NATCOPHARM": "524816",   # Natco Pharma
    "BHEL": "500103",         # Bharat Heavy Electricals
    "MOTHERSON": "517334",    # Samvardhana Motherson International
    "FORTIS": "532843",       # Fortis Healthcare
    "HDFCAMC": "541729",      # HDFC Asset Management Company
    "LTIM": "540005",         # LTIMindtree
    # --- New Nifty 200 additions (March 2026) ---
    "360ONE": "543900",
    "ABCAPITAL": "540691",
    "ADANIENSOL": "ASM",  # formerly ADANITRANS, BSE ASM code - use NSE
    "ADANIPOWER": "533096",
    "APLAPOLLO": "533758",
    "ASTRAL": "532830",
    "ATGL": "542066",
    "BAJAJHFL": "543528",
    "BAJAJHLDNG": "500490",
    "BANKINDIA": "532149",
    "BDL": "541143",
    "BHARTIHEXA": "ASM",
    "BSE": "ASM",
    "CGPOWER": "500093",
    "COCHINSHIP": "533272",
    "COROMANDEL": "506395",
    "DMART": "540376",
    "ENRIN": "ASM",
    "ETERNAL": "543320",  # formerly ZOMATO
    "GMRAIRPORT": "ASM",
    "GODFRYPHLP": "500163",
    "HINDZINC": "500188",
    "HUDCO": "540530",
    "HYUNDAI": "ASM",
    "IDEA": "532822",
    "INDUSTOWER": "534816",
    "IRB": "532947",
    "IREDA": "ASM",
    "IRFC": "543257",
    "ITCHOTELS": "ASM",
    "JIOFIN": "ASM",
    "JSWENERGY": "533148",
    "KALYANKJIL": "ASM",
    "KEI": "517569",
    "KPITTECH": "542651",
    "LTF": "ASM",
    "LTM": "ASM",  # LTIMindtree
    "MANKIND": "ASM",
    "MAZDOCK": "543041",
    "MFSL": "532493",
    "MOTILALOFS": "532892",
    "NATIONALUM": "532234",
    "NHPC": "533098",
    "NTPCGREEN": "ASM",
    "OIL": "533106",
    "PATANJALI": "ASM",
    "PHOENIXLTD": "512018",
    "POWERINDIA": "500319",
    "PREMIERENE": "ASM",
    "RVNL": "542649",
    "SOLARINDS": "ASM",
    "SUPREMEIND": "517585",
    "SUZLON": "532667",
    "SWIGGY": "ASM",
    "TATACOMM": "500483",
    "TATATECH": "ASM",
    "TIINDIA": "ASM",
    "TVSMOTOR": "532343",
    "VBL": "ASM",
    "VMM": "ASM",
    "WAAREEENER": "ASM",
    "YESBANK": "532648",
}


def get_bse_token(nse_symbol: str) -> Optional[str]:
    """
    Get BSE security token for an NSE symbol.

    Args:
        nse_symbol: NSE symbol (e.g. RELIANCE, TCS, HDFCBANK)

    Returns:
        BSE token string, or None if not found
    """
    if not nse_symbol or not isinstance(nse_symbol, str):
        return None

    symbol = nse_symbol.strip().upper()

    # Check static mapping first
    token = NSE_TO_BSE_TOKEN.get(symbol)
    if token:
        return token

    # Optional: fetch from Angel Broking OpenAPIScripMaster (requires network)
    if os.getenv("FETCH_BSE_TOKENS", "false").lower() == "true":
        try:
            token = _fetch_bse_token_from_angel(symbol)
            if token:
                return token
        except Exception as e:
            logger.debug(f"Could not fetch BSE token for {symbol} from Angel: {e}")

    logger.warning(f"No BSE token mapping for NSE symbol: {symbol}")
    return None


def _fetch_bse_token_from_angel(symbol: str) -> Optional[str]:
    """
    Fetch BSE token from Angel Broking OpenAPIScripMaster.
    Requires network access.

    NOTE: Performance concern — this endpoint returns the full scrip master
    (~150MB+ JSON) for every single lookup. Consider caching the response
    or downloading once and reusing if this fallback is called frequently.
    """
    import requests

    url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    for row in data:
        if row.get("exch_seg") == "BSE" and row.get("symbol", "").upper() == symbol.upper():
            return str(row.get("token", ""))
    return None
