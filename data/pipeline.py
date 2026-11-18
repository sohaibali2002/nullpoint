"""Assemble a multi-symbol price panel from free providers.

Live data is fetched per symbol through the cached provider chain and validated. There is no
synthetic fallback: if a symbol cannot be fetched (provider outage, rate limit, network disabled)
the loader raises ``DataUnavailable`` so a backtest never runs on invented prices.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from typing import Any

import pandas as pd

from data.base import DataUnavailable, ProviderError
from data.lake import DataLake
from data.providers import get_provider
from data.quality import check_ohlcv
from engine.base import MarketData
from engine.performance import parallel_map

DEFAULT_START = "2016-01-01"
DEFAULT_END = str(datetime.now(UTC).date())


@dataclass
class Dataset:
    data: MarketData
    provider: str
    synthetic: bool = False
    quality: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    content_hash: str = ""
    fetched_at: str = ""
    lake_paths: list[str] = field(default_factory=list)
    point_in_time: bool = False
    survivorship_free: bool = False

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
            "point_in_time": self.point_in_time,
            "survivorship_free": self.survivorship_free,
            "bias_note": (
                "Prices are revised by the provider and the universe is not reconstructed from "
                "delisted constituents; treat these results as biased until a point-in-time "
                "universe is supplied."
                if not (self.point_in_time and self.survivorship_free)
                else "Point-in-time, survivorship-free data declared by the provider."
            ),
        }


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
                    partial(provider.fetch_bars, symbol, start, end),
                    ttl_hours=cache_ttl_hours,
                    start=start,
                    end=end,
                )
            else:
                frame = provider.fetch_bars(symbol, start, end)
            if frame is None or frame.empty:
                raise ProviderError("empty frame")
            return frame, name
        except Exception as exc:
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
    max_workers: int | None = None,
) -> Dataset:
    """Load a validated close panel from live providers or raise ``DataUnavailable``."""
    symbols = [s.upper() for s in symbols]
    if not symbols:
        raise ValueError("universe is empty")
    if not allow_network:
        raise DataUnavailable(
            "market data requires network access; set ALLOW_NETWORK=true or load a cached lake"
        )

    warnings: list[str] = []
    quality: list[dict[str, Any]] = []
    lake_paths: list[str] = []
    volumes: dict[str, pd.Series] = {}
    closes: dict[str, pd.Series] = {}
    providers: set[str] = set()
    failures: list[str] = []

    def fetch_one(symbol: str) -> dict[str, Any]:
        try:
            frame, provider_name = _fetch_symbol(symbol, start, end, lake, cache_ttl_hours)
            report = check_ohlcv(frame, symbol, provider_name)
            return {
                "symbol": symbol,
                "frame": frame,
                "provider": provider_name,
                "report": report,
                "path": str(lake.path_for(provider_name, symbol)) if lake is not None else "",
            }
        except Exception as exc:
            return {"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"}

    # Symbols are independent network calls.  Fetch them concurrently, bounded by the
    # detected CPU/cgroup budget, and process the outcomes in the caller's order.
    for outcome in parallel_map(
        fetch_one,
        symbols,
        max_workers=max_workers if max_workers else min(len(symbols), 8),
    ):
        symbol = str(outcome["symbol"])
        if "error" in outcome:
            failures.append(f"{symbol}: {outcome['error']}")
            continue
        frame = outcome["frame"]
        provider_name = str(outcome["provider"])
        report = outcome["report"]
        quality.append(report.as_dict())
        warnings.extend(f"{symbol}: {w}" for w in report.warnings)
        if not report.passed:
            failures.append(f"{symbol}: quality failed ({'; '.join(report.errors)})")
            continue
        closes[symbol] = frame["close"].rename(symbol)
        if "volume" in frame.columns:
            volumes[symbol] = frame["volume"].rename(symbol).fillna(0.0)
        providers.add(provider_name)
        if outcome.get("path"):
            lake_paths.append(str(outcome["path"]))

    if len(closes) != len(symbols) or not closes:
        raise DataUnavailable(
            "market data unavailable; no synthetic substitute is generated: "
            + "; ".join(failures[:4])
        )

    panel = pd.concat(closes.values(), axis=1).sort_index()
    panel = panel.ffill(limit=5)
    bench_symbol = benchmark or symbols[0]
    if bench_symbol in panel.columns:
        panel = panel.loc[panel[bench_symbol].notna()]
    panel = panel.dropna()
    if len(panel) < min_rows:
        raise DataUnavailable(
            f"only {len(panel)} aligned rows for {', '.join(symbols)}; need at least {min_rows}"
        )

    volume_panel = (
        pd.concat(volumes.values(), axis=1).reindex(panel.index).fillna(0.0) if volumes else None
    )
    return Dataset(
        data=MarketData(
            close=panel,
            volume=volume_panel,
            benchmark=panel.get(bench_symbol),
        ),
        provider="+".join(sorted(providers)),
        synthetic=False,
        quality=quality,
        warnings=warnings,
        content_hash=MarketData(close=panel).data_hash(),
        fetched_at=datetime.now(UTC).isoformat(),
        lake_paths=lake_paths,
    )
