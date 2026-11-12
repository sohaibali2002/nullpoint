"""Engine contract, market-data container and shared grid / walk-forward orchestration.

The only engine-specific work an adapter must implement is ``run_backtest``. Grid search and
walk-forward are defined once here so every adapter produces structurally identical results and
validation never branches on engine.
"""

from __future__ import annotations

import hashlib
import itertools
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import numpy as np
import pandas as pd

from engine.metrics import TRADING_DAYS, Trade, compute_metrics
from engine.strategy import StrategySpec

OBJECTIVES = ("sharpe", "sortino", "calmar", "cagr", "total_return", "profit_factor")


class EngineUnavailable(RuntimeError):
    """Raised when an optional engine dependency is not installed."""


@dataclass
class MarketData:
    """Wide close-price panel plus optional OHLCV and a benchmark series."""

    close: pd.DataFrame
    open: pd.DataFrame | None = None
    high: pd.DataFrame | None = None
    low: pd.DataFrame | None = None
    volume: pd.DataFrame | None = None
    benchmark: pd.Series | None = None
    provider: str = "unknown"
    as_of: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.close, pd.DataFrame) or self.close.empty:
            raise ValueError("MarketData.close must be a non-empty DataFrame")
        if not isinstance(self.close.index, pd.DatetimeIndex):
            raise ValueError("MarketData.close must have a DatetimeIndex")
        if self.close.columns.has_duplicates:
            raise ValueError("MarketData.close has duplicate symbols")
        self.close = self.close.sort_index().astype(float)
        self.close.columns = [str(c).upper() for c in self.close.columns]
        for frame in (self.open, self.high, self.low, self.volume):
            if frame is not None:
                frame.columns = [str(c).upper() for c in frame.columns]
        if len(self.close) < 20:
            raise ValueError("MarketData needs at least 20 rows to backtest meaningfully")

    @property
    def symbols(self) -> list[str]:
        return list(self.close.columns)

    @property
    def index(self) -> pd.DatetimeIndex:
        return self.close.index

    def slice(self, start: int = 0, end: int | None = None) -> MarketData:
        end = len(self.close) if end is None else end
        if start < 0 or end > len(self.close) or start >= end:
            raise ValueError(f"invalid slice start={start} end={end} n={len(self.close)}")
        idx = self.close.index[start:end]
        return MarketData(
            close=self.close.iloc[start:end],
            open=None if self.open is None else self.open.iloc[start:end],
            high=None if self.high is None else self.high.iloc[start:end],
            low=None if self.low is None else self.low.iloc[start:end],
            volume=None if self.volume is None else self.volume.iloc[start:end],
            benchmark=None if self.benchmark is None else self.benchmark.reindex(idx),
            provider=self.provider,
            as_of=self.as_of,
        )

    def benchmark_series(self, symbol: str = "SPY") -> pd.Series | None:
        if self.benchmark is not None:
            return self.benchmark.reindex(self.close.index)
        if symbol.upper() in self.close.columns:
            return self.close[symbol.upper()]
        return None

    def returns(self) -> pd.DataFrame:
        return self.close.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)

    def data_hash(self) -> str:
        digest = hashlib.sha256()
        digest.update(str(list(self.close.columns)).encode())
        digest.update(str(self.close.index[0]).encode())
        digest.update(str(self.close.index[-1]).encode())
        digest.update(np.ascontiguousarray(self.close.to_numpy(dtype="float64")).tobytes())
        return digest.hexdigest()


@runtime_checkable
class TrialCounter(Protocol):
    """Monotone counter of every strategy/parameter combination ever evaluated."""

    def increment(self, n: int = 1) -> int: ...

    @property
    def value(self) -> int: ...


class MemoryTrialCounter:
    """Process-local counter for library use. The API always passes the DB-backed counter."""

    def __init__(self, start: int = 0) -> None:
        self._value = int(start)

    def increment(self, n: int = 1) -> int:
        if n < 0:
            raise ValueError("trial increments cannot be negative")
        self._value += int(n)
        return self._value

    @property
    def value(self) -> int:
        return self._value


@dataclass
class BacktestResult:
    spec_id: str
    engine: str
    params: dict[str, Any]
    returns: pd.Series
    equity: pd.Series
    positions: pd.DataFrame
    benchmark_returns: pd.Series
    gross_returns: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    costs: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    trades: list[Trade] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    seed: int = 7

    @property
    def sharpe(self) -> float:
        return float(self.metrics.get("sharpe", 0.0))

    @property
    def dates(self) -> list[str]:
        return [d.strftime("%Y-%m-%d") for d in self.returns.index]

    def to_dict(self, include_series: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "spec_id": self.spec_id,
            "engine": self.engine,
            "params": self.params,
            "metrics": self.metrics,
            "seed": self.seed,
            "n_trades": len(self.trades),
        }
        if include_series:
            payload.update(
                {
                    "dates": self.dates,
                    "returns": [float(x) for x in self.returns.fillna(0.0)],
                    "equity": [float(x) for x in self.equity.fillna(1.0)],
                    "costs": [float(x) for x in self.costs.fillna(0.0)],
                }
            )
        return payload


@dataclass
class GridResult:
    spec_id: str
    objective: str
    results: list[BacktestResult]
    best: BacktestResult | None
    best_params: dict[str, Any]
    trial_index: int
    failures: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> list[dict[str, Any]]:
        rows = []
        for result in self.results:
            rows.append(
                {
                    "params": result.params,
                    "score": float(result.metrics.get(self.objective, 0.0)),
                    "sharpe": result.metrics.get("sharpe", 0.0),
                    "cagr": result.metrics.get("cagr", 0.0),
                    "max_drawdown": result.metrics.get("max_drawdown", 0.0),
                    "n_trades": len(result.trades),
                }
            )
        return sorted(rows, key=lambda r: r["score"], reverse=True)


@dataclass
class WalkForwardReport:
    spec_id: str
    mode: str
    objective: str
    folds: list[dict[str, Any]]
    oos_returns: pd.Series
    metrics: dict[str, Any]
    param_stability: dict[str, Any]
    trial_index: int
    n_folds: int

    @property
    def oos_sharpe(self) -> float:
        return float(self.metrics.get("sharpe", 0.0))

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec_id": self.spec_id,
            "mode": self.mode,
            "objective": self.objective,
            "n_folds": self.n_folds,
            "folds": self.folds,
            "metrics": self.metrics,
            "param_stability": self.param_stability,
            "trial_index": self.trial_index,
        }


def expand_grid(grid: dict[str, Iterable[Any]]) -> list[dict[str, Any]]:
    if not grid:
        return [{}]
    keys = sorted(grid)
    values: list[list[Any]] = []
    for key in keys:
        options = list(grid[key])
        if not options:
            raise ValueError(f"grid parameter {key!r} has no values")
        values.append(options)
    return [dict(zip(keys, combo, strict=True)) for combo in itertools.product(*values)]


def _score(result: BacktestResult, objective: str, min_trades: int) -> float:
    if len(result.trades) < min_trades:
        return float("-inf")
    value = float(result.metrics.get(objective, 0.0))
    return value if np.isfinite(value) else float("-inf")


class EngineAdapter(ABC):
    """Common interface. Adapters differ only in how a single backtest is executed."""

    name: str = "base"
    supports_multi_asset: bool = True

    @abstractmethod
    def run_backtest(
        self,
        spec: StrategySpec,
        data: MarketData,
        params: dict[str, Any] | None = None,
        *,
        trial_counter: TrialCounter | None = None,
        cost_multiplier: float = 1.0,
        delay_bars: int = 1,
    ) -> BacktestResult:
        ...

    # ------------------------------------------------------------------ grid
    def run_grid(
        self,
        spec: StrategySpec,
        grid: dict[str, Iterable[Any]],
        data: MarketData,
        *,
        objective: str = "sharpe",
        trial_counter: TrialCounter | None = None,
        min_trades: int = 3,
        cost_multiplier: float = 1.0,
        max_trials: int | None = None,
    ) -> GridResult:
        if objective not in OBJECTIVES:
            raise ValueError(f"objective must be one of {OBJECTIVES}")
        combos = expand_grid(grid)
        if max_trials is not None:
            combos = combos[:max_trials]
        results: list[BacktestResult] = []
        failures: list[dict[str, Any]] = []
        for combo in combos:
            try:
                result = self.run_backtest(
                    spec.with_params(combo),
                    data,
                    trial_counter=trial_counter,
                    cost_multiplier=cost_multiplier,
                )
            except Exception as exc:  # noqa: BLE001 - a bad grid point must not kill the search
                failures.append({"params": combo, "error": str(exc)})
                # A failed attempt is still a trial: multiple-testing accounting must include it.
                if trial_counter is not None:
                    trial_counter.increment(1)
                continue
            results.append(result)
        scored = [r for r in results if _score(r, objective, min_trades) > float("-inf")]
        best = max(scored, key=lambda r: _score(r, objective, min_trades)) if scored else None
        return GridResult(
            spec_id=spec.name,
            objective=objective,
            results=results,
            best=best,
            best_params=dict(best.params) if best else {},
            trial_index=trial_counter.value if trial_counter else 0,
            failures=failures,
        )

    # -------------------------------------------------------- walk-forward
    def run_walk_forward(
        self,
        spec: StrategySpec,
        param_grid: dict[str, Iterable[Any]],
        data: MarketData,
        *,
        folds: int = 5,
        mode: str = "rolling",
        train_bars: int | None = None,
        test_bars: int | None = None,
        embargo_bars: int = 5,
        objective: str = "sharpe",
        min_trades: int = 3,
        trial_counter: TrialCounter | None = None,
        cost_multiplier: float = 1.0,
    ) -> WalkForwardReport:
        if mode not in {"rolling", "anchored"}:
            raise ValueError("mode must be 'rolling' or 'anchored'")
        n = len(data.close)
        test_bars = test_bars or max(20, n // (folds + 2))
        train_bars = train_bars or max(60, test_bars * 2)
        if test_bars * folds + 20 > n:
            raise ValueError(f"not enough data: n={n} for folds={folds} test_bars={test_bars}")

        fold_records: list[dict[str, Any]] = []
        oos_parts: list[pd.Series] = []
        chosen: list[dict[str, Any]] = []

        for i in range(folds):
            test_end = n - (folds - 1 - i) * test_bars
            test_start = test_end - test_bars
            train_end = test_start - max(0, embargo_bars)
            train_start = 0 if mode == "anchored" else max(0, train_end - train_bars)
            if train_end - train_start < 40 or test_start >= test_end:
                continue
            train = self.run_grid(
                spec,
                param_grid,
                data.slice(train_start, train_end),
                objective=objective,
                trial_counter=trial_counter,
                min_trades=min_trades,
                cost_multiplier=cost_multiplier,
            )
            if train.best is None:
                fold_records.append(
                    {
                        "fold": i,
                        "train": [str(data.index[train_start].date()), str(data.index[train_end - 1].date())],
                        "test": [str(data.index[test_start].date()), str(data.index[test_end - 1].date())],
                        "skipped": "no in-sample candidate met min_trades",
                    }
                )
                continue
            oos = self.run_backtest(
                spec.with_params(train.best_params),
                data.slice(test_start, test_end),
                trial_counter=trial_counter,
                cost_multiplier=cost_multiplier,
            )
            oos_parts.append(oos.returns)
            chosen.append(train.best_params)
            is_sharpe = float(train.best.metrics.get("sharpe", 0.0))
            oos_sharpe = float(oos.metrics.get("sharpe", 0.0))
            fold_records.append(
                {
                    "fold": i,
                    "train": [str(data.index[train_start].date()), str(data.index[train_end - 1].date())],
                    "test": [str(data.index[test_start].date()), str(data.index[test_end - 1].date())],
                    "best_params": train.best_params,
                    "is_metrics": {
                        "sharpe": is_sharpe,
                        "cagr": train.best.metrics.get("cagr"),
                        "n_trades": len(train.best.trades),
                    },
                    "oos_metrics": {
                        "sharpe": oos_sharpe,
                        "cagr": oos.metrics.get("cagr"),
                        "max_drawdown": oos.metrics.get("max_drawdown"),
                        "n_trades": len(oos.trades),
                    },
                    "efficiency": oos_sharpe / is_sharpe if abs(is_sharpe) > 1e-9 else 0.0,
                }
            )

        if oos_parts:
            oos_returns = pd.concat(oos_parts).sort_index()
            oos_returns = oos_returns[~oos_returns.index.duplicated(keep="first")]
            equity = (1.0 + oos_returns.fillna(0.0)).cumprod()
            benchmark = data.benchmark_series(spec.benchmark)
            bench_ret = (
                benchmark.reindex(oos_returns.index).pct_change(fill_method=None).fillna(0.0)
                if benchmark is not None
                else None
            )
            metrics = compute_metrics(oos_returns, equity, benchmark=bench_ret)
        else:
            oos_returns = pd.Series(dtype=float)
            metrics = compute_metrics(oos_returns, pd.Series(dtype=float))

        stability = parameter_stability(chosen)
        return WalkForwardReport(
            spec_id=spec.name,
            mode=mode,
            objective=objective,
            folds=fold_records,
            oos_returns=oos_returns,
            metrics=metrics,
            param_stability=stability,
            trial_index=trial_counter.value if trial_counter else 0,
            n_folds=len(fold_records),
        )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{type(self).__name__} name={self.name!r}>"


def parameter_stability(chosen_params: list[dict[str, Any]]) -> dict[str, Any]:
    """How often the optimizer picked the same region across folds."""
    if not chosen_params:
        return {"modal_share": 0.0, "n_distinct": 0, "params": {}}
    tokens = ["|".join(f"{k}={v}" for k, v in sorted(p.items())) for p in chosen_params]
    counts: dict[str, int] = {}
    for token in tokens:
        counts[token] = counts.get(token, 0) + 1
    modal, modal_count = max(counts.items(), key=lambda kv: kv[1])
    return {
        "modal_share": modal_count / len(tokens),
        "n_distinct": len(counts),
        "modal": modal,
        "params": chosen_params[-1],
    }


_ADAPTER_CACHE: dict[str, EngineAdapter] = {}


def get_adapter(name: str = "native") -> EngineAdapter:
    name = name.lower().strip()
    if name in _ADAPTER_CACHE:
        return _ADAPTER_CACHE[name]
    if name == "native":
        from engine.native import NativeAdapter

        adapter: EngineAdapter = NativeAdapter()
    elif name in {"vectorbt", "vbt"}:
        from engine.vectorbt_adapter import VectorbtAdapter

        adapter = VectorbtAdapter()
    elif name in {"backtesting", "backtesting.py", "bta"}:
        from engine.backtesting_adapter import BacktestingPyAdapter

        adapter = BacktestingPyAdapter()
    else:
        raise ValueError(f"unknown engine adapter {name!r}")
    _ADAPTER_CACHE[name] = adapter
    return adapter


def list_adapters() -> dict[str, dict[str, Any]]:
    availability: dict[str, dict[str, Any]] = {
        "native": {"available": True, "note": "NumPy/pandas reference engine (always available)"}
    }
    try:
        import vectorbt  # noqa: F401

        availability["vectorbt"] = {"available": True, "note": "primary engine"}
    except Exception as exc:  # noqa: BLE001
        availability["vectorbt"] = {
            "available": False,
            "note": f"install with `uv pip install -e '.[vbt]'` ({type(exc).__name__})",
        }
    try:
        import backtesting  # noqa: F401

        availability["backtesting"] = {"available": True, "note": "second adapter (pluggability)"}
    except Exception as exc:  # noqa: BLE001
        availability["backtesting"] = {
            "available": False,
            "note": f"install with `uv pip install -e '.[bta]'` ({type(exc).__name__})",
        }
    return availability


def annualized_from_equity(equity: pd.Series) -> float:
    if len(equity) < 2:
        return 0.0
    return float(equity.iloc[-1] ** (TRADING_DAYS / len(equity)) - 1.0)
