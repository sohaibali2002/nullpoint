from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from engine import MarketData, MemoryTrialCounter, make_spec
from engine.base import get_adapter
from engine.metrics import sharpe_ratio
from engine.strategy import Costs, RiskRules
from engine.tribes import ParameterSpace, TribesOptimizer
from engine.validation import (
    cpcv_paths,
    effective_trials,
    probabilistic_sharpe_ratio,
    same_bar_dependence_canary,
    signal_shuffle_null,
    validate_strategy,
)
from tests.conftest import make_prices

LONG = "def signal(features, params, close):\n    return close * 0 + 1.0\n"
MOMENTUM = """
def signal(features, params, close):
    lookback = int(params.get("lookback", 10))
    top_n = int(params.get("top_n", 1))
    score = features[f"momentum_{lookback}"] / features["volatility"].clip(lower=1e-4)
    ranks = score.rank(axis=1, ascending=False, method="first")
    raw = (ranks <= top_n).astype(float)
    return raw.div(raw.abs().sum(axis=1).replace(0, 1), axis=0)
"""


def test_tribes_finds_a_quadratic_optimum() -> None:
    space = ParameterSpace.from_grid({"x": [0.0, 0.25, 0.5, 1.0], "y": [-1.0, -0.5, 0.0]})
    optimizer = TribesOptimizer(space, max_evaluations=50, seed=11)

    def objective(params: dict[str, float]) -> float:
        return -((params["x"] - 0.3) ** 2 + (params["y"] + 0.4) ** 2)

    result = optimizer.minimize(objective)
    assert result.best_params["x"] == pytest.approx(0.25)
    assert result.best_params["y"] == pytest.approx(-0.5)
    assert result.evaluations <= 50
    assert result.best_score > -0.07
    assert result.history


def test_tribes_is_deterministic() -> None:
    space = ParameterSpace.from_grid({"x": [0, 1, 2, 3, 4]})
    first = TribesOptimizer(space, max_evaluations=20, seed=4).minimize(lambda p: -(p["x"] - 3) ** 2)
    second = TribesOptimizer(space, max_evaluations=20, seed=4).minimize(lambda p: -(p["x"] - 3) ** 2)
    assert first.best_params == second.best_params
    assert first.history == second.history


def test_run_tribes_backtests_and_records_metadata() -> None:
    data = MarketData(close=make_prices(n=220, seed=5))
    spec = make_spec(
        "tribes subject",
        "synthetic momentum",
        ["SPY", "QQQ", "XLK"],
        signal_code=MOMENTUM,
        features={
            "momentum_5": {"type": "momentum", "window": 5},
            "momentum_10": {"type": "momentum", "window": 10},
            "volatility": {"type": "volatility", "window": 20},
        },
        risk=RiskRules(max_position_weight=0.5, max_gross_exposure=1.0),
    )
    counter = MemoryTrialCounter()
    result = get_adapter("native").run_tribes(
        spec,
        {"lookback": [5, 10], "top_n": [1, 2]},
        data,
        trial_counter=counter,
        max_trials=12,
        seed=2,
        min_trades=1,
    )
    assert result.metadata["optimizer"] == "tribes"
    assert result.best is not None
    assert counter.value > 0
    assert result.best_params["lookback"] in {5, 10}


def test_walk_forward_can_use_tribes_with_warmup() -> None:
    data = MarketData(close=make_prices(n=480, seed=8))
    spec = make_spec(
        "tribes wfo",
        "synthetic momentum",
        ["SPY", "QQQ", "XLK"],
        signal_code=MOMENTUM,
        features={
            "momentum_5": {"type": "momentum", "window": 5},
            "momentum_10": {"type": "momentum", "window": 10},
            "volatility": {"type": "volatility", "window": 20},
        },
        risk=RiskRules(max_position_weight=0.5, max_gross_exposure=1.0),
    )
    report = get_adapter("native").run_walk_forward(
        spec,
        {"lookback": [5, 10], "top_n": [1, 2]},
        data,
        folds=2,
        optimizer="tribes",
        max_trials=8,
        warmup_bars=80,
        min_trades=1,
        seed=3,
    )
    assert report.n_folds == 2
    assert all(fold.get("warmup_bars", 0) > 0 for fold in report.folds)
    assert len(report.oos_returns) > 0


def test_vectorbt_and_native_agree_on_a_constant_weight() -> None:
    from engine.base import EngineUnavailable

    try:
        vectorbt = get_adapter("vectorbt")
    except EngineUnavailable:
        pytest.skip("vectorbt extra is not installed")
    close = make_prices(n=260, symbols=("AAA",), seed=3)
    spec = make_spec(
        "parity",
        "constant long",
        ["AAA"],
        signal_code=LONG,
        features={},
        risk=RiskRules(max_position_weight=1.0, max_gross_exposure=1.0, long_short=False),
        costs=Costs(commission_bps=1.0, slippage_bps=2.0, spread_bps=0.5),
        benchmark="AAA",
    )
    native = get_adapter("native").run_backtest(spec, MarketData(close=close))
    vbt = vectorbt.run_backtest(spec, MarketData(close=close))
    assert vbt.metrics["total_return"] == pytest.approx(native.metrics["total_return"], abs=1e-4)
    assert native.returns.corr(vbt.returns) > 0.9999


def test_stop_erases_no_loss_and_exits_on_the_stop_bar() -> None:
    index = pd.bdate_range("2020-01-01", periods=40)
    close = pd.DataFrame({"SPY": 100.0 * np.cumprod(np.repeat(0.98, 40))}, index=index)
    spec = make_spec(
        "stop",
        "stop accounting",
        ["SPY"],
        signal_code=LONG,
        features={},
        risk=RiskRules(stop_loss_pct=0.05, max_position_weight=1.0, max_gross_exposure=1.0, long_short=False),
        costs=Costs(commission_bps=0.0, slippage_bps=0.0, spread_bps=0.0),
    )
    result = get_adapter("native").run_backtest(spec, MarketData(close=close))
    pos = result.positions["SPY"].to_numpy()
    entered = np.where(pos != 0.0)[0]
    assert len(entered) > 0
    stopped = np.where(pos == 0.0)[0]
    stopped = stopped[stopped > entered[0]]
    assert len(stopped) > 0
    exit_bar = int(stopped[0])
    assert exit_bar > 2
    # The bar that triggered the stop is still held from the prior close, so its -2% return
    # must be realized; only the following bar is flat.
    assert result.returns.iloc[exit_bar] == pytest.approx(-0.02, abs=1e-9)


def test_psr_uses_per_period_sharpe_reference_value() -> None:
    # Bailey & Lopez de Prado: SR=0.10 per period, n=100, normal returns.  The denominator
    # includes the (kurtosis - 1) / 4 * SR^2 correction (kurtosis=3 here).
    sigma = math.sqrt((1.0 + 0.5 * 0.10**2) / 99.0)
    expected = 0.5 * (1.0 + math.erf(0.10 / sigma / math.sqrt(2.0)))
    assert probabilistic_sharpe_ratio(0.10, 100) == pytest.approx(expected, abs=1e-9)
    annualised = 0.10 * math.sqrt(252)
    assert probabilistic_sharpe_ratio(annualised, 100) > 0.999  # the old, wrong units


def test_effective_trials_clusters_correlated_candidates() -> None:
    rng = np.random.default_rng(6)
    base = rng.normal(0.0004, 0.01, 500)
    columns = {}
    for cluster in range(4):
        series = base + rng.normal(0, 0.0015, 500) if cluster == 0 else rng.normal(0.0004, 0.01, 500)
        columns[f"c{cluster}_a"] = series
        columns[f"c{cluster}_b"] = series + rng.normal(0, 0.0005, 500)
    result = effective_trials(pd.DataFrame(columns), correlation_threshold=0.9)
    assert result["n_raw"] == 8
    assert 4 <= result["n_effective"] < 8


def test_cpcv_selects_params_on_train_only_and_never_stitches() -> None:
    rng = np.random.default_rng(7)
    n = 240
    index = pd.bdate_range("2015-01-01", periods=n)
    matrix = pd.DataFrame(
        {
            "bad": rng.normal(-0.0002, 0.01, n),
            "good": rng.normal(0.0015, 0.005, n),
        },
        index=index,
    )
    from engine.validation import combinatorial_purged_cv

    splits = combinatorial_purged_cv(n, n_groups=4, k_test=2, embargo=2)
    result = cpcv_paths(matrix, splits, {"bad": {"lookback": 10}, "good": {"lookback": 20}})
    assert result["n_paths"] > 0
    assert result["median_oos_sharpe"] > 1.0
    assert all(record["params"] == {"lookback": 20} for record in result["records"] if "params" in record)


def test_signal_shuffle_keeps_costs_and_penalises_foresight() -> None:
    rng = np.random.default_rng(8)
    index = pd.bdate_range("2018-01-01", periods=500)
    returns = pd.DataFrame({"AAA": rng.normal(0.0, 0.01, 500)}, index=index)
    # Positions at t that know the *next* return: with the engine's one-bar execution this
    # becomes a perfect same-bar oracle, which the placebo must reject.
    oracle = np.sign(returns["AAA"]).shift(-1).fillna(0.0)
    positions = pd.DataFrame({"AAA": oracle}, index=index)
    pnl = (positions.shift(1) * returns).sum(axis=1)
    observed = sharpe_ratio(pnl)
    null = signal_shuffle_null(positions, returns, observed, n_sims=120, seed=2)
    assert null.p_value < 0.02
    assert null.extra["null_type"] == "block_shuffled_returns_fixed_positions"


def test_same_bar_canary_passes_with_honest_fills() -> None:
    spec = make_spec("canary", "canary", ["SPY"], signal_code=LONG, features={})
    result = same_bar_dependence_canary(spec, seed=1)
    assert result["passed"] is True
    assert abs(result["observed_sharpe"]) < result["threshold"]


def test_production_refuses_default_secret_and_local_sandbox() -> None:
    from api.config import Settings

    with pytest.raises(ValueError, match="default JWT secret"):
        Settings(app_env="production", sandbox_backend="docker")
    with pytest.raises(ValueError, match="local sandbox"):
        Settings(app_env="production", secret_key="x" * 40, sandbox_backend="local")
    with pytest.raises(ValueError, match="create schema"):
        Settings(app_env="production", secret_key="x" * 40, sandbox_backend="docker")
    with pytest.raises(ValueError, match="ENGINE_SANDBOX"):
        Settings(
            app_env="production",
            secret_key="x" * 40,
            sandbox_backend="docker",
            auto_create_schema=False,
        )
    Settings(
        app_env="production",
        secret_key="x" * 40,
        sandbox_backend="docker",
        auto_create_schema=False,
        engine_sandbox=True,
    )


def test_validate_strategy_exposes_effective_trials_and_null_gate() -> None:
    data = MarketData(close=make_prices(n=320, seed=13))
    spec = make_spec(
        "validation structure",
        "synthetic",
        ["SPY", "QQQ"],
        signal_code=LONG,
        features={},
        risk=RiskRules(max_position_weight=0.5, max_gross_exposure=1.0),
    )
    report = validate_strategy(
        spec,
        data,
        grid={"lookback": [5, 10]},
        folds=2,
        cpcv_groups=4,
        mc_sims=30,
        min_trades=1,
    )
    assert report.gate("signal_shuffle_null") is not None
    assert report.gate("same_bar_canary") is not None
    assert report.gate("regime_robustness") is not None
    assert "n_trials_effective" in report.sections["deflated_sharpe"]
