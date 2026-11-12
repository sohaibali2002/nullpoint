from __future__ import annotations

import httpx
import numpy as np
import pandas as pd
import pytest

from data.base import ProviderError
from data.lake import DataLake
from data.providers import (
    AlphaVantageProvider,
    BinanceProvider,
    FredProvider,
    StooqProvider,
    YahooProvider,
    default_provider_order,
    get_provider,
    provider_catalog,
)


def _client(handler) -> httpx.Client:  # type: ignore[no-untyped-def]
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_yahoo_parses_and_prefers_adjusted_close():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "query1.finance.yahoo.com" in str(request.url)
        return httpx.Response(
            200,
            json={
                "chart": {
                    "result": [
                        {
                            "timestamp": [1577836800, 1577923200, 1578009600],
                            "indicators": {
                                "quote": [
                                    {
                                        "open": [100.0, 101.0, 102.0],
                                        "high": [102.0, 103.0, 104.0],
                                        "low": [99.0, 100.0, 101.0],
                                        "close": [101.0, 102.0, 103.0],
                                        "volume": [1000, 1100, 1200],
                                    }
                                ],
                                "adjclose": [{"adjclose": [50.5, 51.0, 51.5]}],
                            },
                        }
                    ],
                    "error": None,
                }
            },
        )

    provider = YahooProvider(client=_client(handler))
    frame = provider.fetch_bars("SPY", "2020-01-01", "2020-01-10")
    assert len(frame) == 3
    assert frame["close"].iloc[0] == pytest.approx(50.5)
    assert frame.attrs["provider"] == "yahoo"
    assert "available_at" in frame.columns


def test_stooq_parses_csv_and_handles_missing():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="Date,Open,High,Low,Close,Volume\n2020-01-02,10,11,9,10.5,100\n2020-01-03,10.5,11.5,10,11,120\n",
        )

    provider = StooqProvider(client=_client(handler))
    frame = provider.fetch_bars("SPY", "2020-01-01", "2020-01-05")
    assert list(frame.columns[:5]) == ["open", "high", "low", "close", "volume"]
    assert frame["close"].iloc[-1] == pytest.approx(11.0)

    def empty(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="No data")

    from data.base import DataUnavailable

    with pytest.raises(DataUnavailable):
        StooqProvider(client=_client(empty)).fetch_bars("NOPE", "2020-01-01", "2020-01-05")


def test_fred_series_and_bars():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="DATE,DGS10\n2020-01-02,1.88\n2020-01-03,1.79\n2020-01-06,1.81\n")

    provider = FredProvider(client=_client(handler))
    series = provider.fetch_series("DGS10", "2020-01-01", "2020-01-10")
    assert series.iloc[0] == pytest.approx(1.88)
    bars = provider.fetch_bars("DGS10", "2020-01-01", "2020-01-10")
    assert bars["close"].iloc[-1] == pytest.approx(1.81)
    assert bars.attrs["availability_lag_days"] == 1


def test_binance_klines():
    rows = [
        [1577836800000, "7200.0", "7300.0", "7100.0", "7250.0", "100.0", 1577923199999, "0", 10, "0", "0", "0"],
        [1577923200000, "7250.0", "7350.0", "7200.0", "7300.0", "120.0", 1578009599999, "0", 12, "0", "0", "0"],
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert "BTCUSDT" in str(request.url)
        return httpx.Response(200, json=rows)

    provider = BinanceProvider(client=_client(handler))
    frame = provider.fetch_bars("BTC/USDT", "2020-01-01", "2020-01-05")
    assert len(frame) == 2
    assert frame["close"].iloc[0] == pytest.approx(7250.0)


def test_retry_on_server_error(monkeypatch):
    monkeypatch.setattr("data.base.time.sleep", lambda *_: None)
    state = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        if state["calls"] < 3:
            return httpx.Response(503, text="unavailable")
        return httpx.Response(200, text="Date,Open,High,Low,Close,Volume\n2020-01-02,1,1,1,1,1\n")

    provider = StooqProvider(client=_client(handler), max_retries=3)
    frame = provider.fetch_bars("SPY", "2020-01-01", "2020-01-05")
    assert len(frame) == 1
    assert state["calls"] == 3


def test_exhausted_retries_raise_provider_error(monkeypatch):
    monkeypatch.setattr("data.base.time.sleep", lambda *_: None)
    provider = StooqProvider(client=_client(lambda request: httpx.Response(500)), max_retries=2)
    with pytest.raises(ProviderError):
        provider.fetch_bars("SPY", "2020-01-01", "2020-01-05")


def test_keyed_provider_requires_key(monkeypatch):
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)
    provider = AlphaVantageProvider(client=_client(lambda request: httpx.Response(200, json={})))
    assert provider.available() is False
    from data.base import ProviderUnavailable

    with pytest.raises(ProviderUnavailable):
        provider.fetch_bars("SPY", "2020-01-01", "2020-01-10")


def test_catalog_exposes_licences_and_factory():
    catalog = provider_catalog()
    names = {entry["name"] for entry in catalog}
    assert {"yahoo", "stooq", "fred", "binance", "sec_edgar", "famafrench"} <= names
    yahoo = next(entry for entry in catalog if entry["name"] == "yahoo")
    assert "PERSONAL" in yahoo["license_note"]
    assert get_provider("stooq").name == "stooq"
    with pytest.raises(ValueError):
        get_provider("bloomberg")
    assert default_provider_order("BTC/USDT")[0] == "binance"
    assert default_provider_order("EURUSD=X")[0] == "yahoo"


def test_provider_cache_avoids_second_fetch(tmp_path):
    lake = DataLake(tmp_path)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            200,
            json={
                "chart": {
                    "result": [
                        {
                            "timestamp": [1577836800, 1577923200],
                            "indicators": {
                                "quote": [{"open": [1, 1], "high": [1, 1], "low": [1, 1], "close": [1, 1], "volume": [1, 1]}],
                                "adjclose": [{"adjclose": [1, 1]}],
                            },
                        }
                    ],
                    "error": None,
                }
            },
        )

    provider = YahooProvider(client=_client(handler))
    frame, meta, hit = lake.cached("yahoo", "SPY", "1d", lambda: provider.fetch_bars("SPY", "2020-01-01", "2020-01-10"))  # noqa: RUF059
    assert hit is False
    assert calls["n"] == 1
    _, _, hit2 = lake.cached("yahoo", "SPY", "1d", lambda: provider.fetch_bars("SPY", "2020-01-01", "2020-01-10"))
    assert hit2 is True
    assert calls["n"] == 1
    assert meta["attrs"]["provider"] == "yahoo"


def test_normalize_bars_rejects_garbage():
    from data.base import DataUnavailable, normalize_bars

    with pytest.raises(DataUnavailable):
        normalize_bars(pd.DataFrame(), provider="test", symbol="X")
    frame = pd.DataFrame(
        {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1]},
        index=pd.DatetimeIndex(["2020-01-02"]),
    )
    out = normalize_bars(frame, provider="test", symbol="x")
    assert out.attrs["symbol"] == "X"
    assert np.isfinite(out["close"]).all()
