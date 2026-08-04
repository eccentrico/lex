# Mutual Fund Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add mutual fund lookup, watchlist, and a fund-shaped `deep_research`-style
pipeline as a parallel surface alongside Lex's existing NSE-equity tools.

**Architecture:** New `fund_*`/`mf_*` tools reuse the existing Kite auth session
(`services/kite_data.py`) for scheme search and latest NAV, add a small AMFI-backed
NAV-history service for time series, and a second three-pass research pipeline
(`lex/fund_research.py` + `lex/fund_reports.py`) that mirrors `lex/research.py` /
`lex/reports.py` but with its own fund-shaped section schema. No existing equity
file is modified in a way that changes its behavior — `lex/delegate.py` gains one
new optional parameter with a value-compatible default, and `lex/prompt.py` gains
new constants plus one new line in `build_system_prompt`.

**Tech Stack:** Python, `requests` (already a dependency, used for the AMFI fetch),
`pandas` (already a dependency, used for the Kite MF instrument dump), pytest +
`unittest.mock.patch` / `monkeypatch`, matching the existing `tests/lex/` style.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-04-mutual-fund-support-design.md` — read it
  before starting; this plan implements it task-by-task.
- No new paid data vendor: NAV history comes from AMFI's official free endpoint,
  not a third-party API like mfapi.in.
- No portfolio holdings integration in this pass — `portfolio_status` stays
  equity-only; `mf_holdings()` is not wired in.
- No SIP/order placement or viewing — `mf_orders`/`mf_sips`/`place_mf_order`/
  `place_mf_sip` are not exposed as tools (Lex places no orders, equity or fund).
- No quality scoring for fund reports — `lex/quality.py` stays equity-only.
- New `fund_*`/`mf_*` tools only; existing equity tools, schemas, and files are not
  behaviorally changed.
- `ruff check .` enforces `PLW1514` (unspecified-encoding) — every `open()`/
  `Path.read_text()`/`Path.write_text()` call must pass `encoding="utf-8"`.
- Tests never touch real credentials or `~/.lex` — rely on the existing
  `tests/conftest.py` autouse fixture (isolated `LEX_HOME`, blanked credential env
  vars) exactly as existing tests do. Don't add new fixtures that duplicate it.
- Run `pytest` (whole suite) and `ruff check .` at the end of every task, not just
  the final one — a later task must never be the one that discovers an earlier
  task broke something.

---

### Task 1: Kite MF instrument access + `fund_search`/`fund_quote` tools

**Files:**
- Modify: `services/kite_data.py` (add `_get_mf_instruments()`, `get_mf_quote()`
  methods to `KiteDataService`, near `_get_instruments()` at line 322 and
  `get_quotes()` at line 472)
- Create: `lex/tools/mutual_funds.py`
- Modify: `lex/tools/__init__.py` (import + register `fund_search`, `fund_quote`)
- Test: `tests/lex/test_mutual_funds.py`

**Interfaces:**
- Consumes: `lex.tools.common._ok`/`_err` (existing).
- Produces: `services.kite_data.kite_data._get_mf_instruments() -> pd.DataFrame`
  (columns include `tradingsymbol`, `name`, `amc`, `plan`, `scheme_type`,
  `last_price`, `last_price_date`); `services.kite_data.kite_data.get_mf_quote(scheme_codes: list[str]) -> dict[str, dict]`;
  `lex.tools.mutual_funds.search_schemes(query, limit=5, instruments_df=None) -> list[dict]`;
  `lex.tools.mutual_funds.handle_fund_search(args) -> str`;
  `lex.tools.mutual_funds.handle_fund_quote(args) -> str`. Task 2 imports
  `search_schemes`'s module and adds `handle_fund_history` to it.

- [ ] **Step 1: Write the failing tests for the tool handlers**

Create `tests/lex/test_mutual_funds.py`:

```python
import json
from unittest.mock import patch

import pandas as pd

from lex.tools.mutual_funds import (
    search_schemes, handle_fund_search, handle_fund_quote)

MF_DF = pd.DataFrame([
    {"tradingsymbol": "120503", "name": "PARAG PARIKH FLEXI CAP FUND - DIRECT GROWTH",
     "amc": "PPFAS", "plan": "direct", "scheme_type": "Equity", "last_price": 75.5,
     "last_price_date": "2026-08-01"},
    {"tradingsymbol": "120504", "name": "PARAG PARIKH FLEXI CAP FUND - REGULAR GROWTH",
     "amc": "PPFAS", "plan": "regular", "scheme_type": "Equity", "last_price": 70.1,
     "last_price_date": "2026-08-01"},
    {"tradingsymbol": "100001", "name": "HDFC TOP 100 FUND - DIRECT GROWTH",
     "amc": "HDFC", "plan": "direct", "scheme_type": "Equity", "last_price": 900.0,
     "last_price_date": "2026-08-01"},
])


def test_finds_by_scheme_name():
    out = search_schemes("parag parikh flexi cap direct", instruments_df=MF_DF)
    assert out[0]["tradingsymbol"] == "120503"


def test_finds_by_exact_scheme_code():
    out = search_schemes("120503", instruments_df=MF_DF)
    assert out[0]["tradingsymbol"] == "120503"


def test_no_match_returns_empty():
    assert search_schemes("zzzz-not-a-fund", instruments_df=MF_DF) == []


def test_handle_fund_search_envelopes():
    with patch("services.kite_data.kite_data._get_mf_instruments", return_value=MF_DF):
        out = json.loads(handle_fund_search({"query": "hdfc top 100"}))
    assert out["success"] and out["data"][0]["tradingsymbol"] == "100001"


def test_handle_fund_quote_returns_nav():
    fake = {"120503": {"scheme_code": "120503", "nav": 75.5}}
    with patch("services.kite_data.kite_data.get_mf_quote", return_value=fake):
        out = json.loads(handle_fund_quote({"scheme_codes": ["120503"]}))
    assert out["success"] and out["data"]["120503"]["nav"] == 75.5


def test_handle_fund_quote_error_enveloped():
    with patch("services.kite_data.kite_data.get_mf_quote",
              side_effect=RuntimeError("kite down")):
        out = json.loads(handle_fund_quote({"scheme_codes": ["120503"]}))
    assert out["success"] is False and "kite down" in out["error"]


def test_tools_dict_registers_fund_search_and_quote():
    from lex.tools import TOOLS
    assert TOOLS["fund_search"]["schema"]["name"] == "fund_search"
    assert TOOLS["fund_quote"]["schema"]["name"] == "fund_quote"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/lex/test_mutual_funds.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lex.tools.mutual_funds'`

- [ ] **Step 3: Add `_get_mf_instruments()` and `get_mf_quote()` to `KiteDataService`**

In `services/kite_data.py`, add two instance attributes in `__init__` right after
`self._instruments_cache_time = None` (line 123):

```python
        self._mf_instruments_cache: Optional[pd.DataFrame] = None
        self._mf_instruments_cache_time: Optional[datetime] = None
```

Then add these two methods immediately after `_get_instruments()` (after line 361,
before `_symbol_to_token`):

```python
    def _get_mf_instruments(self) -> pd.DataFrame:
        """
        Get and cache the mutual fund scheme master + latest NAV from Kite Connect.

        Returns:
            DataFrame with columns: tradingsymbol (the AMFI scheme code), name,
            amc, plan, scheme_type, last_price, last_price_date, etc.
        """
        if not self._access_token:
            raise RuntimeError(
                "Not authenticated. Call set_access_token() first."
            )

        if (self._mf_instruments_cache is not None and
            self._mf_instruments_cache_time is not None and
            datetime.now() - self._mf_instruments_cache_time < self._cache_ttl):
            return self._mf_instruments_cache

        try:
            instruments = self._retry_with_auth(self._kite.mf_instruments)
            df = pd.DataFrame(instruments)
            self._mf_instruments_cache = df
            self._mf_instruments_cache_time = datetime.now()
            logger.info(f"Fetched and cached {len(df)} MF instruments")
            return df
        except Exception as e:
            logger.error(f"Error fetching MF instruments: {e}")
            if self._mf_instruments_cache is not None:
                logger.warning("Using expired MF instruments cache")
                return self._mf_instruments_cache
            raise

    def get_mf_quote(self, scheme_codes: List[str]) -> Dict[str, Dict[str, Any]]:
        """
        Latest NAV + scheme metadata for a list of AMFI scheme codes.

        Kite's MF `tradingsymbol` field is the AMFI scheme code; this is the
        canonical identifier used across every mutual-fund tool.

        Args:
            scheme_codes: AMFI scheme codes (as returned by fund_search).

        Returns:
            Dict mapping scheme_code -> {scheme_code, name, amc, plan,
            scheme_type, nav, nav_date}. A code with no matching instrument is
            simply absent from the result.
        """
        if not scheme_codes:
            return {}
        try:
            df = self._get_mf_instruments()
        except Exception as e:
            logger.error(f"get_mf_quote: could not load MF instruments: {e}")
            return {}
        result = {}
        for code in scheme_codes:
            match = df[df["tradingsymbol"].astype(str) == str(code)]
            if len(match) == 0:
                continue
            row = match.iloc[0]
            result[str(code)] = {
                "scheme_code": str(code),
                "name": row.get("name", ""),
                "amc": row.get("amc", ""),
                "plan": row.get("plan", ""),
                "scheme_type": row.get("scheme_type", ""),
                "nav": float(row.get("last_price", 0) or 0),
                "nav_date": str(row.get("last_price_date", "")),
            }
        return result
```

**Verify against the live API before moving on**: run
`.venv/bin/python -c "from services.kite_data import kite_data; df = kite_data._get_mf_instruments(); print(df.columns.tolist()); print(df.iloc[0])"`
against an authenticated session (`.venv/bin/python -m services.kite_auth` first if
the token is stale). Confirm `tradingsymbol` is in fact the AMFI scheme code
(numeric string) as the design assumed. If the live column names differ from
`name`/`amc`/`plan`/`scheme_type`/`last_price`/`last_price_date`, adjust
`get_mf_quote` above to match before continuing — everything downstream
(`fund_search`, `fund_quote`, the fund research pipeline) depends on this shape.

- [ ] **Step 4: Create `lex/tools/mutual_funds.py`**

```python
"""Fund search and quote tools: fuzzy scheme lookup + latest NAV.

Mirrors lex/tools/market.py's symbol_search/market_quote shape, but a scheme is
searched by name/AMC rather than by a partial trading-symbol prefix — nobody
types half an AMFI scheme code.
"""
import difflib

from lex.tools.common import _ok, _err


def search_schemes(query: str, limit: int = 5, instruments_df=None) -> list:
    if instruments_df is None:
        from services.kite_data import kite_data
        instruments_df = kite_data._get_mf_instruments()
    df = instruments_df
    q = query.strip().upper()

    exact = df[df["tradingsymbol"].astype(str).str.upper() == q]
    if len(exact):
        return exact.head(limit).to_dict("records")

    name_hit = df[df["name"].str.upper().str.contains(q, regex=False, na=False)]

    names = df["name"].str.upper().tolist()
    fuzzy_names = set(difflib.get_close_matches(q, names, n=limit, cutoff=0.6))
    fuzzy = df[df["name"].str.upper().isin(fuzzy_names)]

    import pandas as pd
    merged = pd.concat([name_hit, fuzzy]).drop_duplicates("tradingsymbol")
    return merged.head(limit)[
        ["tradingsymbol", "name", "amc", "plan", "scheme_type"]
    ].to_dict("records")


def handle_fund_search(args: dict, **kwargs) -> str:
    try:
        return _ok(search_schemes(args["query"], limit=int(args.get("limit", 5))))
    except Exception as e:
        return _err(e)


def handle_fund_quote(args: dict, **kwargs) -> str:
    try:
        from services.kite_data import kite_data
        codes = [str(c) for c in args["scheme_codes"]][:25]
        return _ok(kite_data.get_mf_quote(codes))
    except Exception as e:
        return _err(e)
```

- [ ] **Step 5: Register `fund_search` and `fund_quote` in `TOOLS`**

In `lex/tools/__init__.py`, add the import near the other tool imports (after the
`lex.tools.watchlist` import):

```python
from lex.tools.mutual_funds import handle_fund_search, handle_fund_quote
```

Then add these two entries to the `TOOLS` dict, right after the `symbol_search`
and before `market_quote` entries respectively is not required — append them
anywhere in the dict; put them directly after the `symbol_search` entry for
readability:

```python
    "fund_search": {
        "schema": {
            "name": "fund_search",
            "description": (
                "Look up mutual fund schemes by name or AMC. Use before any tool "
                "that needs an exact scheme_code (e.g. 'parag parikh flexi cap "
                "direct' -> scheme_code)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Scheme name or AMC"},
                    "limit": {"type": "integer", "description": "Max results (default 5)"},
                },
                "required": ["query"],
            },
        },
        "handler": handle_fund_search,
    },
    "fund_quote": {
        "schema": {
            "name": "fund_quote",
            "description": "Latest NAV, AMC, plan and scheme type for up to 25 mutual fund scheme_codes.",
            "parameters": {"type": "object", "properties": {
                "scheme_codes": {"type": "array", "items": {"type": "string"},
                                 "description": "Exact AMFI scheme codes (from fund_search)"}},
                "required": ["scheme_codes"]},
        },
        "handler": handle_fund_quote,
    },
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest tests/lex/test_mutual_funds.py -v`
Expected: PASS (all 6 tests)

- [ ] **Step 7: Run the full suite and lint**

Run: `pytest && ruff check .`
Expected: PASS — no regressions in the existing equity tests.

- [ ] **Step 8: Commit**

```bash
git add services/kite_data.py lex/tools/mutual_funds.py lex/tools/__init__.py tests/lex/test_mutual_funds.py
git commit -m "$(cat <<'EOF'
Add fund_search and fund_quote tools

Reuses the existing Kite auth session for MF scheme search and latest
NAV, mirroring lex/tools/market.py's symbol_search/market_quote shape.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: AMFI NAV history service + `fund_history` tool

**Files:**
- Create: `services/indian_data/mutual_funds.py`
- Create: `tests/services/__init__.py`
- Create: `tests/services/test_mutual_funds.py`
- Modify: `lex/tools/mutual_funds.py` (add `handle_fund_history`)
- Modify: `lex/tools/__init__.py` (import + register `fund_history`)
- Modify: `tests/lex/test_mutual_funds.py` (add `fund_history` tests)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `services.indian_data.mutual_funds.get_nav_history(scheme_code, from_date, to_date) -> list[dict]`
  (each dict `{"date": "YYYY-MM-DD", "nav": float}`, oldest first);
  `lex.tools.mutual_funds.handle_fund_history(args) -> str`. Task 5's fund
  research facts pass calls the `fund_history` tool by name (registered here);
  Task 4's `RESEARCH_FUND_TOOL_NAMES` includes `"fund_history"`.

- [ ] **Step 1: Write the failing tests for the AMFI parser and cache**

Create `tests/services/__init__.py` (empty file, matches `tests/lex/__init__.py`).

Create `tests/services/test_mutual_funds.py`:

```python
from services.indian_data import mutual_funds

_SAMPLE = (
    "120503;INF879O01019;INF879O01027;Parag Parikh Flexi Cap Fund - Direct Growth;74.5000;01-Aug-2026\n"
    "120503;INF879O01019;INF879O01027;Parag Parikh Flexi Cap Fund - Direct Growth;75.1000;02-Aug-2026\n"
    "999999;XXXXXXXXXXX;XXXXXXXXXXX;Some Other Fund;10.0000;01-Aug-2026\n"
)


def test_parse_nav_history_filters_by_scheme_code():
    rows = mutual_funds._parse_nav_history(_SAMPLE, "120503")
    assert rows == [{"date": "2026-08-01", "nav": 74.5}, {"date": "2026-08-02", "nav": 75.1}]


def test_parse_nav_history_ignores_other_schemes():
    rows = mutual_funds._parse_nav_history(_SAMPLE, "999999")
    assert rows == [{"date": "2026-08-01", "nav": 10.0}]


def test_parse_nav_history_no_match_is_empty():
    assert mutual_funds._parse_nav_history(_SAMPLE, "000000") == []


def test_get_nav_history_fetches_and_caches(monkeypatch):
    calls = []

    class FakeResp:
        text = _SAMPLE

        def raise_for_status(self):
            pass

    def fake_get(url, params=None, timeout=None):
        calls.append(params)
        return FakeResp()

    monkeypatch.setattr("requests.get", fake_get)
    rows = mutual_funds.get_nav_history("120503", "2026-08-01", "2026-08-02")
    assert rows == [{"date": "2026-08-01", "nav": 74.5}, {"date": "2026-08-02", "nav": 75.1}]
    assert len(calls) == 1

    # same range again is served from the on-disk cache, no second fetch
    mutual_funds.get_nav_history("120503", "2026-08-01", "2026-08-02")
    assert len(calls) == 1


def test_get_nav_history_falls_back_to_cache_on_fetch_failure(monkeypatch):
    def fail_get(*a, **k):
        raise RuntimeError("AMFI down")

    monkeypatch.setattr("requests.get", fail_get)
    assert mutual_funds.get_nav_history("120503", "2026-08-01", "2026-08-02") == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/services/test_mutual_funds.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.indian_data.mutual_funds'`

- [ ] **Step 3: Create `services/indian_data/mutual_funds.py`**

```python
"""Mutual fund NAV history from AMFI's official history report.

Kite's mf_instruments gives only the latest NAV (see services/kite_data.py's
get_mf_quote); a time series has to come from AMFI directly. AMFI has no
per-scheme endpoint — the history report returns every scheme for a date
range, so this fetches once and filters client-side. A historical NAV never
changes once published, so a fetched range is cached on disk with no TTL;
only genuinely new dates trigger another fetch.
"""
import json
import logging
from datetime import datetime, timedelta

import requests

from services.paths import lex_home

logger = logging.getLogger(__name__)

_HISTORY_URL = "https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx"
_CACHE_FILE = "mf_nav_cache.json"


def _cache_path():
    return lex_home() / _CACHE_FILE


def _load_cache() -> dict:
    p = _cache_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(cache: dict) -> None:
    _cache_path().write_text(json.dumps(cache), encoding="utf-8")


def get_nav_history(scheme_code: str, from_date: str, to_date: str) -> list[dict]:
    """NAV history for one scheme between two dates (YYYY-MM-DD), oldest first."""
    scheme_code = str(scheme_code)
    cache = _load_cache()
    cached_rows = cache.get(scheme_code, [])
    have_dates = {r["date"] for r in cached_rows}
    if _date_range(from_date, to_date) <= have_dates:
        return [r for r in cached_rows if from_date <= r["date"] <= to_date]

    try:
        resp = requests.get(_HISTORY_URL, params={
            "frmdt": _amfi_date(from_date), "todt": _amfi_date(to_date)},
            timeout=30)
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"AMFI NAV history fetch failed: {e}")
        return [r for r in cached_rows if from_date <= r["date"] <= to_date]

    rows = _parse_nav_history(resp.text, scheme_code)
    merged = {r["date"]: r for r in cached_rows}
    merged.update({r["date"]: r for r in rows})
    cache[scheme_code] = sorted(merged.values(), key=lambda r: r["date"])
    _save_cache(cache)
    return [r for r in cache[scheme_code] if from_date <= r["date"] <= to_date]


def _date_range(from_date: str, to_date: str) -> set:
    start = datetime.strptime(from_date, "%Y-%m-%d")
    end = datetime.strptime(to_date, "%Y-%m-%d")
    days = (end - start).days
    return {(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days + 1)}


def _amfi_date(iso_date: str) -> str:
    return datetime.strptime(iso_date, "%Y-%m-%d").strftime("%d-%b-%Y")


def _parse_nav_history(text: str, scheme_code: str) -> list[dict]:
    """Parse AMFI's semicolon-delimited report, filtered to one scheme code.

    Columns: Scheme Code;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;
    Scheme Name;Net Asset Value;Date
    """
    rows = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(";")]
        if len(parts) < 6 or parts[0] != scheme_code:
            continue
        try:
            nav = float(parts[4])
            date = datetime.strptime(parts[5], "%d-%b-%Y").strftime("%Y-%m-%d")
        except (ValueError, IndexError):
            continue
        rows.append({"date": date, "nav": nav})
    return rows
```

**Verify against the live endpoint before moving on**: run
`.venv/bin/python -c "import requests; r = requests.get('https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx', params={'frmdt': '01-Aug-2026', 'todt': '02-Aug-2026'}, timeout=30); print(r.text[:500])"`
and compare the real column layout against `_parse_nav_history`'s assumption
above. If AMFI's live format differs (a header row, a different delimiter, a
different column order), adjust `_parse_nav_history` to match — this is the one
piece of the design the spec explicitly flagged as unverified.

- [ ] **Step 4: Run the AMFI parser/cache tests to verify they pass**

Run: `pytest tests/services/test_mutual_funds.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Write the failing test for `handle_fund_history`**

In `tests/lex/test_mutual_funds.py`, change the import line to also pull in
`handle_fund_history`:

```python
from lex.tools.mutual_funds import (
    search_schemes, handle_fund_search, handle_fund_quote, handle_fund_history)
```

Add:

```python
def test_handle_fund_history_returns_rows():
    fake = [{"date": "2026-08-01", "nav": 74.5}]
    with patch("services.indian_data.mutual_funds.get_nav_history", return_value=fake):
        out = json.loads(handle_fund_history(
            {"scheme_code": "120503", "from_date": "2026-08-01", "to_date": "2026-08-02"}))
    assert out["success"] and out["data"]["rows"] == fake


def test_handle_fund_history_error_enveloped():
    with patch("services.indian_data.mutual_funds.get_nav_history",
              side_effect=RuntimeError("amfi down")):
        out = json.loads(handle_fund_history(
            {"scheme_code": "120503", "from_date": "2026-08-01", "to_date": "2026-08-02"}))
    assert out["success"] is False and "amfi down" in out["error"]


def test_tools_dict_registers_fund_history():
    from lex.tools import TOOLS
    assert TOOLS["fund_history"]["schema"]["name"] == "fund_history"
```

- [ ] **Step 6: Run the test to verify it fails**

Run: `pytest tests/lex/test_mutual_funds.py -v`
Expected: FAIL — `ImportError: cannot import name 'handle_fund_history'`

- [ ] **Step 7: Add `handle_fund_history` to `lex/tools/mutual_funds.py`**

Append to `lex/tools/mutual_funds.py`:

```python
def handle_fund_history(args: dict, **kwargs) -> str:
    try:
        from services.indian_data import mutual_funds
        scheme_code = str(args["scheme_code"])
        rows = mutual_funds.get_nav_history(scheme_code, args["from_date"], args["to_date"])
        return _ok({"scheme_code": scheme_code, "rows": rows})
    except Exception as e:
        return _err(e)
```

- [ ] **Step 8: Register `fund_history` in `TOOLS`**

In `lex/tools/__init__.py`, change the import to:

```python
from lex.tools.mutual_funds import (
    handle_fund_search, handle_fund_quote, handle_fund_history)
```

Add to `TOOLS`, after the `fund_quote` entry:

```python
    "fund_history": {
        "schema": {
            "name": "fund_history",
            "description": "Daily NAV history for one mutual fund scheme_code between two dates (YYYY-MM-DD), sourced from AMFI.",
            "parameters": {"type": "object", "properties": {
                "scheme_code": {"type": "string"},
                "from_date": {"type": "string"}, "to_date": {"type": "string"}},
                "required": ["scheme_code", "from_date", "to_date"]},
        },
        "handler": handle_fund_history,
    },
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `pytest tests/lex/test_mutual_funds.py tests/services/test_mutual_funds.py -v`
Expected: PASS (all tests)

- [ ] **Step 10: Run the full suite and lint**

Run: `pytest && ruff check .`
Expected: PASS

- [ ] **Step 11: Commit**

```bash
git add services/indian_data/mutual_funds.py tests/services/ lex/tools/mutual_funds.py lex/tools/__init__.py tests/lex/test_mutual_funds.py
git commit -m "$(cat <<'EOF'
Add AMFI NAV history service and fund_history tool

Kite only exposes the latest NAV; a time series needs AMFI's official
history report, fetched once per date range and cached on disk since
a published historical NAV never changes.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Mutual fund watchlist tools

**Files:**
- Create: `lex/tools/mf_watchlist.py`
- Modify: `lex/tools/__init__.py` (import + register `mf_watchlist_add`,
  `mf_watchlist_remove`, `mf_watchlist_status`)
- Test: `tests/lex/test_mf_watchlist.py`

**Interfaces:**
- Consumes: `services.kite_data.kite_data.get_mf_quote` (from Task 1).
- Produces: `lex.tools.mf_watchlist.handle_mf_watchlist_add/_remove/_status(args) -> str`;
  `lex.tools.mf_watchlist._load()/_save(items)` (test-only helpers, mirrors
  `lex.tools.watchlist._load`/`_save`). Nothing downstream depends on these.

- [ ] **Step 1: Write the failing tests**

Create `tests/lex/test_mf_watchlist.py`:

```python
import json
import time

from lex.tools import mf_watchlist


def _add(code):
    return json.loads(mf_watchlist.handle_mf_watchlist_add({"scheme_code": code, "note": "n"}))


def test_add_remove_roundtrip(lex_home_tmp):
    assert _add("120503")["success"]
    assert not _add("120503")["success"]  # duplicate refused
    assert json.loads(mf_watchlist.handle_mf_watchlist_remove({"scheme_code": "120503"}))["success"]
    assert not json.loads(mf_watchlist.handle_mf_watchlist_remove({"scheme_code": "120503"}))["success"]


def test_status_reports_move_and_updates_baseline(lex_home_tmp, monkeypatch):
    _add("120503")
    items = mf_watchlist._load()
    items[0].update({"last_nav": 70.0, "last_checked_ts": time.time() - 86400})
    mf_watchlist._save(items)
    monkeypatch.setattr("services.kite_data.kite_data.get_mf_quote",
                        lambda codes: {"120503": {"nav": 74.2}})
    data = json.loads(mf_watchlist.handle_mf_watchlist_status({}))["data"]
    entry = data["entries"][0]
    assert entry["scheme_code"] == "120503"
    assert entry["change_pct"] == 6.0
    assert entry["days_since_check"] == 1
    assert mf_watchlist._load()[0]["last_nav"] == 74.2  # baseline advanced


def test_status_empty(lex_home_tmp):
    data = json.loads(mf_watchlist.handle_mf_watchlist_status({}))["data"]
    assert data["entries"] == []


def test_tools_dict_registers_mf_watchlist():
    from lex.tools import TOOLS
    for name in ("mf_watchlist_add", "mf_watchlist_remove", "mf_watchlist_status"):
        assert TOOLS[name]["schema"]["name"] == name
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/lex/test_mf_watchlist.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lex.tools.mf_watchlist'`

- [ ] **Step 3: Create `lex/tools/mf_watchlist.py`**

```python
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
```

- [ ] **Step 4: Register the three tools in `TOOLS`**

In `lex/tools/__init__.py`, add the import after the `lex.tools.mutual_funds`
import:

```python
from lex.tools.mf_watchlist import (
    handle_mf_watchlist_add, handle_mf_watchlist_remove, handle_mf_watchlist_status)
```

Add to `TOOLS`, after the `fund_history` entry:

```python
    "mf_watchlist_add": {
        "schema": {
            "name": "mf_watchlist_add",
            "description": "Add a mutual fund scheme to your watchlist for tracking NAV changes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scheme_code": {"type": "string", "description": "AMFI scheme code (from fund_search)"},
                    "note": {"type": "string", "description": "Optional note"},
                },
                "required": ["scheme_code"],
            },
        },
        "handler": handle_mf_watchlist_add,
    },
    "mf_watchlist_remove": {
        "schema": {
            "name": "mf_watchlist_remove",
            "description": "Remove a mutual fund scheme from your watchlist.",
            "parameters": {
                "type": "object",
                "properties": {"scheme_code": {"type": "string"}},
                "required": ["scheme_code"],
            },
        },
        "handler": handle_mf_watchlist_remove,
    },
    "mf_watchlist_status": {
        "schema": {
            "name": "mf_watchlist_status",
            "description": ("What changed on the fund watchlist since last check: NAV moves "
                            "vs baseline. Call only when the user explicitly asks about their "
                            "fund watchlist — never on a bare greeting."),
            "parameters": {"type": "object", "properties": {}},
        },
        "handler": handle_mf_watchlist_status,
    },
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/lex/test_mf_watchlist.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Run the full suite and lint**

Run: `pytest && ruff check .`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add lex/tools/mf_watchlist.py lex/tools/__init__.py tests/lex/test_mf_watchlist.py
git commit -m "$(cat <<'EOF'
Add mutual fund watchlist tools

Separate mf_watchlist.json store, same "what changed since last
check" shape as the equity watchlist but diffing NAV.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Fund research prompts + delegate restricted tool set

**Files:**
- Modify: `lex/prompt.py` (add `FUND_FACTS_PASS`, `FUND_NARRATIVE_PASS`,
  `FUND_BEAR_PASS`, `FUND_SYNTHESIS_PROMPT`, `MUTUAL_FUNDS`; extend
  `_PASS_PROMPTS`; wire `MUTUAL_FUNDS` into `build_system_prompt`)
- Modify: `lex/delegate.py` (add `RESEARCH_FUND_TOOL_NAMES`; give `run_pass` a
  `tools` parameter defaulting to the existing `RESEARCH_TOOL_NAMES`)
- Create: `tests/lex/test_prompt.py`
- Modify: `tests/lex/test_delegate.py` (add fund-pass coverage)

**Interfaces:**
- Consumes: `lex.tools.TOOLS` (existing), including `fund_search`/`fund_quote`/
  `fund_history` from Tasks 1–2 (must exist in `TOOLS` for
  `RESEARCH_FUND_TOOL_NAMES <= set(TOOLS)` to hold).
- Produces: `lex.delegate.RESEARCH_FUND_TOOL_NAMES: frozenset`;
  `lex.delegate.run_pass(brief: str, pass_type: str, tools: frozenset = RESEARCH_TOOL_NAMES) -> str`
  (existing 2-arg call sites keep working unchanged); `lex.prompt.FUND_FACTS_PASS`,
  `lex.prompt.FUND_NARRATIVE_PASS`, `lex.prompt.FUND_BEAR_PASS`,
  `lex.prompt.FUND_SYNTHESIS_PROMPT` (all `str`); `lex.prompt.subagent_prompt("fund_facts"|"fund_narrative"|"fund_bear")`
  resolves the fund-specific prompts. Task 5's `lex/fund_research.py` calls
  `delegate.run_pass(brief, f"fund_{stage}", tools=delegate.RESEARCH_FUND_TOOL_NAMES)`
  and `prompt.FUND_SYNTHESIS_PROMPT` directly.

- [ ] **Step 1: Write the failing tests**

Create `tests/lex/test_prompt.py`:

```python
from lex import prompt


def test_build_system_prompt_includes_fund_playbook():
    text = prompt.build_system_prompt("")
    assert "fund_research" in text
    assert "mf_watchlist_status" in text


def test_subagent_prompt_resolves_fund_passes():
    assert prompt.FUND_FACTS_PASS in prompt.subagent_prompt("fund_facts")
    assert prompt.EVIDENCE_RULES in prompt.subagent_prompt("fund_facts")
    assert prompt.FUND_NARRATIVE_PASS in prompt.subagent_prompt("fund_narrative")
    assert prompt.FUND_BEAR_PASS in prompt.subagent_prompt("fund_bear")


def test_equity_passes_still_resolve():
    assert prompt.FACTS_PASS in prompt.subagent_prompt("facts")
    assert prompt.BEAR_PASS in prompt.subagent_prompt("bear")
```

Append to `tests/lex/test_delegate.py`:

```python
def test_fund_tool_set_is_read_only_fund_and_web():
    from lex.tools import TOOLS
    allowed = delegate.RESEARCH_FUND_TOOL_NAMES
    assert allowed <= set(TOOLS)
    forbidden = {"mf_watchlist_add", "mf_watchlist_remove", "mf_watchlist_status",
                 "fund_research", "portfolio_status", "memory_save"}
    assert not (allowed & forbidden)
    assert {"fund_search", "fund_quote", "fund_history", "web_search", "web_fetch"} <= allowed


def test_run_pass_accepts_a_tool_set_override(monkeypatch):
    from lex import prompt
    seen = {}

    def fake_run(client, model, messages, tools, **kw):
        seen["tools"] = set(tools)
        seen["system"] = messages[0]["content"]
        return "REPORT"

    monkeypatch.setattr("lex.agent.run", fake_run)
    monkeypatch.setattr("lex.llm.make_client", lambda: object())

    out = delegate.run_pass("Research 120503", "fund_bear", tools=delegate.RESEARCH_FUND_TOOL_NAMES)

    assert out == "REPORT"
    assert seen["tools"] == set(delegate.RESEARCH_FUND_TOOL_NAMES)
    assert prompt.FUND_BEAR_PASS in seen["system"]


def test_run_pass_default_tool_set_is_unchanged(monkeypatch):
    """The existing 2-arg call sites (fund_research aside) must keep working."""
    seen = {}

    def fake_run(client, model, messages, tools, **kw):
        seen["tools"] = set(tools)
        return "REPORT"

    monkeypatch.setattr("lex.agent.run", fake_run)
    monkeypatch.setattr("lex.llm.make_client", lambda: object())

    delegate.run_pass("Research INFY", "bear")
    assert seen["tools"] == set(delegate.RESEARCH_TOOL_NAMES)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/lex/test_prompt.py tests/lex/test_delegate.py -v`
Expected: FAIL — `AttributeError: module 'lex.prompt' has no attribute 'FUND_FACTS_PASS'`
and `AttributeError: module 'lex.delegate' has no attribute 'RESEARCH_FUND_TOOL_NAMES'`

- [ ] **Step 3: Add the fund pass prompts to `lex/prompt.py`**

Add after `BEAR_PASS` (before `SYNTHESIS_PROMPT`):

```python
FUND_FACTS_PASS = """You are the FACTS pass of a multi-pass mutual fund research run. \
Gather the record, do not tell a story and do not reach a verdict — a later pass does that.
Cover, each as its own labelled block: category, AMC and plan (fund_quote); expense ratio \
(fund_quote / web_search factsheet); NAV trend and returns over 1y/3y/5y versus the \
benchmark and category average (fund_history); portfolio composition — top holdings, sector \
and market-cap concentration (web_search/web_fetch factsheet); fund manager tenure and other \
funds they run (web_search/web_fetch); exit load and lock-in, if any.
Numbers with dates and units, sourced. Where a pillar is unavailable, say so explicitly \
rather than skipping it silently."""

FUND_NARRATIVE_PASS = """You are the NARRATIVE pass of a multi-pass mutual fund research \
run. The facts pass output is given to you below — build on it, do not re-fetch what it \
already established.
Cover: what this fund actually invests in and its stated mandate; how concentrated or \
diversified it is versus its category; whether performance is manager skill or category \
tailwind (check whether peers in the same category moved similarly); the catalysts that \
could change the return profile in the next 2-4 quarters (mandate change, manager change, \
AUM growth diluting small/mid-cap picks)."""

FUND_BEAR_PASS = """You are the BEAR pass of a multi-pass mutual fund research run. The \
earlier passes are given to you below. Your job is to attack them.
Build the strongest honest case against holding this fund: closet indexing (high \
correlation to the benchmark despite active fees), manager churn or a manager new to the \
mandate, high portfolio turnover and its tax drag, category-relative underperformance over \
the cycle, concentration risk, and whether the expense ratio is justified by the alpha \
actually delivered.
Rules: attack the SPECIFIC claims made above — quote the claim you are challenging and say \
what evidence would falsify it. Generic risk-boilerplate that would apply to any fund is a \
failed pass. Steelman, never strawman. Where the case for the fund survives your attack, \
say so.
Close with the three things most likely to make this a bad holding, ranked, each with \
likelihood and impact."""
```

Add after `SYNTHESIS_PROMPT`:

```python
FUND_SYNTHESIS_PROMPT = """You are Lex, synthesising a multi-pass mutual fund research run \
into one report. The facts, narrative and bear passes are given below.

Weigh them — do not concatenate. Where passes disagree, adjudicate and say why the losing \
side lost. The bear pass exists to be taken seriously: if it landed, the verdict must move.

Reply with a single JSON object, no prose around it, no markdown fence, with exactly these \
keys:
  category, expense_ratio, performance, portfolio_composition, fund_manager, risk_exit_load \
— each a markdown string (use "unknown — <which pillar was missing>" when the passes did \
not establish it)
  verdict — {"stance": "bullish|neutral|bearish", "confidence": "high|medium|low", \
"drivers": [str], "what_would_change_my_mind": [str], "summary": str}

"summary" is the one-paragraph answer a reader gets if they read nothing else: direction, \
the reason, and the biggest thing that could break it. Keep source tags from the passes on \
any claim that carries a number."""
```

Update `_PASS_PROMPTS`:

```python
_PASS_PROMPTS = {
    "facts": FACTS_PASS,
    "narrative": NARRATIVE_PASS,
    "bear": BEAR_PASS,
    "fund_facts": FUND_FACTS_PASS,
    "fund_narrative": FUND_NARRATIVE_PASS,
    "fund_bear": FUND_BEAR_PASS,
}
```

Add a `MUTUAL_FUNDS` playbook block after `RESEARCH` and before `ANALYSIS`:

```python
MUTUAL_FUNDS = """## Mutual fund research
Full analysis of a fund, or comparison of 2+ → fund_research (one call per scheme_code). \
It runs a facts pass, a narrative pass and an adversarial bear pass, then hands you a \
sectioned report — you do not need a separate bear-case call.
Quick factual lookup (current NAV, category, expense ratio) → answer inline with \
fund_quote/fund_search; never run fund_research for a single number.

## Fund research you've already done
Reports are saved per scheme. Before a re-run, check fund_research_history — if you looked \
at this scheme before, fund_research automatically works in update mode, so lead with what \
CHANGED rather than repeating the earlier brief.

## Fund watchlist ("what's new with my funds")
Call mf_watchlist_status only when asked something along these lines — not on a bare \
greeting."""
```

Update `build_system_prompt`:

```python
def build_system_prompt(memory_text: str) -> str:
    parts = [PERSONA, MARKET_PULSE, RESEARCH, MUTUAL_FUNDS, ANALYSIS,
             f"Today is {date.today().isoformat()} (IST)."]
    if memory_text.strip():
        parts.append("## What you remember about the user\n" + memory_text)
    return "\n\n".join(parts)
```

- [ ] **Step 4: Add `RESEARCH_FUND_TOOL_NAMES` and the `tools` parameter to `lex/delegate.py`**

Replace the file's content with:

```python
"""Research subagents: nested agent.run() over a restricted tool dict.

Restriction rationale (carried from the old design): research verdicts must
not anchor on current holdings, and fetched web content must never reach
anything stateful — so no portfolio, memory, watchlist, or nested delegation.
"""
RESEARCH_TOOL_NAMES = frozenset({
    "symbol_search", "market_quote", "price_history", "market_overview",
    "fundamentals", "market_events", "web_search", "web_fetch",
    # read-only market data, same as the rest: they expose company filings and
    # public market structure, nothing about the user. Saved-research tools
    # (research_history/research_get) stay out on purpose — a pass that can
    # read our previous verdict stops being an independent look.
    "corporate_actions", "ownership_signals", "peer_comparison", "technicals"})

# Same isolation rationale, fund side: read-only fund/web data only, no
# mf_watchlist, no fund_research_history/get, no recursive delegation.
RESEARCH_FUND_TOOL_NAMES = frozenset({
    "fund_search", "fund_quote", "fund_history", "web_search", "web_fetch"})


def run_pass(brief: str, pass_type: str, tools: frozenset = RESEARCH_TOOL_NAMES) -> str:
    """Run one research-pipeline pass over `tools`; return its report text."""
    from lex import agent, llm, prompt
    from lex.tools import TOOLS
    sub_tools = {k: v for k, v in TOOLS.items() if k in tools}
    messages = [
        {"role": "system", "content": prompt.subagent_prompt(pass_type)},
        {"role": "user", "content": brief},
    ]
    return agent.run(llm.make_client(), llm.default_model(), messages, sub_tools)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/lex/test_prompt.py tests/lex/test_delegate.py -v`
Expected: PASS (all tests, including the pre-existing `test_delegate.py` tests)

- [ ] **Step 6: Run the full suite and lint**

Run: `pytest && ruff check .`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add lex/prompt.py lex/delegate.py tests/lex/test_prompt.py tests/lex/test_delegate.py
git commit -m "$(cat <<'EOF'
Add fund research prompts and a restricted fund delegate tool set

run_pass gains an optional tools parameter (default preserves the
existing equity behavior) so the fund pipeline can reuse the same
subagent harness with its own read-only tool set.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Fund research pipeline (`lex/fund_research.py`)

**Files:**
- Create: `lex/fund_research.py`
- Test: `tests/lex/test_fund_research.py`

**Interfaces:**
- Consumes: `lex.delegate.run_pass`, `lex.delegate.RESEARCH_FUND_TOOL_NAMES`,
  `lex.prompt.FUND_SYNTHESIS_PROMPT` (Task 4); `lex.fund_reports.delta_context`/`save`
  (Task 6 — this task's `run_and_save`/`handle_fund_research` import
  `lex.fund_reports` lazily, so Task 5 can be implemented and tested with
  `lex.fund_reports` monkeypatched before Task 6 exists; the module import
  itself only happens inside function bodies, matching `lex/research.py`'s
  pattern).
- Produces: `lex.fund_research.SECTIONS: tuple[str, ...]`;
  `lex.fund_research.run_research(scheme_code, brief="", depth="full", progress=None, context="") -> dict`;
  `lex.fund_research.run_and_save(scheme_code, brief="", depth="full", progress=None) -> dict`;
  `lex.fund_research.handle_fund_research(args: dict) -> str`. Task 6's
  `lex/tools/__init__.py` registration lazy-imports `handle_fund_research` from
  here; Task 6's `lex/fund_reports.py` imports `SECTIONS` from here.

- [ ] **Step 1: Write the failing tests**

Create `tests/lex/test_fund_research.py`:

```python
import json

import pytest

from lex import fund_research

_SYNTH = json.dumps({
    "category": "Flexi Cap",
    "expense_ratio": "0.62% direct",
    "performance": "18% CAGR 3y vs 14% category average",
    "portfolio_composition": "top 10 holdings 45% of AUM, financials-heavy",
    "fund_manager": "Rajeev Thakkar, 12y tenure",
    "risk_exit_load": "1% exit load within 365 days",
    "verdict": {"stance": "bullish", "confidence": "medium", "drivers": ["manager tenure"],
                "what_would_change_my_mind": ["manager exit"], "summary": "solid flexi cap pick"},
})


@pytest.fixture
def passes(monkeypatch):
    """Record every subagent pass; return canned text per pass type."""
    seen = []

    def fake_pass(brief, pass_type="general", tools=None):
        seen.append({"pass_type": pass_type, "brief": brief, "tools": tools})
        return f"{pass_type.upper()} OUTPUT"

    monkeypatch.setattr("lex.delegate.run_pass", fake_pass)
    return seen


@pytest.fixture
def synth(monkeypatch):
    seen = {}

    def fake_run(client, model, messages, tools, **kw):
        seen["messages"], seen["tools"] = messages, tools
        return seen.get("reply", _SYNTH)

    monkeypatch.setattr("lex.agent.run", fake_run)
    monkeypatch.setattr("lex.llm.make_client", lambda: object())
    monkeypatch.setattr("lex.llm.default_model", lambda: "m")
    return seen


def test_full_depth_runs_three_passes_with_fund_tool_set(passes, synth):
    from lex import delegate
    report = fund_research.run_research("120503")
    assert [p["pass_type"] for p in passes] == ["fund_facts", "fund_narrative", "fund_bear"]
    assert all(p["tools"] == delegate.RESEARCH_FUND_TOOL_NAMES for p in passes)
    assert report["scheme_code"] == "120503" and report["depth"] == "full"
    assert report["passes"]["bear"] == "FUND_BEAR OUTPUT"


def test_brief_depth_skips_narrative(passes, synth):
    report = fund_research.run_research("120503", depth="brief")
    assert [p["pass_type"] for p in passes] == ["fund_facts", "fund_bear"]
    assert report["depth"] == "brief"


def test_later_passes_receive_earlier_output(passes, synth):
    fund_research.run_research("120503", brief="expense ratio")
    facts, narrative, bear = passes
    assert "FUND_FACTS OUTPUT" not in facts["brief"]
    assert "FUND_FACTS OUTPUT" in narrative["brief"]
    assert "FUND_FACTS OUTPUT" in bear["brief"] and "FUND_NARRATIVE OUTPUT" in bear["brief"]
    assert all("expense ratio" in p["brief"] for p in passes)


def test_synthesis_sees_all_passes_and_no_tools(passes, synth):
    fund_research.run_research("120503")
    body = synth["messages"][-1]["content"]
    assert "FACTS PASS" in body and "BEAR PASS" in body
    assert synth["tools"] == {}


def test_synthesis_uses_fund_synthesis_prompt(passes, synth):
    from lex import prompt
    fund_research.run_research("120503")
    assert synth["messages"][0]["content"] == prompt.FUND_SYNTHESIS_PROMPT


def test_sections_parsed_from_synthesis(passes, synth):
    sections = fund_research.run_research("120503")["sections"]
    assert set(fund_research.SECTIONS) <= set(sections)
    assert sections["verdict"]["stance"] == "bullish"
    assert sections["category"] == "Flexi Cap"


def test_missing_keys_become_unknown_not_invented(passes, synth):
    synth["reply"] = json.dumps({"category": "Flexi Cap"})
    sections = fund_research.run_research("120503")["sections"]
    assert sections["category"] == "Flexi Cap"
    assert sections["expense_ratio"].startswith("unknown")
    assert sections["verdict"] == {}


def test_unparseable_synthesis_keeps_raw_text(passes, synth):
    synth["reply"] = "I could not produce JSON, sorry."
    sections = fund_research.run_research("120503")["sections"]
    assert sections["_raw"] == "I could not produce JSON, sorry."
    assert sections["verdict"] == {}


def test_progress_reports_every_stage(passes, synth):
    stages = []
    fund_research.run_research("120503", progress=stages.append)
    assert stages == ["facts", "narrative", "bear", "synthesis"]


def test_run_and_save_delegates_to_fund_reports(passes, synth, monkeypatch):
    calls = {}
    monkeypatch.setattr("lex.fund_reports.delta_context", lambda code: ("", None))
    monkeypatch.setattr("lex.fund_reports.save", lambda report: calls.setdefault("saved", report) and __import__("pathlib").Path("/tmp/x.json"))
    report = fund_research.run_and_save("120503")
    assert report["saved_to"] == "/tmp/x.json"
    assert report["delta_of"] is None
    assert calls["saved"]["scheme_code"] == "120503"


def test_handler_envelopes_success_and_failure(passes, synth, monkeypatch):
    monkeypatch.setattr("lex.fund_reports.delta_context", lambda code: ("", None))
    monkeypatch.setattr("lex.fund_reports.save", lambda report: __import__("pathlib").Path("/tmp/x.json"))
    out = json.loads(fund_research.handle_fund_research({"scheme_code": "120503"}))
    assert out["success"] and out["data"]["sections"]["verdict"]["stance"] == "bullish"
    assert "passes" not in out["data"]

    monkeypatch.setattr("lex.delegate.run_pass",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no key")))
    assert not json.loads(fund_research.handle_fund_research({"scheme_code": "120503"}))["success"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/lex/test_fund_research.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lex.fund_research'`

- [ ] **Step 3: Create `lex/fund_research.py`**

```python
"""Multi-pass mutual fund research: facts -> narrative -> bear -> parent synthesis.

Mirrors lex/research.py's pipeline shape (see that module's docstring for why
the bear pass is split from the narrative pass). Funds get their own section
schema here — there is no moat/promoter/peer-multiple analogue for a fund; see
docs/superpowers/specs/2026-08-04-mutual-fund-support-design.md.
"""
import json
import logging
import re
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

SECTIONS = ("category", "expense_ratio", "performance", "portfolio_composition",
            "fund_manager", "risk_exit_load", "verdict")

DEPTH_PASSES = {"brief": ("facts", "bear"),
                "full": ("facts", "narrative", "bear")}


def run_research(scheme_code: str, brief: str = "", depth: str = "full",
                 progress=None, context: str = "") -> dict:
    """Run the passes for `scheme_code` and return a structured report dict.

    Mirrors lex.research.run_research; see that function's docstring for the
    `progress`/`context` contract. Each stage is dispatched through
    delegate.run_pass with a "fund_" prefixed pass_type (fund_facts,
    fund_narrative, fund_bear) so lex.prompt resolves the fund-specific
    prompts, but is stored in `passes` under the unprefixed stage name
    ("facts", "narrative", "bear") so _pass_brief's earlier-pass lookup stays
    identical to the equity pipeline's.
    """
    from lex import delegate
    scheme_code = str(scheme_code).strip()
    stages = DEPTH_PASSES.get(depth) or DEPTH_PASSES["full"]
    passes: dict[str, str] = {}
    for stage in stages:
        _note(progress, stage)
        passes[stage] = delegate.run_pass(
            _pass_brief(stage, scheme_code, brief, context, passes), f"fund_{stage}",
            tools=delegate.RESEARCH_FUND_TOOL_NAMES)
    _note(progress, "synthesis")
    sections = _synthesize(scheme_code, brief, context, passes)
    return {
        "scheme_code": scheme_code,
        "brief": brief,
        "depth": "brief" if stages == DEPTH_PASSES["brief"] else "full",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sections": sections,
        "passes": passes,
    }


def _note(progress, stage: str) -> None:
    if progress is None:
        return
    try:
        progress(stage)
    except Exception:  # a progress display must never kill a research run
        logger.exception("progress callback failed for stage %s", stage)


def _pass_brief(stage: str, scheme_code: str, brief: str, context: str,
                passes: dict) -> str:
    parts = [f"Scheme code: {scheme_code} (AMFI)."]
    if brief:
        parts.append(f"The user specifically wants: {brief}")
    if context:
        parts.append(context)
    for earlier in ("facts", "narrative"):
        if stage != earlier and earlier in passes:
            parts.append(f"### {earlier.upper()} PASS OUTPUT\n{passes[earlier]}")
    return "\n\n".join(parts)


def _synthesize(scheme_code: str, brief: str, context: str, passes: dict) -> dict:
    from lex import agent, llm, prompt
    body = "\n\n".join(f"### {k.upper()} PASS\n{v}" for k, v in passes.items())
    user = f"Scheme code: {scheme_code} (AMFI)."
    if brief:
        user += f"\nThe user specifically wants: {brief}"
    if context:
        user += f"\n{context}"
    messages = [{"role": "system", "content": prompt.FUND_SYNTHESIS_PROMPT},
                {"role": "user", "content": f"{user}\n\n{body}"}]
    text = agent.run(llm.make_client(), llm.default_model(), messages, {})
    return _parse_sections(text)


def _parse_sections(text: str) -> dict:
    """Coerce the synthesis reply into the section schema; never raise."""
    parsed = _json_object(text)
    if parsed is None:
        logger.warning("fund synthesis did not return JSON — keeping raw text")
        sections = {s: "unknown — synthesis returned unstructured output"
                    for s in SECTIONS}
        sections["verdict"] = {}
        sections["_raw"] = text
        return sections
    sections = {}
    for name in SECTIONS:
        value = parsed.get(name)
        if name == "verdict":
            sections[name] = value if isinstance(value, dict) else {}
        else:
            sections[name] = value if isinstance(value, str) and value.strip() \
                else "unknown — not established by the research passes"
    return sections


def _json_object(text: str):
    """First JSON object in `text`, tolerating ``` fences and surrounding prose."""
    if not text:
        return None
    candidate = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)```", candidate, re.S)
    if fenced:
        candidate = fenced.group(1).strip()
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(candidate[start:end + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def run_and_save(scheme_code: str, brief: str = "", depth: str = "full",
                 progress=None) -> dict:
    """run_research, but picking up where the last report on this scheme left
    off and writing the result to disk. Shared by the tool handler."""
    from lex import fund_reports
    context, prior_at = fund_reports.delta_context(scheme_code)
    report = run_research(scheme_code, brief=brief, depth=depth, progress=progress,
                          context=context)
    report["delta_of"] = prior_at
    try:
        report["saved_to"] = str(fund_reports.save(report))
    except OSError as e:  # a full disk shouldn't throw away a finished run
        logger.exception("could not save fund report for %s", scheme_code)
        report["saved_to"] = None
        report["save_error"] = str(e)
    return report


def handle_fund_research(args: dict) -> str:
    from lex.tools.common import _ok, _err
    try:
        report = run_and_save(args["scheme_code"], brief=args.get("brief", ""),
                              depth=args.get("depth", "full"))
        return _ok({k: v for k, v in report.items() if k != "passes"})
    except Exception as e:
        return _err(e)
```

- [ ] **Step 4: Run the tests — expect the `run_and_save`/`handler` tests to fail on the missing `lex.fund_reports` module**

Run: `pytest tests/lex/test_fund_research.py -v`
Expected: the pipeline tests (`test_full_depth_runs_three_passes_with_fund_tool_set`
through `test_progress_reports_every_stage`) PASS; `test_run_and_save_delegates_to_fund_reports`
and `test_handler_envelopes_success_and_failure` FAIL with `ModuleNotFoundError:
No module named 'lex.fund_reports'` — expected, `lex.fund_reports` doesn't exist
yet, monkeypatching an attribute on a nonexistent module fails at `setattr` time.
Move on to Task 6, which creates it; re-run this file's full suite at the end of
Task 6.

- [ ] **Step 5: Run the full suite and lint**

Run: `pytest && ruff check .`
Expected: the two tests named above fail (same reason), everything else passes —
confirm no other regression before moving to Task 6.

- [ ] **Step 6: Commit**

```bash
git add lex/fund_research.py tests/lex/test_fund_research.py
git commit -m "$(cat <<'EOF'
Add the mutual fund research pipeline

Mirrors lex/research.py's three-pass facts/narrative/bear shape with
a fund-specific section schema (category, expense ratio, performance,
portfolio composition, fund manager, risk & exit load). Persistence
(lex.fund_reports) lands in the next commit; two tests here exercise
it via monkeypatch ahead of that module existing.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Fund report persistence (`lex/fund_reports.py`) + tool registration

**Files:**
- Create: `lex/fund_reports.py`
- Modify: `lex/tools/__init__.py` (lazy-import + register `fund_research`,
  `fund_research_history`, `fund_research_get`)
- Test: `tests/lex/test_fund_reports.py`

**Interfaces:**
- Consumes: `lex.fund_research.SECTIONS`, `lex.fund_research.handle_fund_research`
  (Task 5).
- Produces: `lex.fund_reports.save(report: dict) -> Path`;
  `lex.fund_reports.load(scheme_code, n=1) -> dict | None`;
  `lex.fund_reports.history(scheme_code, limit=10) -> list[dict]`;
  `lex.fund_reports.researched_schemes() -> list[str]`;
  `lex.fund_reports.delta_context(scheme_code) -> tuple[str, str | None]`;
  `lex.fund_reports.render(report, mode="full") -> str`;
  `lex.fund_reports.age(generated_at) -> str | None`;
  `lex.fund_reports.handle_fund_research_history(args) -> str`;
  `lex.fund_reports.handle_fund_research_get(args) -> str`. Nothing further
  depends on this task; it closes the loop Task 5 left open.

- [ ] **Step 1: Write the failing tests**

Create `tests/lex/test_fund_reports.py`:

```python
import json

import pytest

from lex import fund_reports, fund_research

_VERDICT = {"stance": "bullish", "confidence": "medium", "drivers": ["manager tenure"],
            "what_would_change_my_mind": ["manager exit"],
            "summary": "Solid flexi cap pick."}


def _report(scheme_code="120503", **over):
    report = {
        "scheme_code": scheme_code, "brief": "", "depth": "full",
        "generated_at": "2026-07-20T09:00:00+00:00",
        "sections": {
            "category": "Flexi Cap", "expense_ratio": "0.62% direct",
            "performance": "18% CAGR 3y", "portfolio_composition": "top 10 = 45% AUM",
            "fund_manager": "Rajeev Thakkar, 12y", "risk_exit_load": "1% exit load <365d",
            "verdict": dict(_VERDICT),
        },
        "passes": {"facts": "F", "narrative": "N", "bear": "B"},
    }
    report.update(over)
    return report


def test_save_writes_json_and_markdown(lex_home_tmp):
    path = fund_reports.save(_report())
    assert path.parent == lex_home_tmp / "mf_research" / "120503"
    assert json.loads(path.read_text(encoding="utf-8"))["scheme_code"] == "120503"
    md = path.with_suffix(".md").read_text(encoding="utf-8")
    assert "bullish" in md and "Solid flexi cap pick." in md


def test_load_is_newest_first_and_bounds_checked(lex_home_tmp):
    fund_reports.save(_report(generated_at="first"))
    fund_reports.save(_report(generated_at="second"))
    assert fund_reports.load("120503")["generated_at"] == "second"
    assert fund_reports.load("120503", 2)["generated_at"] == "first"
    assert fund_reports.load("120503", 3) is None
    assert fund_reports.load("NOSUCH") is None


def test_history_digest(lex_home_tmp):
    fund_reports.save(_report())
    fund_reports.save(_report())
    hist = fund_reports.history("120503")
    assert len(hist) == 2
    assert hist[0]["stance"] == "bullish" and hist[0]["confidence"] == "medium"
    assert hist[0]["summary"] == "Solid flexi cap pick."
    assert fund_reports.history("NOSUCH") == []


def test_delta_context_empty_on_first_look(lex_home_tmp):
    assert fund_reports.delta_context("120503") == ("", None)


def test_delta_context_carries_prior_verdict(lex_home_tmp):
    fund_reports.save(_report())
    context, prior_at = fund_reports.delta_context("120503")
    assert prior_at == "2026-07-20T09:00:00+00:00"
    assert "bullish" in context and "Solid flexi cap pick." in context
    assert "manager exit" in context
    assert "UPDATE" in context and "changed" in context


def test_render_brief_is_short_and_leads_with_the_answer(lex_home_tmp):
    md = fund_reports.render(_report(), mode="brief")
    assert md.startswith("# 120503 — bullish (confidence: medium)")
    assert "Solid flexi cap pick." in md
    assert "## Category" not in md


def test_render_full_has_every_section(lex_home_tmp):
    md = fund_reports.render(_report())
    for title in ("Category", "Expense ratio", "Performance", "Portfolio composition",
                  "Fund manager", "Risk & exit load", "Verdict drivers",
                  "What would change my mind"):
        assert f"### {title}" in md


def test_render_separates_facts_from_judgement(lex_home_tmp):
    md = fund_reports.render(_report())
    facts, interpretation, judgement = (md.index("## Facts"),
                                        md.index("## Interpretation"),
                                        md.index("## Judgement"))
    assert facts < interpretation < judgement
    assert md.index("### Category") < interpretation
    assert md.index("### Performance") < judgement


def test_render_survives_a_half_empty_report(lex_home_tmp):
    md = fund_reports.render({"scheme_code": "X", "sections": {"_raw": "model rambled"}})
    assert "no verdict" in md and "model rambled" in md


def test_researched_schemes(lex_home_tmp):
    assert fund_reports.researched_schemes() == []
    fund_reports.save(_report())
    fund_reports.save(_report(scheme_code="100001"))
    assert fund_reports.researched_schemes() == ["100001", "120503"]


def test_history_and_get_handlers(lex_home_tmp):
    fund_reports.save(_report())
    hist = json.loads(fund_reports.handle_fund_research_history({"scheme_code": "120503"}))
    assert hist["success"] and hist["data"]["reports"][0]["stance"] == "bullish"

    got = json.loads(fund_reports.handle_fund_research_get({"scheme_code": "120503"}))
    assert got["success"] and got["data"]["sections"]["verdict"]["stance"] == "bullish"
    assert "passes" not in got["data"]

    missing = json.loads(fund_reports.handle_fund_research_get({"scheme_code": "120503", "n": 9}))
    assert not missing["success"]


@pytest.fixture
def stub_passes(monkeypatch):
    monkeypatch.setattr("lex.delegate.run_pass", lambda brief, pass_type="fund_facts", tools=None: "X")
    monkeypatch.setattr("lex.llm.make_client", lambda: object())
    monkeypatch.setattr("lex.llm.default_model", lambda: "m")

    def fake_run(client, model, messages, tools, **kw):
        return json.dumps({"verdict": _VERDICT})

    monkeypatch.setattr("lex.agent.run", fake_run)


def test_fund_research_saves_and_then_runs_in_update_mode(lex_home_tmp, stub_passes):
    first = json.loads(fund_research.handle_fund_research({"scheme_code": "120503"}))["data"]
    assert first["delta_of"] is None
    assert first["saved_to"].endswith(".json")
    assert "passes" not in first

    second = json.loads(fund_research.handle_fund_research({"scheme_code": "120503"}))["data"]
    assert second["delta_of"] == first["generated_at"]
    assert len(fund_reports.history("120503")) == 2


def test_tools_dict_registers_fund_research_tools():
    from lex.tools import TOOLS
    for name in ("fund_research", "fund_research_history", "fund_research_get"):
        assert TOOLS[name]["schema"]["name"] == name
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/lex/test_fund_reports.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lex.fund_reports'`

- [ ] **Step 3: Create `lex/fund_reports.py`**

```python
"""Saved mutual fund research reports: one directory per scheme under lex_home().

Mirrors lex/reports.py — see that module's docstring for the flat-file
rationale. The fund section schema has no list-valued sections (no
risks/catalysts array the way an equity report does — risk considerations
live in the risk_exit_load markdown section instead), so rendering is a
subset of reports.py's.
"""
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from lex.fund_research import SECTIONS
from lex.paths import lex_home

logger = logging.getLogger(__name__)

_UNSAFE = re.compile(r"[^A-Z0-9._-]")


def _dir(scheme_code: str) -> Path:
    d = lex_home() / "mf_research" / _UNSAFE.sub("_", str(scheme_code).strip().upper())
    d.mkdir(parents=True, exist_ok=True)
    return d


def save(report: dict) -> Path:
    stem = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S%f")
    d = _dir(report["scheme_code"])
    path = d / f"{stem}.json"
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    (d / f"{stem}.md").write_text(render(report), encoding="utf-8")
    return path


def _files(scheme_code: str) -> list[Path]:
    return sorted(_dir(scheme_code).glob("*.json"), reverse=True)


def load(scheme_code: str, n: int = 1) -> dict | None:
    """The nth most recent report for `scheme_code` (1 = latest), or None."""
    files = _files(scheme_code)
    if n < 1 or n > len(files):
        return None
    try:
        return json.loads(files[n - 1].read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.exception("unreadable fund report %s", files[n - 1])
        return None


def history(scheme_code: str, limit: int = 10) -> list[dict]:
    """Newest-first digest of past reports: date, depth, verdict."""
    out = []
    for i in range(1, min(limit, len(_files(scheme_code))) + 1):
        report = load(scheme_code, i)
        if report is None:
            continue
        verdict = report.get("sections", {}).get("verdict") or {}
        out.append({"n": i, "generated_at": report.get("generated_at"),
                    "age": age(report.get("generated_at")),
                    "depth": report.get("depth"),
                    "stance": verdict.get("stance"),
                    "confidence": verdict.get("confidence"),
                    "summary": verdict.get("summary")})
    return out


def researched_schemes() -> list[str]:
    """Scheme codes with at least one saved report, alphabetically."""
    root = lex_home() / "mf_research"
    if not root.is_dir():
        return []
    return sorted(d.name for d in root.iterdir()
                  if d.is_dir() and any(d.glob("*.json")))


def delta_context(scheme_code: str) -> tuple[str, str | None]:
    """Prompt context asking what changed since the last report, if there is one."""
    prior = load(scheme_code)
    if prior is None:
        return "", None
    verdict = prior.get("sections", {}).get("verdict") or {}
    when = prior.get("generated_at") or "an earlier session"
    lines = [f"You already researched scheme {scheme_code} on {when}. That report "
             f"concluded: {verdict.get('stance', 'unknown')} "
             f"(confidence {verdict.get('confidence', 'unknown')})."]
    if verdict.get("summary"):
        lines.append(f"Its summary was: {verdict['summary']}")
    watch = verdict.get("what_would_change_my_mind") or []
    if watch:
        lines.append("It said these would change the view: " + "; ".join(map(str, watch)))
    lines.append("This run is an UPDATE. Concentrate on what has changed since then — "
                 "NAV/performance moves, manager or mandate changes, expense-ratio "
                 "revisions, whether any of the above triggers fired — and say "
                 "explicitly whether the verdict moves and why. Re-state settled "
                 "background only where it is needed to make the change legible.")
    return "\n".join(lines), prior.get("generated_at")


_TITLES = {"category": "Category", "expense_ratio": "Expense ratio",
           "performance": "Performance", "portfolio_composition": "Portfolio composition",
           "fund_manager": "Fund manager", "risk_exit_load": "Risk & exit load"}

# Same fact-vs-interpretation split as reports.py, adapted to the fund schema:
# category/expense_ratio/portfolio_composition are close to what a tool
# returned; performance/fund_manager/risk_exit_load lean on judgement.
_GROUPS = (("Facts", ("category", "expense_ratio", "portfolio_composition")),
           ("Interpretation", ("performance", "fund_manager", "risk_exit_load")))


def age(generated_at: str | None) -> str | None:
    """"today" / "3 days ago" — how stale the thing you're reading is."""
    if not generated_at:
        return None
    try:
        then = datetime.fromisoformat(str(generated_at))
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    days = (datetime.now(timezone.utc) - then).days
    if days < 0:
        return None
    return "today" if days == 0 else "1 day ago" if days == 1 else f"{days} days ago"


def render(report: dict, mode: str = "full") -> str:
    """Markdown for a stored report. brief = the answer; full = the whole file."""
    sections = report.get("sections") or {}
    verdict = sections.get("verdict") or {}
    stance = verdict.get("stance", "no verdict")
    confidence = verdict.get("confidence", "unknown")
    out = [f"# {report.get('scheme_code', '?')} — {stance} (confidence: {confidence})"]

    meta = [m for m in (report.get("generated_at"), age(report.get("generated_at")),
                        f"update on {report['delta_of']}" if report.get("delta_of") else None,
                        f"{report['depth']} run" if report.get("depth") else None)
            if m]
    if meta:
        out.append("*" + " · ".join(str(m) for m in meta) + "*")
    if verdict.get("summary"):
        out += ["", verdict["summary"]]

    if mode != "brief":
        for group, names in _GROUPS:
            bodies = [(name, sections.get(name)) for name in names if sections.get(name)]
            if not bodies:
                continue
            out += ["", f"## {group}"]
            for name, body in bodies:
                out += ["", f"### {_TITLES[name]}", str(body)]

    judgement = _judgement(verdict, mode)
    if judgement:
        if mode != "brief":
            out += ["", "## Judgement"]
        out += judgement

    if sections.get("_raw"):
        out += ["", "## Unstructured synthesis output", str(sections["_raw"])]
    return "\n".join(out).strip() + "\n"


def _judgement(verdict: dict, mode: str) -> list[str]:
    heading = "##" if mode == "brief" else "###"
    out: list[str] = []
    if mode != "brief":
        drivers = verdict.get("drivers") or []
        if drivers:
            out += ["", f"{heading} Verdict drivers"] + [f"- {d}" for d in drivers]
    watch = verdict.get("what_would_change_my_mind") or []
    if watch:
        out += ["", f"{heading} What would change my mind"] + [f"- {w}" for w in watch]
    return out


def handle_fund_research_history(args: dict) -> str:
    from lex.tools.common import _ok, _err
    try:
        scheme_code = str(args["scheme_code"]).strip()
        return _ok({"scheme_code": scheme_code, "reports": history(scheme_code)})
    except Exception as e:
        return _err(e)


def handle_fund_research_get(args: dict) -> str:
    from lex.tools.common import _ok, _err
    try:
        scheme_code = str(args["scheme_code"]).strip()
        report = load(scheme_code, int(args.get("n", 1)))
        if report is None:
            return _err(f"no saved report #{args.get('n', 1)} for {scheme_code}")
        return _ok({k: v for k, v in report.items() if k != "passes"})
    except Exception as e:
        return _err(e)
```

`SECTIONS` is imported but unused directly in this file except implicitly (the
schema shape is enforced by `fund_research._parse_sections`, not here) — keep the
import anyway: it documents the coupling between the two modules the way
`reports.py` does with `research.py`, and a future section-name change here would
otherwise be easy to miss.

- [ ] **Step 4: Register `fund_research`, `fund_research_history`, `fund_research_get` in `TOOLS`**

In `lex/tools/__init__.py`, add three lazy-import wrapper functions near the
existing `_lazy_deep_research`/`_lazy_research_history`/`_lazy_research_get`:

```python
def _lazy_fund_research(args):
    """Lazy import: lex.fund_research reaches back into this module's TOOLS dict."""
    from lex.fund_research import handle_fund_research
    return handle_fund_research(args)


def _lazy_fund_research_history(args):
    from lex.fund_reports import handle_fund_research_history
    return handle_fund_research_history(args)


def _lazy_fund_research_get(args):
    from lex.fund_reports import handle_fund_research_get
    return handle_fund_research_get(args)
```

Add to `TOOLS`, after the `mf_watchlist_status` entry:

```python
    "fund_research": {
        "schema": {
            "name": "fund_research",
            "description": ("Full multi-pass research on one mutual fund scheme: a facts "
                            "pass, a narrative pass, then an adversarial bear pass, "
                            "synthesised into a sectioned report with a verdict and "
                            "confidence. This is the tool for 'analyse this fund' / "
                            "comparisons (one call per scheme_code). It is slow and "
                            "expensive — never use it for a single NAV or ratio."),
            "parameters": {
                "type": "object",
                "properties": {
                    "scheme_code": {"type": "string", "description": "Exact AMFI scheme code"},
                    "brief": {"type": "string", "description": "Optional extra focus, e.g. 'the user cares about the expense ratio and manager tenure'"},
                    "depth": {"type": "string", "description": "full (default, 3 passes) | brief (facts + bear only, faster)"},
                },
                "required": ["scheme_code"],
            },
        },
        "handler": _lazy_fund_research,
    },
    "fund_research_history": {
        "schema": {
            "name": "fund_research_history",
            "description": ("Past fund_research reports saved for a scheme, newest first, "
                            "with their date and verdict. Check this before running "
                            "fund_research so you can talk about what changed instead of "
                            "starting from scratch."),
            "parameters": {"type": "object", "properties": {
                "scheme_code": {"type": "string", "description": "Exact AMFI scheme code"}},
                "required": ["scheme_code"]},
        },
        "handler": _lazy_fund_research_history,
    },
    "fund_research_get": {
        "schema": {
            "name": "fund_research_get",
            "description": ("Read back one saved fund research report in full (sections + "
                            "verdict). n=1 is the most recent; use fund_research_history to "
                            "see what exists."),
            "parameters": {"type": "object", "properties": {
                "scheme_code": {"type": "string", "description": "Exact AMFI scheme code"},
                "n": {"type": "integer", "description": "1 = latest (default)"}},
                "required": ["scheme_code"]},
        },
        "handler": _lazy_fund_research_get,
    },
```

- [ ] **Step 5: Run this task's tests and the deferred Task 5 tests together**

Run: `pytest tests/lex/test_fund_reports.py tests/lex/test_fund_research.py -v`
Expected: PASS (all tests in both files, including the two deferred from Task 5)

- [ ] **Step 6: Run the full suite and lint**

Run: `pytest && ruff check .`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add lex/fund_reports.py lex/tools/__init__.py tests/lex/test_fund_reports.py
git commit -m "$(cat <<'EOF'
Add fund report persistence and register the fund research tools

lex/fund_reports.py mirrors lex/reports.py against the fund section
schema, saved under ~/.lex/mf_research/<SCHEME_CODE>/. Registers
fund_research, fund_research_history, and fund_research_get in TOOLS.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Full-suite verification

**Files:** none created or modified — this task is a verification gate, not new
code. If it finds a problem, the fix belongs in the task that introduced it; come
back and amend that task's step rather than patching around it here.

**Interfaces:** none.

- [ ] **Step 1: Run the whole test suite**

Run: `pytest -v`
Expected: every test passes, including all pre-existing equity tests
(`test_market_tools.py`, `test_watchlist.py`, `test_reports.py`, `test_research.py`,
`test_delegate.py`, `test_quality_harness.py`, etc.) and every new mutual-fund
test added across Tasks 1–6. If anything fails, fix it in the task file that owns
the broken code and re-run this step — don't patch it from here.

- [ ] **Step 2: Run the linter**

Run: `ruff check .`
Expected: no findings. Every new `open()`/`read_text()`/`write_text()` call added
in Tasks 1–6 must carry `encoding="utf-8"` (the one lint rule this repo enforces,
`PLW1514`).

- [ ] **Step 3: Confirm the tool surface is complete**

Run:
```bash
.venv/bin/python -c "
from lex.tools import TOOLS
mf_tools = sorted(n for n in TOOLS if n.startswith(('fund_', 'mf_')))
print(mf_tools)
assert mf_tools == ['fund_history', 'fund_quote', 'fund_research',
                    'fund_research_get', 'fund_research_history', 'fund_search',
                    'mf_watchlist_add', 'mf_watchlist_remove', 'mf_watchlist_status']
print('OK — all 9 mutual fund tools registered')
"
```
Expected: prints the sorted list and `OK — all 9 mutual fund tools registered`.

- [ ] **Step 4: Commit if Step 1 or 2 required any fixes**

If no fixes were needed in this task, there is nothing to commit — the prior six
tasks already left a clean, fully-passing tree. If a fix was made here (it
shouldn't be, per the note above, but if a fix was applied directly rather than
going back), commit it:

```bash
git add -A
git commit -m "$(cat <<'EOF'
Fix full-suite regression found during mutual fund support verification

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```
