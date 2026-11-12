"""Provider abstraction shared by every free data source.

Providers are small, testable HTTP clients. Caching, retries, rate limits and point-in-time
timestamps live here so a new provider only has to parse its payload.
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx
import numpy as np
import pandas as pd

OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]


class ProviderError(RuntimeError):
    """Base class for provider failures that should surface in the UI/data quality report."""


class DataUnavailable(ProviderError):
    """The provider has no data for this symbol/date range (do not retry)."""


class ProviderUnavailable(ProviderError):
    """Missing dependency or API key; the provider is listed but not usable."""


@dataclass(frozen=True)
class ProviderMeta:
    name: str
    requires_key: bool
    asset_classes: tuple[str, ...]
    license_note: str
    homepage: str
    rate_limit_per_min: int = 60
    point_in_time: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "requires_key": self.requires_key,
            "asset_classes": list(self.asset_classes),
            "license_note": self.license_note,
            "homepage": self.homepage,
            "rate_limit_per_min": self.rate_limit_per_min,
            "point_in_time": self.point_in_time,
        }


class RateLimiter:
    """Thread-safe token bucket; simple and good enough for a single worker process."""

    def __init__(self, per_minute: int, burst: int | None = None) -> None:
        self.rate = max(1, per_minute) / 60.0
        self.capacity = float(burst or max(1, per_minute))
        self._tokens = self.capacity
        self._updated = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, tokens: float = 1.0) -> None:
        with self._lock:
            now = time.monotonic()
            self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate)
            self._updated = now
            if self._tokens < tokens:
                wait = (tokens - self._tokens) / self.rate
                time.sleep(min(wait, 5.0))
                self._tokens = 0.0
            else:
                self._tokens -= tokens


def normalize_bars(
    frame: pd.DataFrame,
    *,
    provider: str,
    symbol: str,
    interval: str = "1d",
    adjusted: bool = True,
    available_at_lag_days: int = 0,
) -> pd.DataFrame:
    """Coerce any provider payload into a canonical bar frame with point-in-time metadata."""
    if frame is None or frame.empty:
        raise DataUnavailable(f"{provider}: no bars returned for {symbol}")
    df = frame.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_localize(None)
            df = df.set_index("date")
        else:
            raise ProviderError(f"{provider}: response for {symbol} has no datetime index")
    index = pd.DatetimeIndex(df.index)
    if index.tz is not None:
        index = index.tz_convert(None)
    df.index = index.normalize()
    df.index.name = "date"
    df = df[~df.index.duplicated(keep="last")].sort_index()

    for column in OHLCV_COLUMNS:
        if column not in df.columns:
            if column == "volume":
                df[column] = np.nan
            else:
                raise ProviderError(f"{provider}: missing {column} for {symbol}")
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df = df[OHLCV_COLUMNS].astype(float)
    df = df[df["close"].notna() & (df["close"] > 0)]
    if df.empty:
        raise DataUnavailable(f"{provider}: all bars invalid for {symbol}")
    df.attrs.update(
        {
            "provider": provider,
            "symbol": symbol.upper(),
            "interval": interval,
            "adjusted": adjusted,
            "fetched_at": pd.Timestamp.utcnow().isoformat(),
            "point_in_time": available_at_lag_days == 0 and provider in {"sec_edgar"},
            "availability_lag_days": available_at_lag_days,
        }
    )
    df["available_at"] = pd.DatetimeIndex(df.index) + pd.Timedelta(days=available_at_lag_days)
    return df


@dataclass
class BarProvider(ABC):
    meta: ProviderMeta
    client: httpx.Client | None = None
    timeout: float = 20.0
    max_retries: int = 3
    _limiter: RateLimiter | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self._limiter is None:
            self._limiter = RateLimiter(self.meta.rate_limit_per_min)

    @property
    def name(self) -> str:
        return self.meta.name

    def available(self) -> bool:
        return True

    def _http(self) -> httpx.Client:
        if self.client is None:
            self.client = httpx.Client(
                timeout=self.timeout,
                headers={
                    "User-Agent": "Nullpoint/0.1 (research; contact@nullpoint.dev)",
                    "Accept": "application/json,text/csv,*/*",
                },
                follow_redirects=True,
            )
        return self.client

    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        assert self._limiter is not None
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            self._limiter.acquire()
            try:
                response = self._http().get(url, params=params, headers=headers)
            except httpx.HTTPError as exc:
                last_error = exc
                time.sleep(min(2**attempt, 4))
                continue
            if response.status_code == 429 or response.status_code >= 500:
                last_error = ProviderError(
                    f"{self.name}: HTTP {response.status_code} for {response.request.url}"
                )
                time.sleep(min(2**attempt, 8))
                continue
            if response.status_code == 404:
                raise DataUnavailable(f"{self.name}: 404 for {response.request.url}")
            if response.status_code >= 400:
                raise ProviderError(
                    f"{self.name}: HTTP {response.status_code} for {response.request.url}"
                )
            return response
        raise ProviderError(f"{self.name}: request failed after {self.max_retries} attempts: {last_error}")

    def get_json(self, url: str, **kwargs: Any) -> Any:
        return self.get(url, **kwargs).json()

    @abstractmethod
    def fetch_bars(
        self, symbol: str, start: str | pd.Timestamp, end: str | pd.Timestamp, interval: str = "1d"
    ) -> pd.DataFrame:
        ...

    def fetch_series(self, series_id: str, start: str, end: str) -> pd.Series:
        raise NotImplementedError(f"{self.name} does not provide macro series")

    def fetch_fundamentals(self, identifier: str) -> pd.DataFrame:
        raise NotImplementedError(f"{self.name} does not provide fundamentals")


def to_utc_timestamp(value: str | pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tz is None else ts.tz_convert("UTC")
