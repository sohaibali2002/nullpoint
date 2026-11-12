"""vectorbt adapter.

vectorbt is the production fill simulator: it handles target-percent orders, fees, slippage and
per-asset accounting. Signal generation, risk sizing and stop rules are shared with the native
engine so a strategy behaves identically across adapters; only the fill/portfolio layer differs.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from engine.base import BacktestResult, EngineAdapter, EngineUnavailable, MarketData, TrialCounter
from engine.features import build_features
from engine.metrics import compute_metrics
from engine.native import NativeAdapter, extract_trades
from engine.signal import SignalError, compile_signal, evaluate_signal
from engine.strategy import StrategySpec


class VectorbtAdapter(EngineAdapter):
    name = "vectorbt"
    supports_multi_asset = True

    def __init__(self) -> None:
        try:
            import vectorbt as vbt  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            raise EngineUnavailable(
                "vectorbt is not installed. Install the primary engine with: "
                "`uv pip install -e '.[vbt]'` (requires numba)."
            ) from exc
        self._vbt = vbt

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
        import vectorbt as vbt

        merged = {**spec.params, **(params or {})}
        close = data.close[spec.universe]
        benchmark = data.benchmark_series(spec.benchmark)
        features = build_features(close, spec.features, benchmark=benchmark)
        try:
            raw = evaluate_signal(compile_signal(spec.signal_code), features, close, merged)
        except SignalError:
            raise

        native = NativeAdapter()
        positions = native._apply_stops(close, native._targets(raw, spec, close, delay_bars), spec)
        positions = native._rebalance(positions, spec)
        positions = native._size(close, positions, spec)

        fee = (spec.costs.commission_bps + spec.costs.spread_bps) * cost_multiplier / 10_000.0
        slippage = spec.costs.slippage_bps * cost_multiplier / 10_000.0
        portfolio = vbt.Portfolio.from_orders(
            close=close,
            size=positions.diff().fillna(positions),
            size_type="targetpercent",
            direction="both",
            fees=fee,
            slippage=slippage,
            freq="1D",
            init_cash=1.0,
            cash_sharing=True,
        )
        net = pd.Series(portfolio.returns(), index=close.index).fillna(0.0)
        equity = (1.0 + net).cumprod()
        costs = native._costs(positions, spec, merged, cost_multiplier)
        bench_ret = (
            benchmark.reindex(close.index).pct_change(fill_method=None).fillna(0.0)
            if benchmark is not None
            else None
        )
        metrics = compute_metrics(net, equity, positions=positions, trades=extract_trades(positions, close), benchmark=bench_ret)
        metrics["engine_note"] = "fills simulated by vectorbt.from_orders (target percent)"
        if trial_counter is not None:
            trial_counter.increment(1)

        return BacktestResult(
            spec_id=spec.name,
            engine=self.name,
            params=merged,
            returns=net,
            equity=equity,
            positions=positions,
            benchmark_returns=bench_ret if bench_ret is not None else pd.Series(dtype=float),
            gross_returns=(positions * close.pct_change(fill_method=None).fillna(0.0)).sum(axis=1),
            costs=costs,
            trades=extract_trades(positions, close),
            metrics=metrics,
        )


__all__ = ["VectorbtAdapter", "np"]
