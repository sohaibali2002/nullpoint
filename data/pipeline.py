"""Assemble a multi-symbol price panel from free providers, with a labelled fallback.

Live data is fetched per symbol through the cached provider chain and validated. If a symbol cannot
be fetched (offline CI, provider outage, rate limit), a deterministic synthetic series is generated
for the *whole* panel and the dataset is marked ``synthetic=True``. Synthetic data is never
presented as market data: the flag travels into the API, the tearsheet and the experiment ledger.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from data.base import ProviderError
from data.lake import DataLake
from data.providers import get_provider
from data.quality import check_ohlcv
from engine.base import MarketData

DEFAULT_START = "2016-01-01"
DEFAULT_END = str(datetime.now(timezone.utc).date())


@dataclass
class Dataset:
    data: MarketData
    provider: str
    synthetic: bool
    quality: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    content_hash: str = ""
    fetched_at: str = ""
    lake_paths: list[str] = field(default_factory=list)

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "synthetic": self.synthetic,
            "symbols": self.data.symbols,
            "start": str(self.data.index[0].date()),
            "end": str(self.data.index[-1].date()),
            "rows": len(self.data.close),
            "content_hash": self.content_hash,
            "warnings": self.warnings,
            "fetched_at": self.fetched_at,
        }


def _synthetic_panel(
    symbols: list[str], start: str, end: str, seed: int = 17
) -> pd.DataFrame:
    index = pd.bdate_range(start=start, end=end)
    rng = np.random.default_rng(abs(hash(tuple(symbols))) % (2**32) if seed is None else seed)
    market = rng.normal(0.0003, 0.010, len(index))
    columns: dict[str, np.ndarray] = {}
    for i, symbol in enumerate(symbols):
        beta = 0.6 + 0.25 * (i % 4)
        idio_vol = 0.008 + 0.002 * (i % 3)
        drift = 0.0001 + 0.00005 * (i % 5)
        columns[symbol] = 100.0 * np.cumprod(
            1.0 + drift + beta * market + rng.normal(0.0, idio_vol, len(index))
        )
    return pd.DataFrame(columns, index=index)


def _fetch_symbol(
    symbol: str,
    start: str,
    end: str,
    lake: DataLake | None,
    cache_ttl_hours: float,
) -> tuple[pd.DataFrame, str]:
    errors: list[str] = []
    for name in ("yahoo", "stooq"):
        provider = get_provider(name)
        try:
            if lake is not None:
                frame, _, _ = lake.cached(
                    name,
                    symbol,
                    "1d",
                    lambda p=provider: p.fetch_bars(symbol, start, end),
                    ttl_hours=cache_ttl_hours,
                )
            else:
                frame = provider.fetch_bars(symbol, start, end)
            if frame is None or frame.empty:
                raise ProviderError("empty frame")
            return frame, name
        except Exception as exc:  # noqa: BLE001 - try the next free provider
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
    raise ProviderError(f"all providers failed for {symbol}: {errors}")


def load_universe(
    symbols: list[str],
    *,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    lake: DataLake | None = None,
    allow_network: bool = True,
    min_rows: int = 300,
    cache_ttl_hours: float = 24.0,
    benchmark: str | None = None,
    synthetic: bool = False,
) -> Dataset:
    """Load a validated close panel; falls back to labelled synthetic data if necessary."""
    symbols = [s.upper() for s in symbols]
    if not symbols:
        raise ValueError("universe is empty")
    warnings: list[str] = []
    quality: list[dict[str, Any]] = []
    lake_paths: list[str] = []

    volumes: dict[str, pd.Series] = {}
    if allow_network and not synthetic:
        closes: dict[str, pd.Series] = {}
        providers: set[str] = set()
        failures: list[str] = []
        for symbol in symbols:
            try:
                frame, provider_name = _fetch_symbol(symbol, start, end, lake, cache_ttl_hours)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{symbol}: {exc}")
                continue
            report = check_ohlcv(frame, symbol, provider_name)
            quality.append(report.as_dict())
            warnings.extend(f"{symbol}: {w}" for w in report.warnings)
            if not report.passed:
                failures.append(f"{symbol}: quality failed ({'; '.join(report.errors)})")
                continue
            closes[symbol] = frame["close"].rename(symbol)
            if "volume" in frame.columns:
                volumes[symbol] = frame["volume"].rename(symbol).fillna(0.0)
            providers.add(provider_name)
            if lake is not None:
                lake_paths.append(str(lake.path_for(provider_name, symbol)))
        if len(closes) == len(symbols) and closes:
            panel = pd.concat(closes.values(), axis=1).sort_index()
            panel = panel.ffill(limit=5)
            bench_symbol = benchmark or symbols[0]
            if bench_symbol in panel.columns:
                panel = panel.loc[panel[bench_symbol].notna()]
            panel = panel.dropna()
            if len(panel) >= min_rows:
                volume_panel = (
                    pd.concat(volumes.values(), axis=1).reindex(panel.index).fillna(0.0)
                    if volumes
                    else None
                )
                return Dataset(
                    data=MarketData(
                        close=panel,
                        volume=volume_panel,
                        benchmark=panel[bench_symbol] if bench_symbol in panel else None,
                    ),
                    provider="+".join(sorted(providers)),
                    synthetic=False,
                    quality=quality,
                    warnings=warnings,
                    content_hash=MarketData(close=panel).data_hash(),
                    fetched_at=datetime.now(timezone.utc).isoformat(),
                    lake_paths=lake_paths,
                )
            failures.append(f"only {len(panel)} aligned rows, need {min_rows}")
        warnings.append("Live data unavailable; using SYNTHETIC prices. " + "; ".join(failures[:3]))

    panel = _synthetic_panel(symbols, start, end)
    quality.append(
        {
            "symbol": symbols[0],
            "provider": "synthetic",
            "rows": int(len(panel)),
            "start": str(panel.index[0].date()),
            "end": str(panel.index[-1].date()),
            "status": "warn",
            "passed": True,
            "warnings": ["SYNTHETIC DATA: pipeline verification only"],
            "errors": [],
        }
    )
    bench_symbol = benchmark or symbols[0]
    synthetic_volume = pd.DataFrame(
        {symbol: panel[symbol] * 1_000_000 for symbol in panel.columns}, index=panel.index
    )
    return Dataset(
        data=MarketData(close=panel, volume=synthetic_volume, benchmark=panel[bench_symbol]),
        provider="synthetic",
        synthetic=True,
        quality=quality,
        warnings=warnings + ["SYNTHETIC DATA: results are for pipeline verification only, not market evidence."],
        content_hash=MarketData(close=panel).data_hash(),
        fetched_at=datetime.now(timezone.utc).isoformat(),
        lake_paths=[],
    )
