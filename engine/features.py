"""Feature construction from a wide close-price panel.

Every builder uses trailing windows only. If a feature needs a future value it does not belong
here; leakage checks in ``engine.validation`` test that claim empirically.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

TRADING_DAYS = 252


class FeatureError(ValueError):
    """Raised for unknown or malformed feature definitions."""


def _window(cfg: dict[str, Any], default: int = 20) -> int:
    window = int(cfg.get("window", default))
    if window < 1:
        raise FeatureError("feature window must be >= 1")
    return window


def sma(close: pd.DataFrame, window: int) -> pd.DataFrame:
    return close.rolling(window, min_periods=max(2, window // 2)).mean()


def ema(close: pd.DataFrame, window: int) -> pd.DataFrame:
    return close.ewm(span=window, min_periods=max(2, window // 2), adjust=False).mean()


def momentum(close: pd.DataFrame, window: int) -> pd.DataFrame:
    return close.pct_change(window, fill_method=None)


def volatility(close: pd.DataFrame, window: int) -> pd.DataFrame:
    rets = close.pct_change(fill_method=None)
    return rets.rolling(window, min_periods=max(3, window // 2)).std(ddof=1) * np.sqrt(TRADING_DAYS)


def zscore(close: pd.DataFrame, window: int) -> pd.DataFrame:
    mean = close.rolling(window, min_periods=max(3, window // 2)).mean()
    std = close.rolling(window, min_periods=max(3, window // 2)).std(ddof=1)
    return (close - mean) / std.replace(0, np.nan)


def rsi(close: pd.DataFrame, window: int = 14) -> pd.DataFrame:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def cross_sectional_rank(close: pd.DataFrame, window: int) -> pd.DataFrame:
    perf = close.pct_change(window, fill_method=None)
    return perf.rank(axis=1, pct=True) - 0.5


def drawdown_feature(close: pd.DataFrame, window: int) -> pd.DataFrame:
    rolling_max = close.rolling(window, min_periods=max(3, window // 2)).max()
    return close / rolling_max - 1


def trend(close: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    fast = int(cfg.get("fast", 20))
    slow = int(cfg.get("slow", 100))
    return sma(close, fast) / sma(close, slow).replace(0, np.nan) - 1


def beta(close: pd.DataFrame, cfg: dict[str, Any], benchmark: pd.Series | None) -> pd.DataFrame:
    if benchmark is None:
        raise FeatureError("beta feature requires a benchmark series")
    window = _window(cfg, 60)
    rets = close.pct_change(fill_method=None)
    bench = benchmark.reindex(close.index).pct_change(fill_method=None)
    cov = rets.rolling(window, min_periods=window // 2).cov(bench)
    var = bench.rolling(window, min_periods=window // 2).var()
    return cov.div(var.replace(0, np.nan), axis=0)


BUILDERS = {
    "sma": lambda close, cfg, bench: sma(close, _window(cfg)),
    "ema": lambda close, cfg, bench: ema(close, _window(cfg)),
    "momentum": lambda close, cfg, bench: momentum(close, _window(cfg)),
    "volatility": lambda close, cfg, bench: volatility(close, _window(cfg)),
    "zscore": lambda close, cfg, bench: zscore(close, _window(cfg)),
    "rsi": lambda close, cfg, bench: rsi(close, _window(cfg, 14)),
    "rank": lambda close, cfg, bench: cross_sectional_rank(close, _window(cfg)),
    "drawdown": lambda close, cfg, bench: drawdown_feature(close, _window(cfg)),
    "trend": lambda close, cfg, bench: trend(close, cfg),
    "beta": beta,
}


def build_features(
    close: pd.DataFrame,
    specs: dict[str, dict[str, Any]],
    benchmark: pd.Series | None = None,
) -> dict[str, pd.DataFrame]:
    """Build every declared feature. Unknown types fail loudly instead of silently zeroing."""
    features: dict[str, pd.DataFrame] = {}
    for name, cfg in specs.items():
        kind = str(cfg.get("type", "")).lower()
        if kind not in BUILDERS:
            raise FeatureError(f"unknown feature type {kind!r} for {name!r}")
        frame = BUILDERS[kind](close, cfg, benchmark)
        features[name] = frame.reindex(index=close.index, columns=close.columns).replace(
            [np.inf, -np.inf], np.nan
        )
    return features
