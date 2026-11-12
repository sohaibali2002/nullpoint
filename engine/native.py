"""Native reference engine.

Semantics (shared by every adapter where the vendor engine allows it):

* Signals produced from data up to and including bar *t* are executed at bar *t+1*.
* Costs: per-unit-turnover commission + slippage + spread, plus annual borrow on short notional.
* Stops are evaluated on closing prices only, once per bar. No intraday fills are simulated.
* Portfolio volatility targeting uses a trailing covariance window (60 bars by default).
* Trade PnL for profit-factor/win-rate purposes is gross of costs; net performance is reported
  separately in the equity curve.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, cast

import numpy as np
import pandas as pd

from engine.base import (
    BacktestResult,
    EngineAdapter,
    MarketData,
    TrialCounter,
)
from engine.features import build_features
from engine.metrics import Trade, compute_metrics
from engine.signal import SignalError, SignalFn, compile_signal, evaluate_signal
from engine.strategy import StrategySpec


@lru_cache(maxsize=128)
def _compile_cached(code: str) -> SignalFn:
    return compile_signal(code)


class NativeAdapter(EngineAdapter):
    name = "native"
    supports_multi_asset = True

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
        if delay_bars < 1:
            raise ValueError("next-bar execution requires delay_bars >= 1")
        merged = {**spec.params, **(params or {})}
        missing = [s for s in spec.universe if s not in data.close.columns]
        if missing:
            raise ValueError(f"universe symbols missing from data: {missing}")
        if cost_multiplier <= 0:
            raise ValueError("cost_multiplier must be > 0")

        close = data.close[spec.universe]
        benchmark = data.benchmark_series(spec.benchmark)
        features = build_features(close, spec.features, benchmark=benchmark)

        try:
            fn = _compile_cached(spec.signal_code)
            raw = evaluate_signal(fn, features, close, merged)
        except SignalError:
            raise

        positions = self._targets(raw, spec, close, delay_bars)
        positions = self._apply_stops(close, positions, spec)
        positions = self._rebalance(positions, spec)
        positions = self._size(close, positions, spec)

        asset_returns = close.pct_change(fill_method=None).fillna(0.0)
        gross = (positions * asset_returns).sum(axis=1)
        costs = self._costs(positions, spec, merged, cost_multiplier)
        net = (gross - costs).fillna(0.0)

        if spec.risk.max_drawdown_stop and len(net) > 5:
            net, positions = self._portfolio_drawdown_stop(net, positions, spec, cost_multiplier)

        equity = (1.0 + net).cumprod()
        trades = extract_trades(positions, close)
        bench_ret = (
            benchmark.reindex(close.index).pct_change(fill_method=None).fillna(0.0)
            if benchmark is not None
            else None
        )
        metrics = compute_metrics(
            net,
            equity,
            positions=positions,
            trades=trades,
            benchmark=bench_ret,
        )
        metrics["n_trials_at_run"] = trial_counter.value if trial_counter else 0
        metrics["cost_multiplier"] = cost_multiplier
        metrics["delay_bars"] = delay_bars
        metrics["avg_cost_bps"] = (
            float(costs.sum() / max(1e-9, positions.diff().abs().sum().sum()) * 1e4)
            if positions.diff().abs().sum().sum() > 0
            else 0.0
        )

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
            gross_returns=gross,
            costs=costs,
            trades=trades,
            metrics=metrics,
        )

    # ------------------------------------------------------------------ steps
    @staticmethod
    def _targets(
        raw: pd.DataFrame, spec: StrategySpec, close: pd.DataFrame, delay_bars: int
    ) -> pd.DataFrame:
        target = raw.shift(delay_bars).fillna(0.0)
        if not spec.risk.long_short:
            target = target.clip(lower=0.0)
        return target.clip(-1.0, 1.0)

    @staticmethod
    def _apply_stops(close: pd.DataFrame, positions: pd.DataFrame, spec: StrategySpec) -> pd.DataFrame:
        stop = spec.risk.stop_loss_pct
        take = spec.risk.take_profit_pct
        if stop is None and take is None:
            return positions
        out = positions.copy()
        for symbol in positions.columns:
            sym_pos = positions[symbol].to_numpy(dtype=float)
            prices = close[symbol].to_numpy(dtype=float)
            current = 0.0
            entry = np.nan
            col = cast(int, out.columns.get_loc(symbol))
            for i in range(len(out)):
                wanted = sym_pos[i]
                if current != 0.0 and np.isfinite(entry):
                    signed = (prices[i] / entry - 1.0) * np.sign(current)
                    if np.isfinite(signed) and (
                        (stop is not None and signed <= -stop) or (take is not None and signed >= take)
                    ):
                        out.iat[i, col] = 0.0
                        current = 0.0
                        entry = np.nan
                        continue
                if wanted == 0.0:
                    current = 0.0
                    entry = np.nan
                elif current == 0.0 or np.sign(wanted) != np.sign(current):
                    current = float(wanted)
                    entry = float(prices[i])
                out.iat[i, col] = current
        return out

    @staticmethod
    def _rebalance(positions: pd.DataFrame, spec: StrategySpec) -> pd.DataFrame:
        if spec.rebalance == "daily" or positions.empty:
            return positions
        freq = "W" if spec.rebalance == "weekly" else "M"
        periods = pd.DatetimeIndex(positions.index).to_period(freq)
        starts = np.ones(len(periods), dtype=bool)
        starts[1:] = periods[1:] != periods[:-1]
        mask = pd.DataFrame(
            np.repeat(starts[:, None], positions.shape[1], axis=1),
            index=positions.index,
            columns=positions.columns,
        )
        return positions.where(mask, np.nan).ffill().fillna(0.0)

    @staticmethod
    def _size(close: pd.DataFrame, positions: pd.DataFrame, spec: StrategySpec) -> pd.DataFrame:
        risk = spec.risk
        sized = positions.clip(-risk.max_position_weight, risk.max_position_weight)

        target_vol = risk.vol_target_annual
        if target_vol:
            rets = close.pct_change(fill_method=None).fillna(0.0)
            window = min(60, max(20, len(rets) // 4))
            cov = rets.rolling(window, min_periods=window // 2).cov()
            vols = []
            for dt in sized.index:
                try:
                    block = cov.loc[dt].reindex(index=sized.columns, columns=sized.columns).fillna(0.0)
                except KeyError:  # pragma: no cover - thin data
                    vols.append(np.nan)
                    continue
                w = sized.loc[dt].to_numpy(dtype=float)
                vols.append(float(np.sqrt(max(0.0, w @ block.to_numpy() @ w))))
            ex_ante = pd.Series(vols, index=sized.index)
            scale = (target_vol / ex_ante.replace(0, np.nan)).clip(upper=3.0).fillna(1.0)
            sized = sized.mul(scale, axis=0)

        gross = sized.abs().sum(axis=1)
        cap = risk.max_gross_exposure
        shrink = np.where(gross > cap, cap / gross.replace(0, np.nan), 1.0)
        sized = sized.mul(pd.Series(shrink, index=gross.index).fillna(1.0), axis=0)
        return sized.fillna(0.0)

    @staticmethod
    def _costs(
        positions: pd.DataFrame,
        spec: StrategySpec,
        params: dict[str, Any],
        multiplier: float,
    ) -> pd.Series:
        turnover = positions.diff().abs()
        turnover.iloc[0] = positions.iloc[0].abs()
        bps = spec.costs.per_turnover_bps * multiplier
        traded_cost = turnover.sum(axis=1) * bps / 10_000.0
        short_notional = positions.clip(upper=0.0).abs().sum(axis=1)
        borrow = short_notional * (spec.costs.borrow_bps_annual / 10_000.0) / 252.0
        # `slippage_bps` is part of per-turnover cost; optional extra spread widening via params.
        extra = float(params.get("extra_slippage_bps", 0.0)) * multiplier
        if extra:
            traded_cost = traded_cost + turnover.sum(axis=1) * extra / 10_000.0
        return (traded_cost + borrow).astype(float)

    @staticmethod
    def _portfolio_drawdown_stop(
        net: pd.Series, positions: pd.DataFrame, spec: StrategySpec, cost_multiplier: float
    ) -> tuple[pd.Series, pd.DataFrame]:
        assert spec.risk.max_drawdown_stop is not None
        equity = (1.0 + net).cumprod()
        breach = (equity / equity.cummax() - 1.0) <= -spec.risk.max_drawdown_stop
        if not breach.any():
            return net, positions
        first_idx = int(np.argmax(breach.to_numpy()))
        net = net.copy()
        positions = positions.copy()
        exit_cost = positions.iloc[first_idx].abs().sum() * spec.costs.per_turnover_bps * cost_multiplier / 10_000.0
        net.iloc[first_idx] -= exit_cost
        net.iloc[first_idx + 1 :] = 0.0
        positions.iloc[first_idx + 1 :] = 0.0
        return net, positions


def extract_trades(positions: pd.DataFrame, close: pd.DataFrame) -> list[Trade]:
    """Round-trip trade ledger built from position segments (gross PnL, close-to-close)."""
    trades: list[Trade] = []
    if positions.empty:
        return trades
    rets = close.pct_change(fill_method=None).fillna(0.0)
    for symbol in positions.columns:
        pos = np.asarray(positions[symbol].to_numpy(), dtype=float)
        price_series = np.asarray(close[symbol].to_numpy(), dtype=float).reshape(-1)
        dates = pd.DatetimeIndex(positions.index)
        current = 0.0
        entry_i: int | None = None
        for i, wanted in enumerate(pos):
            if current == 0.0 and wanted != 0.0:
                current = float(wanted)
                entry_i = i
            elif current != 0.0 and (wanted == 0.0 or np.sign(wanted) != np.sign(current)):
                assert entry_i is not None
                segment = rets[symbol].iloc[entry_i + 1 : i + 1]
                growth = float(cast(float, (1.0 + current * segment).prod())) if len(segment) else 1.0
                trades.append(
                    Trade(
                        symbol=symbol,
                        direction=int(np.sign(current)),
                        entry_date=str(dates[entry_i].date()),
                        exit_date=str(dates[i].date()),
                        entry_price=float(price_series[entry_i]),
                        exit_price=float(price_series[i]),
                        pnl=growth - 1.0,
                        bars=i - entry_i,
                    )
                )
                current = float(wanted)
                entry_i = i if wanted != 0.0 else None
        if current != 0.0 and entry_i is not None:
            segment = rets[symbol].iloc[entry_i + 1 :]
            growth = float(cast(float, (1.0 + current * segment).prod())) if len(segment) else 1.0
            trades.append(
                Trade(
                    symbol=symbol,
                    direction=int(np.sign(current)),
                    entry_date=str(dates[entry_i].date()),
                    exit_date=str(dates[-1].date()),
                    entry_price=float(price_series[entry_i]),
                    exit_price=float(price_series[-1]),
                    pnl=growth - 1.0,
                    bars=len(pos) - 1 - entry_i,
                )
            )
    return trades


__all__ = ["NativeAdapter", "extract_trades"]
