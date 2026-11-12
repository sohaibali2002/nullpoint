"""backtesting.py adapter — the pluggability proof and licence fallback.

Vendor constraint: backtesting.py simulates one instrument per run. For multi-asset strategies this
adapter is rejected explicitly instead of silently producing a different strategy. Grid and
walk-forward still work because they are implemented once in ``EngineAdapter``.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from engine.base import BacktestResult, EngineAdapter, EngineUnavailable, MarketData, TrialCounter
from engine.features import build_features
from engine.metrics import compute_metrics
from engine.native import NativeAdapter, extract_trades
from engine.signal import SignalError, compile_signal, evaluate_signal
from engine.strategy import StrategySpec


class BacktestingPyAdapter(EngineAdapter):
    name = "backtesting"
    supports_multi_asset = False

    def __init__(self) -> None:
        try:
            import backtesting  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            raise EngineUnavailable(
                "backtesting.py is not installed. Install the second adapter with: "
                "`uv pip install -e '.[bta]'`."
            ) from exc

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
        from backtesting import Backtest, Strategy as BtStrategy

        if len(spec.universe) != 1:
            raise ValueError("backtesting.py adapter supports exactly one symbol per run")
        symbol = spec.universe[0]
        merged = {**spec.params, **(params or {})}
        close = data.close[[symbol]]
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
        targets = positions[symbol].reindex(close.index).fillna(0.0)

        frame = pd.DataFrame(
            {
                "Open": close[symbol],
                "High": close[symbol],
                "Low": close[symbol],
                "Close": close[symbol],
                "Volume": 1_000_000,
                "Target": targets,
            },
            index=close.index,
        )
        commission = (spec.costs.commission_bps + spec.costs.spread_bps) * cost_multiplier / 10_000.0

        class _TargetStrategy(BtStrategy):  # type: ignore[misc]
            def init(self) -> None:  # pragma: no cover - executed by vendor engine
                self.target = self.data.Target

            def next(self) -> None:  # pragma: no cover - executed by vendor engine
                desired = float(self.target[-1])
                price = float(self.data.Close[-1])
                equity = float(self.equity)
                if desired == 0:
                    if self.position:
                        self.position.close()
                    return
                units = int(abs(desired) * equity / max(price, 1e-9))
                if units <= 0:
                    return
                if desired > 0 and self.position.size <= 0:
                    if self.position:
                        self.position.close()
                    self.buy(size=units)
                elif desired < 0 and self.position.size >= 0:
                    if self.position:
                        self.position.close()
                    self.sell(size=units)

        bt = Backtest(frame, _TargetStrategy, cash=1_000_000, commission=commission,
                      exclusive_orders=False, finalize_trades=True)
        stats = bt.run()
        equity_values = stats["_equity_curve"]["Equity"]
        equity = equity_values / float(equity_values.iloc[0])
        net = equity.pct_change().fillna(0.0)
        costs = native._costs(positions, spec, merged, cost_multiplier)
        bench_ret = (
            benchmark.reindex(close.index).pct_change(fill_method=None).fillna(0.0)
            if benchmark is not None
            else None
        )
        trades = extract_trades(positions, close)
        metrics = compute_metrics(net, equity, positions=positions, trades=trades, benchmark=bench_ret)
        metrics["engine_note"] = "fills simulated by backtesting.py (single asset)"
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
            trades=trades,
            metrics=metrics,
        )


__all__ = ["BacktestingPyAdapter"]
