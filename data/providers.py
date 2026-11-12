"""Free data providers with parsing, licensing metadata and graceful degradation.

Only free tiers are used. Every provider declares its licence constraints in ``ProviderMeta`` and
those notes are served to the UI, so a user cannot enable a source without seeing its terms.
"""

from __future__ import annotations

import io
import os
import zipfile
from dataclasses import dataclass, field
from typing import Any

import httpx
import numpy as np
import pandas as pd

from data.base import (
    BarProvider,
    DataUnavailable,
    ProviderError,
    ProviderMeta,
    ProviderUnavailable,
    normalize_bars,
    to_utc_timestamp,
)

YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
STOOQ_URL = "https://stooq.com/q/d/l/"
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
AV_URL = "https://www.alphavantage.co/query"
TIINGO_URL = "https://api.tiingo.com/tiingo/daily/{ticker}/prices"
POLYGON_URL = "https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}"
ALPACA_URL = "https://data.alpaca.markets/v2/stocks/{symbol}/bars"
BINANCE_URL = "https://api.binance.com/api/v3/klines"
SEC_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
FF_URL = (
    "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
    "F-F_Research_Data_Factors_daily_CSV.zip"
)


@dataclass
class YahooProvider(BarProvider):
    meta: ProviderMeta = field(
        default_factory=lambda: ProviderMeta(
            name="yahoo",
            requires_key=False,
            asset_classes=("equity", "etf", "fx", "index", "crypto"),
            license_note="Yahoo Finance public endpoints are for PERSONAL, non-commercial use. "
            "Do not redistribute data. Replace with a licensed feed before any commercial launch.",
            homepage="https://finance.yahoo.com",
            rate_limit_per_min=30,
        )
    )

    def fetch_bars(self, symbol: str, start: str | pd.Timestamp, end: str | pd.Timestamp, interval: str = "1d") -> pd.DataFrame:
        start_ts = int(to_utc_timestamp(start).timestamp())
        end_ts = int(to_utc_timestamp(end).timestamp())
        payload = self.get_json(
            YAHOO_URL.format(symbol=symbol),
            params={
                "period1": start_ts,
                "period2": end_ts,
                "interval": interval,
                "events": "div,splits",
                "includeAdjustedClose": "true",
            },
        )
        chart = (payload or {}).get("chart") or {}
        if chart.get("error"):
            raise ProviderError(f"yahoo: {chart['error']}")
        results = chart.get("result") or []
        if not results:
            raise DataUnavailable(f"yahoo: no result for {symbol}")
        result = results[0]
        timestamps = result.get("timestamp") or []
        if not timestamps:
            raise DataUnavailable(f"yahoo: no timestamps for {symbol}")
        quote = (result.get("indicators", {}).get("quote") or [{}])[0]
        adj = ((result.get("indicators", {}).get("adjclose") or [{}])[0] or {}).get("adjclose")
        frame = pd.DataFrame(
            {
                "open": quote.get("open"),
                "high": quote.get("high"),
                "low": quote.get("low"),
                "close": quote.get("close"),
                "volume": quote.get("volume"),
            },
            index=pd.to_datetime(timestamps, unit="s", utc=True).tz_convert(None),
        )
        if adj is not None and len(adj) == len(frame):
            frame["adj_close"] = adj
            frame["close"] = np.where(pd.notna(adj), adj, frame["close"])
        out = normalize_bars(frame, provider="yahoo", symbol=symbol, interval=interval, adjusted=True)
        return out


@dataclass
class StooqProvider(BarProvider):
    meta: ProviderMeta = field(
        default_factory=lambda: ProviderMeta(
            name="stooq",
            requires_key=False,
            asset_classes=("equity", "etf", "index", "fx"),
            license_note="Stooq free downloads are for personal use with rate limits; no redistribution. "
            "Daily data may be split-adjusted but is not dividend-adjusted.",
            homepage="https://stooq.com",
            rate_limit_per_min=20,
        )
    )

    def fetch_bars(self, symbol: str, start: str | pd.Timestamp, end: str | pd.Timestamp, interval: str = "1d") -> pd.DataFrame:
        stooq_symbol = symbol.lower()
        if "." not in stooq_symbol and "/" not in stooq_symbol and not stooq_symbol.endswith("=x"):
            stooq_symbol = f"{stooq_symbol}.us"
        response = self.get(
            STOOQ_URL,
            params={"s": stooq_symbol, "d1": str(pd.Timestamp(start).date()).replace("-", ""),
                    "d2": str(pd.Timestamp(end).date()).replace("-", ""), "i": "d"},
        )
        text = response.text.strip()
        if not text or text.lower().startswith("<") or "Date,Open" not in text:
            raise DataUnavailable(f"stooq: no CSV for {symbol}")
        frame = pd.read_csv(io.StringIO(text))
        frame.columns = [c.strip().lower() for c in frame.columns]
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame = frame.dropna(subset=["date"]).set_index("date")
        return normalize_bars(frame, provider="stooq", symbol=symbol, interval=interval, adjusted=False)


@dataclass
class FredProvider(BarProvider):
    meta: ProviderMeta = field(
        default_factory=lambda: ProviderMeta(
            name="fred",
            requires_key=False,
            asset_classes=("macro",),
            license_note="FRED series are subject to per-series copyright (e.g. S&P data is not "
            "redistributable). Free API key recommended; respect FRED terms of use.",
            homepage="https://fred.stlouisfed.org",
            rate_limit_per_min=60,
            point_in_time=True,
        )
    )
    api_key: str | None = None

    def fetch_series(self, series_id: str, start: str, end: str) -> pd.Series:
        params: dict[str, Any] = {"id": series_id, "cosd": str(pd.Timestamp(start).date()),
                                  "coed": str(pd.Timestamp(end).date())}
        if self.api_key:
            params["api_key"] = self.api_key
            params["file_type"] = "csv"
        response = self.get(FRED_CSV_URL, params=params)
        frame = pd.read_csv(io.StringIO(response.text))
        if frame.shape[1] < 2:
            raise DataUnavailable(f"fred: malformed CSV for {series_id}")
        frame.columns = ["date", "value"]
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
        series = frame.dropna(subset=["date"]).set_index("date")["value"].dropna()
        if series.empty:
            raise DataUnavailable(f"fred: no observations for {series_id}")
        return series

    def fetch_bars(self, symbol: str, start: str | pd.Timestamp, end: str | pd.Timestamp, interval: str = "1d") -> pd.DataFrame:
        series = self.fetch_series(symbol, str(start), str(end))
        frame = pd.DataFrame({"open": series, "high": series, "low": series, "close": series})
        frame["volume"] = np.nan
        out = normalize_bars(frame, provider="fred", symbol=symbol, interval=interval, adjusted=True,
                             available_at_lag_days=1)
        return out


@dataclass
class AlphaVantageProvider(BarProvider):
    meta: ProviderMeta = field(
        default_factory=lambda: ProviderMeta(
            name="alphavantage",
            requires_key=True,
            asset_classes=("equity", "etf", "fx", "crypto"),
            license_note="Free tier: 25 requests/day, 5/min, personal use. Attribution required; "
            "no redistribution of raw data.",
            homepage="https://www.alphavantage.co",
            rate_limit_per_min=5,
        )
    )
    api_key: str | None = None

    def available(self) -> bool:
        return bool(self.api_key or os.environ.get("ALPHAVANTAGE_API_KEY"))

    def _key(self) -> str:
        key = self.api_key or os.environ.get("ALPHAVANTAGE_API_KEY")
        if not key:
            raise ProviderUnavailable("alphavantage: ALPHAVANTAGE_API_KEY is not set")
        return key

    def fetch_bars(self, symbol: str, start: str | pd.Timestamp, end: str | pd.Timestamp, interval: str = "1d") -> pd.DataFrame:
        payload = self.get_json(
            AV_URL,
            params={
                "function": "TIME_SERIES_DAILY_ADJUSTED",
                "symbol": symbol,
                "outputsize": "full",
                "apikey": self._key(),
            },
        )
        if "Note" in payload or "Information" in payload:
            raise ProviderError(f"alphavantage: {payload.get('Note') or payload.get('Information')}")
        series = payload.get("Time Series (Daily)")
        if not series:
            raise DataUnavailable(f"alphavantage: no daily series for {symbol}")
        rows = []
        for date_str, values in series.items():
            rows.append(
                {
                    "date": date_str,
                    "open": values.get("1. open"),
                    "high": values.get("2. high"),
                    "low": values.get("3. low"),
                    "close": values.get("5. adjusted close") or values.get("4. close"),
                    "volume": values.get("6. volume"),
                }
            )
        frame = pd.DataFrame(rows)
        frame["date"] = pd.to_datetime(frame["date"])
        frame = frame.set_index("date").sort_index().loc[str(pd.Timestamp(start).date()) : str(pd.Timestamp(end).date())]
        return normalize_bars(frame, provider="alphavantage", symbol=symbol, interval=interval, adjusted=True)


@dataclass
class TiingoProvider(BarProvider):
    meta: ProviderMeta = field(
        default_factory=lambda: ProviderMeta(
            name="tiingo",
            requires_key=True,
            asset_classes=("equity", "etf"),
            license_note="Free tier is for internal/personal use with attribution; no redistribution.",
            homepage="https://www.tiingo.com",
            rate_limit_per_min=60,
        )
    )
    api_key: str | None = None

    def available(self) -> bool:
        return bool(self.api_key or os.environ.get("TIINGO_API_KEY"))

    def fetch_bars(self, symbol: str, start: str | pd.Timestamp, end: str | pd.Timestamp, interval: str = "1d") -> pd.DataFrame:
        token = self.api_key or os.environ.get("TIINGO_API_KEY")
        if not token:
            raise ProviderUnavailable("tiingo: TIINGO_API_KEY is not set")
        payload = self.get_json(
            TIINGO_URL.format(ticker=symbol.lower()),
            params={"startDate": str(pd.Timestamp(start).date()), "endDate": str(pd.Timestamp(end).date()),
                    "resampleFreq": interval},
            headers={"Authorization": f"Token {token}"},
        )
        if not payload:
            raise DataUnavailable(f"tiingo: no rows for {symbol}")
        frame = pd.DataFrame(payload)
        frame["date"] = pd.to_datetime(frame["date"], utc=True).dt.tz_convert(None)
        frame = frame.set_index("date")
        frame = frame.rename(columns={"adjClose": "adj_close"})
        if "adj_close" in frame:
            frame["close"] = frame["adj_close"]
        return normalize_bars(frame, provider="tiingo", symbol=symbol, interval=interval, adjusted=True)


@dataclass
class PolygonProvider(BarProvider):
    meta: ProviderMeta = field(
        default_factory=lambda: ProviderMeta(
            name="polygon",
            requires_key=True,
            asset_classes=("equity", "etf", "fx", "crypto"),
            license_note="Free tier: 5 calls/min, delayed data, personal use. Redistribution prohibited.",
            homepage="https://polygon.io",
            rate_limit_per_min=5,
        )
    )
    api_key: str | None = None

    def available(self) -> bool:
        return bool(self.api_key or os.environ.get("POLYGON_API_KEY"))

    def fetch_bars(self, symbol: str, start: str | pd.Timestamp, end: str | pd.Timestamp, interval: str = "1d") -> pd.DataFrame:
        key = self.api_key or os.environ.get("POLYGON_API_KEY")
        if not key:
            raise ProviderUnavailable("polygon: POLYGON_API_KEY is not set")
        payload = self.get_json(
            POLYGON_URL.format(ticker=symbol.upper(), start=str(pd.Timestamp(start).date()),
                               end=str(pd.Timestamp(end).date())),
            params={"adjusted": "true", "apiKey": key, "limit": 50000},
        )
        results = payload.get("results") or []
        if not results:
            raise DataUnavailable(f"polygon: no aggregates for {symbol}")
        frame = pd.DataFrame(results)
        frame["date"] = pd.to_datetime(frame["t"], unit="ms", utc=True).dt.tz_convert(None)
        frame = frame.set_index("date").rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
        return normalize_bars(frame, provider="polygon", symbol=symbol, interval=interval, adjusted=True)


@dataclass
class AlpacaProvider(BarProvider):
    meta: ProviderMeta = field(
        default_factory=lambda: ProviderMeta(
            name="alpaca",
            requires_key=True,
            asset_classes=("equity", "etf"),
            license_note="Free IEX feed: delayed/partial volume, personal use. SIP requires a paid plan.",
            homepage="https://alpaca.markets",
            rate_limit_per_min=200,
        )
    )
    api_key: str | None = None
    api_secret: str | None = None

    def available(self) -> bool:
        return bool(self.api_key or os.environ.get("ALPACA_API_KEY"))

    def fetch_bars(self, symbol: str, start: str | pd.Timestamp, end: str | pd.Timestamp, interval: str = "1d") -> pd.DataFrame:
        key = self.api_key or os.environ.get("ALPACA_API_KEY")
        secret = self.api_secret or os.environ.get("ALPACA_API_SECRET")
        if not key or not secret:
            raise ProviderUnavailable("alpaca: ALPACA_API_KEY / ALPACA_API_SECRET are not set")
        payload = self.get_json(
            ALPACA_URL.format(symbol=symbol.upper()),
            params={"timeframe": "1Day", "start": str(pd.Timestamp(start).date()),
                    "end": str(pd.Timestamp(end).date()), "adjustment": "all", "feed": "iex", "limit": 10000},
            headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
        )
        bars = payload.get("bars") or []
        if not bars:
            raise DataUnavailable(f"alpaca: no bars for {symbol}")
        frame = pd.DataFrame(bars)
        frame["date"] = pd.to_datetime(frame["t"], utc=True).dt.tz_convert(None)
        frame = frame.set_index("date")
        return normalize_bars(frame, provider="alpaca", symbol=symbol, interval=interval, adjusted=True)


@dataclass
class BinanceProvider(BarProvider):
    meta: ProviderMeta = field(
        default_factory=lambda: ProviderMeta(
            name="binance",
            requires_key=False,
            asset_classes=("crypto",),
            license_note="Public market-data endpoints; Binance terms restrict commercial use. "
            "Use ccxt/testnet for execution; this provider is read-only research data.",
            homepage="https://www.binance.com",
            rate_limit_per_min=60,
        )
    )

    def fetch_bars(self, symbol: str, start: str | pd.Timestamp, end: str | pd.Timestamp, interval: str = "1d") -> pd.DataFrame:
        pair = symbol.replace("/", "").replace("-", "").upper()
        params = {
            "symbol": pair,
            "interval": interval if interval in {"1d", "4h", "1h"} else "1d",
            "startTime": int(to_utc_timestamp(start).timestamp() * 1000),
            "endTime": int(to_utc_timestamp(end).timestamp() * 1000),
            "limit": 1000,
        }
        payload = self.get_json(BINANCE_URL, params=params)
        if not isinstance(payload, list) or not payload:
            raise DataUnavailable(f"binance: no klines for {symbol}")
        frame = pd.DataFrame(
            payload,
            columns=["open_time", "open", "high", "low", "close", "volume", "close_time",
                     "quote_volume", "trades", "taker_base", "taker_quote", "ignore"],
        )
        frame["date"] = pd.to_datetime(frame["open_time"], unit="ms", utc=True).dt.tz_convert(None)
        frame = frame.set_index("date")
        return normalize_bars(frame, provider="binance", symbol=symbol, interval=interval, adjusted=True)


@dataclass
class SecEdgarProvider(BarProvider):
    meta: ProviderMeta = field(
        default_factory=lambda: ProviderMeta(
            name="sec_edgar",
            requires_key=False,
            asset_classes=("fundamentals",),
            license_note="US SEC EDGAR data is public domain. A descriptive User-Agent with contact "
            "information is mandatory; keep requests under 10/second.",
            homepage="https://www.sec.gov/edgar/sec-api-documentation",
            rate_limit_per_min=300,
            point_in_time=True,
        )
    )
    user_agent: str = "Nullpoint research contact@example.com"

    def fetch_fundamentals(self, identifier: str) -> pd.DataFrame:
        cik = identifier.strip().lstrip("0").zfill(10)
        payload = self.get_json(SEC_URL.format(cik=cik), headers={"User-Agent": self.user_agent})
        facts = (payload.get("facts") or {}).get("us-gaap") or {}
        wanted = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "NetIncomeLoss",
                  "Assets", "Liabilities", "StockholdersEquity"]
        rows: list[dict[str, Any]] = []
        for concept in wanted:
            for unit, entries in (facts.get(concept) or {}).get("units", {}).items():
                if unit != "USD":
                    continue
                for entry in entries:
                    if entry.get("form") not in {"10-K", "10-Q"}:
                        continue
                    rows.append(
                        {
                            "end": entry.get("end"),
                            "filed": entry.get("filed"),
                            "available_at": entry.get("filed"),
                            "concept": concept,
                            "value": entry.get("val"),
                            "fy": entry.get("fy"),
                            "fp": entry.get("fp"),
                        }
                    )
        if not rows:
            raise DataUnavailable(f"sec_edgar: no us-gaap facts for CIK {cik}")
        frame = pd.DataFrame(rows)
        frame["end"] = pd.to_datetime(frame["end"], errors="coerce")
        frame["available_at"] = pd.to_datetime(frame["available_at"], errors="coerce")
        frame = frame.dropna(subset=["end", "available_at"]).sort_values("filed")
        pivot = frame.pivot_table(index=["end", "available_at"], columns="concept", values="value", aggfunc="last")
        pivot = pivot.reset_index().set_index("end").sort_index()
        pivot.attrs["point_in_time_column"] = "available_at"
        return pivot

    def fetch_bars(self, symbol: str, start: str | pd.Timestamp, end: str | pd.Timestamp, interval: str = "1d") -> pd.DataFrame:
        raise NotImplementedError("SEC EDGAR provides fundamentals, not price bars")


@dataclass
class FamaFrenchProvider(BarProvider):
    meta: ProviderMeta = field(
        default_factory=lambda: ProviderMeta(
            name="famafrench",
            requires_key=False,
            asset_classes=("factors",),
            license_note="Kenneth French Data Library: free for academic/research use; cite Fama & French. "
            "Do not redistribute the raw library commercially.",
            homepage="https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html",
            rate_limit_per_min=10,
            point_in_time=False,
        )
    )

    def fetch_factors(self, start: str | None = None, end: str | None = None) -> pd.DataFrame:
        response = self.get(FF_URL)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            name = next((n for n in archive.namelist() if n.lower().endswith(".csv")), None)
            if name is None:
                raise ProviderError("famafrench: zip did not contain a CSV")
            raw = archive.read(name).decode("utf-8", errors="ignore")
        lines = raw.splitlines()
        header_idx = next((i for i, line in enumerate(lines) if line.strip().lower().startswith(",mkt-rf")), None)
        if header_idx is None:
            raise ProviderError("famafrench: could not find header row")
        columns = [c.strip() for c in lines[header_idx].split(",")]
        records: list[list[str]] = []
        for line in lines[header_idx + 1 :]:
            parts = [p.strip() for p in line.split(",")]
            if not parts or not parts[0].isdigit() or len(parts[0]) != 8:
                break
            records.append(parts)
        frame = pd.DataFrame(records, columns=columns)
        frame["date"] = pd.to_datetime(frame.iloc[:, 0], format="%Y%m%d", errors="coerce")
        frame = frame.dropna(subset=["date"]).set_index("date")
        for column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame = frame / 100.0
        if start:
            frame = frame.loc[pd.Timestamp(start) :]
        if end:
            frame = frame.loc[: pd.Timestamp(end)]
        return frame.dropna(how="all")

    def fetch_bars(self, symbol: str, start: str | pd.Timestamp, end: str | pd.Timestamp, interval: str = "1d") -> pd.DataFrame:
        factors = self.fetch_factors(str(start), str(end))
        if symbol.upper() not in factors.columns:
            raise DataUnavailable(f"famafrench: unknown factor {symbol}")
        series = factors[symbol.upper()]
        frame = pd.DataFrame({"open": series, "high": series, "low": series, "close": series, "volume": np.nan})
        return normalize_bars(frame, provider="famafrench", symbol=symbol, interval=interval, adjusted=True)


PROVIDER_CLASSES: dict[str, type[BarProvider]] = {
    "yahoo": YahooProvider,
    "stooq": StooqProvider,
    "fred": FredProvider,
    "alphavantage": AlphaVantageProvider,
    "tiingo": TiingoProvider,
    "polygon": PolygonProvider,
    "alpaca": AlpacaProvider,
    "binance": BinanceProvider,
    "sec_edgar": SecEdgarProvider,
    "famafrench": FamaFrenchProvider,
}


def get_provider(
    name: str,
    *,
    client: httpx.Client | None = None,
    api_key: str | None = None,
    api_secret: str | None = None,
    user_agent: str | None = None,
) -> BarProvider:
    key = name.lower()
    if key not in PROVIDER_CLASSES:
        raise ValueError(f"unknown provider {name!r}; choose from {sorted(PROVIDER_CLASSES)}")
    cls = PROVIDER_CLASSES[key]
    kwargs: dict[str, Any] = {"client": client}
    if key == "alphavantage" or key == "tiingo" or key == "polygon":
        kwargs["api_key"] = api_key
    elif key == "alpaca":
        kwargs["api_key"], kwargs["api_secret"] = api_key, api_secret
    elif key == "fred":
        kwargs["api_key"] = api_key
    elif key == "sec_edgar" and user_agent:
        kwargs["user_agent"] = user_agent
    return cls(**kwargs)


def provider_catalog() -> list[dict[str, Any]]:
    catalog: list[dict[str, Any]] = []
    for cls in PROVIDER_CLASSES.values():
        try:
            catalog.append(cls().meta.as_dict())  # type: ignore[call-arg]
        except Exception:
            catalog.append({"name": cls.__name__, "requires_key": True, "available": False})
    return catalog


def default_provider_order(symbol: str) -> list[str]:
    upper = symbol.upper()
    if "/" in upper or upper.endswith("USDT"):
        return ["binance", "yahoo"]
    if upper.endswith("=X"):
        return ["yahoo", "stooq"]
    if upper.startswith("^") or upper.endswith("=F"):
        return ["yahoo", "stooq"]
    return ["yahoo", "stooq", "tiingo", "polygon", "alphavantage"]


def fetch_bars_any(
    symbol: str,
    start: str,
    end: str,
    *,
    providers: list[BarProvider] | None = None,
    interval: str = "1d",
) -> tuple[pd.DataFrame, str]:
    """Try providers in order; return the first successful frame and its name."""
    if providers is None:
        providers = [get_provider(name) for name in default_provider_order(symbol) if name in {"yahoo", "stooq", "binance"}]
    errors: list[str] = []
    for provider in providers:
        if not provider.available():
            errors.append(f"{provider.name}: unavailable (missing key)")
            continue
        try:
            frame = provider.fetch_bars(symbol, start, end, interval)
            if frame is not None and not frame.empty:
                return frame, provider.name
        except (ProviderError, NotImplementedError) as exc:
            errors.append(f"{provider.name}: {exc}")
            continue
    raise DataUnavailable(f"no provider returned data for {symbol}: {errors}")
