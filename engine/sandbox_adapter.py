"""Native backtests executed inside the sandbox.

The normal ``NativeAdapter`` compiles agent-written signal code in the worker process.  This adapter
keeps the same engine semantics but runs each backtest in a separate process (and, in production, a
networkless container).  Only the scanned signal source is untrusted; the harness that reconstructs
``MarketData`` and calls ``NativeAdapter`` is platform code and is never scanned as user input.

The trade-off is explicit: container startup is slower than an in-process call.  That is acceptable
for agent-authored strategies; the SPY research harness can still select the native/vectorbt adapter
for bulk offline work.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

from engine.base import (
    BacktestResult,
    EngineAdapter,
    MarketData,
    TrialCounter,
)
from engine.metrics import Trade
from engine.strategy import StrategySpec
from sandbox.runner import SandboxError, SandboxLimits, run_in_sandbox

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# The harness is trusted platform code.  It is appended after the scanned signal source and uses the
# ``signal`` function defined there.  It deliberately does not implement its own portfolio
# accounting: it monkey-patches the native compiler and calls the real NativeAdapter, so a sandboxed
# result cannot silently diverge from the reference engine.
HARNESS = r'''
import json, os, sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, os.environ.get("NP_PROJECT_ROOT") or "/srv")
import numpy as np
import pandas as pd
import engine.native as native
from engine.base import MarketData
from engine.strategy import StrategySpec

DATA = Path(os.environ["NP_DATA_DIR"])
close = pd.read_csv(DATA / "close.csv", index_col=0, parse_dates=True)
volume = None
volume_path = DATA / "volume.csv"
if volume_path.exists():
    volume = pd.read_csv(volume_path, index_col=0, parse_dates=True)
benchmark_path = DATA / "benchmark.csv"
benchmark = None
if benchmark_path.exists():
    benchmark = pd.read_csv(benchmark_path, index_col=0, parse_dates=True).iloc[:, 0]

payload = PAYLOAD
spec_payload = dict(payload["spec"])
spec_payload["signal_code"] = "def signal(features, params, close):\n    return None\n"
spec = StrategySpec(**spec_payload)
if benchmark is None and spec.benchmark in close.columns:
    benchmark = close[spec.benchmark]
data = MarketData(close=close, volume=volume, benchmark=benchmark, provider="sandbox")

# The user source has already defined ``signal`` in this module.  Route the native engine's compiler
# through that function instead of compiling the placeholder again.
native._compile_cached = lambda code: signal
result = native.NativeAdapter().run_backtest(
    spec,
    data,
    params=payload.get("params") or {},
    trial_counter=None,
    cost_multiplier=float(payload.get("cost_multiplier", 1.0)),
    delay_bars=int(payload.get("delay_bars", 1)),
)

def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value

emit({
    "spec_id": result.spec_id,
    "engine": result.engine,
    "params": clean(result.params),
    "seed": int(result.seed),
    "dates": [str(d.date()) for d in result.returns.index],
    "returns": [float(v) for v in result.returns.fillna(0.0)],
    "equity": [float(v) for v in result.equity.fillna(1.0)],
    "gross_returns": [float(v) for v in result.gross_returns.fillna(0.0)],
    "costs": [float(v) for v in result.costs.fillna(0.0)],
    "positions": {str(c): [float(v) for v in result.positions[c].fillna(0.0)] for c in result.positions.columns},
    "benchmark_returns": [float(v) for v in result.benchmark_returns.fillna(0.0)],
    "metrics": clean(result.metrics),
    "trades": [clean(asdict(t)) for t in result.trades],
})
'''


class SandboxEngineAdapter(EngineAdapter):
    """Run the native engine in a sandboxed process/container."""

    name = "sandbox"

    def __init__(self, *, production: bool | None = None) -> None:
        self.production = production
        self.last_execution: dict[str, Any] | None = None

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
        spec_payload = spec.model_dump(mode="json")
        spec_payload.pop("signal_code", None)
        payload = {
            "spec": spec_payload,
            "params": merged,
            "cost_multiplier": float(cost_multiplier),
            "delay_bars": int(delay_bars),
        }
        with tempfile.TemporaryDirectory(prefix="np-engine-") as tmp:
            data_dir = Path(tmp)
            data.close.to_csv(data_dir / "close.csv")
            if data.volume is not None:
                data.volume.to_csv(data_dir / "volume.csv")
            if data.benchmark is not None:
                data.benchmark.rename("benchmark").to_frame().to_csv(data_dir / "benchmark.csv")
            execution = run_in_sandbox(
                spec.signal_code,
                harness=HARNESS,
                payload=payload,
                data_dir=data_dir,
                project_dir=PROJECT_ROOT,
                backend="auto",
                production=self.production,
                # A multi-asset panel plus returns/positions is larger than a typical tool result.
                limits=SandboxLimits(max_output_bytes=10_000_000),
            )
        self.last_execution = execution.as_dict()
        if not execution.ok:
            detail = (execution.stderr or execution.stdout or "sandboxed backtest failed").strip()
            raise SandboxError(detail[-2000:])
        output = execution.output
        if not isinstance(output, dict) or "returns" not in output:
            raise SandboxError("sandboxed backtest returned no result payload")
        try:
            result = _result_from_payload(output)
            if trial_counter is not None:
                trial_counter.increment(1)
            return result
        except (KeyError, TypeError, ValueError) as exc:
            raise SandboxError(f"invalid sandboxed backtest result: {exc}") from exc


def _result_from_payload(payload: dict[str, Any]) -> BacktestResult:
    dates = pd.to_datetime(payload["dates"])
    returns = pd.Series(payload["returns"], index=dates, dtype=float)
    equity = pd.Series(payload["equity"], index=dates, dtype=float)
    positions = pd.DataFrame(payload["positions"], index=dates).astype(float)
    positions.columns = [str(c).upper() for c in positions.columns]
    trades = [Trade(**row) for row in payload.get("trades", [])]
    def series(key: str) -> pd.Series:
        values = payload.get(key) or []
        return pd.Series(values, index=dates if values else None, dtype=float)

    gross = series("gross_returns")
    costs = series("costs")
    benchmark = series("benchmark_returns")
    return BacktestResult(
        spec_id=str(payload["spec_id"]),
        engine="sandbox-native",
        params=dict(payload.get("params") or {}),
        returns=returns,
        equity=equity,
        positions=positions,
        benchmark_returns=benchmark,
        gross_returns=gross,
        costs=costs,
        trades=trades,
        metrics=dict(payload.get("metrics") or {}),
        seed=int(payload.get("seed", 7)),
    )


def adapter_payload(adapter: SandboxEngineAdapter) -> dict[str, Any]:
    return adapter.last_execution or {}


__all__ = ["HARNESS", "PROJECT_ROOT", "SandboxEngineAdapter", "adapter_payload"]
