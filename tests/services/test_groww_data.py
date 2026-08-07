import pytest

from services import groww_data as gd


class FakeGrowwAPI:
    EXCHANGE_NSE = "NSE"
    SEGMENT_CASH = "CASH"

    def __init__(self, token):
        self.token = token

    def get_instrument_by_exchange_and_trading_symbol(self, exchange, trading_symbol):
        assert exchange == "NSE"
        return {"trading_symbol": trading_symbol,
                "groww_symbol": f"NSE-{trading_symbol}",
                "exchange_token": "2885"}

    def get_quote(self, trading_symbol, exchange, segment):
        assert exchange == "NSE" and segment == "CASH"
        return {"last_price": 1234.5, "trading_symbol": trading_symbol}

    def get_historical_candles(self, exchange, segment, groww_symbol, start_time,
                                end_time, candle_interval):
        assert exchange == "NSE" and segment == "CASH" and candle_interval == "1day"
        return {"candles": [[start_time, end_time, groww_symbol]]}


@pytest.fixture(autouse=True)
def _fresh_singleton(monkeypatch):
    """GrowwDataService is a singleton (mirrors KiteDataService) — reset its
    class-level instance and the real module-level singleton's cached client
    between tests so one test's fake client can't leak into the next."""
    gd.GrowwDataService._instance = None
    gd.GrowwDataService._initialized = False
    gd.groww_data._client = None
    monkeypatch.setattr(gd, "get_or_renew_access_token", lambda: "fake-token")
    monkeypatch.setattr(gd, "GrowwAPI", FakeGrowwAPI)


def test_get_quotes_returns_raw_payload_keyed_by_input_symbol():
    service = gd.GrowwDataService()
    out = service.get_quotes(["RELIANCE"])
    assert out == {"RELIANCE": {"last_price": 1234.5, "trading_symbol": "RELIANCE"}}


def test_get_quotes_skips_a_symbol_that_fails(monkeypatch):
    class Flaky(FakeGrowwAPI):
        def get_quote(self, trading_symbol, exchange, segment):
            if trading_symbol == "BADSYM":
                raise RuntimeError("groww down")
            return super().get_quote(trading_symbol, exchange, segment)

    monkeypatch.setattr(gd, "GrowwAPI", Flaky)
    service = gd.GrowwDataService()
    out = service.get_quotes(["RELIANCE", "BADSYM"])
    assert "RELIANCE" in out
    assert "BADSYM" not in out


def test_get_quotes_strips_nse_prefix():
    service = gd.GrowwDataService()
    out = service.get_quotes(["NSE:RELIANCE"])
    assert out["NSE:RELIANCE"]["trading_symbol"] == "RELIANCE"


def test_get_historical_data_resolves_groww_symbol_and_fetches_candles():
    service = gd.GrowwDataService()
    out = service.get_historical_data("RELIANCE", "2026-07-01", "2026-07-10")
    assert out["candles"][0] == ["2026-07-01 00:00:00", "2026-07-10 23:59:59", "NSE-RELIANCE"]


def test_get_historical_data_rejects_unsupported_interval():
    service = gd.GrowwDataService()
    with pytest.raises(ValueError, match="Unsupported interval"):
        service.get_historical_data("RELIANCE", "2026-07-01", "2026-07-10", interval="week")
