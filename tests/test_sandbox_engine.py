from __future__ import annotations

import numpy as np
import pytest

from engine import MemoryTrialCounter, make_spec
from engine.native import NativeAdapter
from engine.sandbox_adapter import SandboxEngineAdapter
from sandbox.runner import SandboxError
from tests.conftest import make_market_data

SIGNAL = """
def signal(features, params, close):
    lookback = int(params.get("lookback", 20))
    mom = features[f"momentum_{lookback}"]
    vol = features["volatility"].clip(lower=1e-4)
    score = mom / vol
    ranks = score.rank(axis=1, ascending=False, method="first")
    longs = (ranks <= int(params.get("top_n", 2))).astype(float)
    raw = longs.sub(longs.mean(axis=1), axis=0)
    return raw.div(raw.abs().sum(axis=1).replace(0, 1), axis=0)
"""


def _spec():
    return make_spec(
        "sandbox parity",
        "The same signal must produce the same result inside the sandbox.",
        ["SPY", "QQQ", "XLK"],
        signal_code=SIGNAL,
        features={
            "momentum_20": {"type": "momentum", "window": 20},
            "volatility": {"type": "volatility", "window": 20},
        },
        params={"lookback": 20, "top_n": 2, "long_short": True},
    )


def test_sandbox_adapter_matches_native_engine(monkeypatch) -> None:
    monkeypatch.setenv("SANDBOX_BACKEND", "local")
    data = make_market_data(n=320, symbols=("SPY", "QQQ", "XLK"), seed=19)
    spec = _spec()
    expected = NativeAdapter().run_backtest(spec, data)
    counter = MemoryTrialCounter()
    actual = SandboxEngineAdapter().run_backtest(spec, data, trial_counter=counter)

    assert counter.value == 1
    assert actual.engine == "sandbox-native"
    assert actual.metrics["sharpe"] == pytest.approx(expected.metrics["sharpe"], rel=1e-9)
    assert actual.metrics["cagr"] == pytest.approx(expected.metrics["cagr"], rel=1e-9)
    assert len(actual.returns) == len(expected.returns)
    assert np.max(np.abs(actual.returns.to_numpy() - expected.returns.to_numpy())) < 1e-12
    assert int(actual.positions.to_numpy().sum()) == int(expected.positions.to_numpy().sum())


def test_sandbox_adapter_fails_closed_in_production_without_docker(monkeypatch) -> None:
    monkeypatch.setenv("SANDBOX_BACKEND", "local")
    with pytest.raises(SandboxError, match="production"):
        SandboxEngineAdapter(production=True).run_backtest(_spec(), make_market_data(n=120))


def test_sandbox_adapter_fails_closed_when_signal_crashes(monkeypatch) -> None:
    monkeypatch.setenv("SANDBOX_BACKEND", "local")
    spec = _spec().model_copy(
        update={"signal_code": "def signal(features, params, close):\n    raise RuntimeError('boom')\n"}
    )
    with pytest.raises(SandboxError, match="boom"):
        SandboxEngineAdapter().run_backtest(spec, make_market_data(n=120), trial_counter=None)
