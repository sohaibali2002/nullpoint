from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from engine import MemoryTrialCounter, make_spec
from engine.strategy import RiskRules
from engine.validation import (
    GATES,
    block_bootstrap_returns,
    combinatorial_purged_cv,
    cost_sensitivity,
    cscv_pbo,
    deflated_sharpe_ratio,
    expected_max_sharpe,
    future_canary,
    lookahead_scan,
    parameter_plateau,
    probabilistic_sharpe_ratio,
    purged_kfold,
    random_entry_null,
    regime_analysis,
    sealed_holdout_split,
    signal_shuffle_null,
    trade_bootstrap,
    validate_strategy,
)
from tests.conftest import make_market_data, make_prices, make_trending_prices

MOMENTUM = """
def signal(features, params, close):
    lookback = int(params.get("lookback", 20))
    top_n = int(params.get("top_n", 2))
    score = features[f"momentum_{lookback}"] / features["volatility"].clip(lower=1e-4)
    ranks = score.rank(axis=1, ascending=False, method="first")
    longs = (ranks <= top_n).astype(float)
    shorts = (ranks > ranks.shape[1] - top_n).astype(float)
    raw = longs - shorts
    return raw.div(raw.abs().sum(axis=1).replace(0, 1), axis=0)
"""


def _spec(signal_code: str = MOMENTUM):
    return make_spec(
        "validation subject",
        "synthetic momentum",
        ["AAA", "BBB", "CCC", "DDD"],
        signal_code=signal_code,
        features={
            "momentum_10": {"type": "momentum", "window": 10},
            "momentum_20": {"type": "momentum", "window": 20},
            "momentum_40": {"type": "momentum", "window": 40},
            "volatility": {"type": "volatility", "window": 20},
        },
        params={"lookback": 20, "top_n": 2},
        risk=RiskRules(max_position_weight=1.0, max_gross_exposure=1.0),
        benchmark="AAA",
    )


def test_purged_kfold_has_no_overlap_and_embargo():
    splits = purged_kfold(500, n_splits=5, embargo=10)
    assert len(splits) == 5
    covered = np.concatenate([s.test for s in splits])
    assert len(covered) == 500
    assert len(set(covered)) == 500
    for split in splits:
        assert not set(split.train) & set(split.test)
        before = split.train[split.train < split.test[0]]
        after = split.train[split.train > split.test[-1]]
        if len(before):
            assert split.test[0] - before.max() > 10
        if len(after):
            assert after.min() - split.test[-1] > 10


def test_purged_kfold_rejects_tiny_samples():
    with pytest.raises(ValueError):
        purged_kfold(20, n_splits=5)


def test_cpcv_path_count_and_disjointness():
    splits = combinatorial_purged_cv(400, n_groups=4, k_test=2, embargo=3)
    assert len(splits) == math.comb(4, 2)
    for split in splits:
        assert len(set(split.test)) == len(split.test)
        assert not set(split.train) & set(split.test)
    with pytest.raises(ValueError):
        combinatorial_purged_cv(400, n_groups=4, k_test=4)


def test_sealed_holdout_split():
    split = sealed_holdout_split(1000, 0.2)
    assert split["visible"] == [0, 800]
    assert split["sealed"] == [800, 1000]


def test_pbo_is_high_for_pure_noise_and_low_for_a_real_edge():
    rng = np.random.default_rng(0)
    noise = pd.DataFrame(rng.normal(0.0, 0.01, size=(600, 10)))
    noisy = cscv_pbo(noise)
    assert noisy.pbo > 0.30
    assert noisy.n_combinations > 10
    assert noisy.n_strategies == 10

    edged = noise.copy()
    edged[0] = rng.normal(0.0025, 0.0015, size=600)
    strong = cscv_pbo(edged)
    assert strong.pbo < 0.2
    assert strong.pbo < noisy.pbo


def test_pbo_requires_multiple_strategies():
    with pytest.raises(ValueError):
        cscv_pbo(pd.DataFrame({"only": np.zeros(100)}))


def test_probabilistic_and_deflated_sharpe():
    assert probabilistic_sharpe_ratio(0.0, 500) == pytest.approx(0.5, abs=0.01)
    assert probabilistic_sharpe_ratio(3.0, 500) > 0.99
    assert probabilistic_sharpe_ratio(0.2, 100, skew=-2, kurtosis=10) < probabilistic_sharpe_ratio(0.2, 100)
    assert deflated_sharpe_ratio(0.0, 100, 500) < 0.5
    assert deflated_sharpe_ratio(0.3, 1, 100) > deflated_sharpe_ratio(0.3, 10, 100)
    assert expected_max_sharpe(1, 500) == 0.0
    assert expected_max_sharpe(100, 500) > expected_max_sharpe(10, 500) > 0


def test_block_bootstrap_separates_signal_from_noise():
    rng = np.random.default_rng(3)
    good = block_bootstrap_returns(pd.Series(rng.normal(0.0015, 0.005, 500)), n_sims=200, seed=1)
    assert good.prob_le_zero < 0.05
    noise = block_bootstrap_returns(pd.Series(rng.normal(0.0, 0.01, 500)), n_sims=200, seed=1)
    assert noise.prob_le_zero > 0.2
    assert noise.p05 < noise.p50 < noise.p95


def test_trade_bootstrap_requires_enough_trades():
    result = trade_bootstrap([0.01, -0.01, 0.02], n_sims=50)
    assert result.n_sims == 0
    assert result.prob_le_zero == 1.0


def test_random_entry_null_detects_an_impossible_sharpe():
    rng = np.random.default_rng(4)
    asset = pd.Series(rng.normal(0.0002, 0.01, 600))
    impossible = random_entry_null(asset, observed_sharpe=20.0, n_sims=100, seed=2)
    assert impossible.p_value == 0.0
    ordinary = random_entry_null(asset, observed_sharpe=0.0, n_sims=100, seed=2)
    assert 0.2 <= ordinary.p_value <= 1.0


def test_signal_shuffle_null_penalises_perfect_foresight():
    rng = np.random.default_rng(5)
    index = pd.bdate_range("2019-01-01", periods=500)
    asset_returns = pd.DataFrame({"AAA": rng.normal(0.0, 0.01, 500)}, index=index)
    oracle = np.sign(asset_returns["AAA"])
    positions = pd.DataFrame({"AAA": oracle}, index=index)
    pnl = oracle * asset_returns["AAA"]
    observed = float(pnl.mean() / pnl.std(ddof=1) * math.sqrt(252))
    assert observed > 10  # instantaneous foresight is nearly perfect
    result = signal_shuffle_null(positions, asset_returns, observed, n_sims=200, seed=3)
    assert result.p_value < 0.02
    assert result.p95 < observed / 2


def test_parameter_plateau_rewards_broad_regions():
    broad = [
        {"params": {"a": a, "b": b}, "score": 1.0}
        for a in (1, 2, 3)
        for b in (1, 2, 3)
    ]
    plateau = parameter_plateau(broad)
    assert plateau["plateau_share"] == pytest.approx(1.0)
    spike = [
        {"params": {"a": a, "b": b}, "score": 2.0 if (a, b) == (2, 2) else 0.0}
        for a in (1, 2, 3)
        for b in (1, 2, 3)
    ]
    spiky = parameter_plateau(spike)
    assert spiky["plateau_share"] < 0.2
    assert spiky["heatmap"]["best_cell"] == [1, 1]


def test_cost_sensitivity_flags_fragile_strategies():
    fragile = cost_sensitivity(lambda mult, delay: 1.2 if mult == 1.0 and delay == 1 else 0.05)
    assert fragile["survives_3x_delay"] is False
    robust = cost_sensitivity(lambda mult, delay: 1.0 / mult)
    assert robust["table"][-1]["cost_multiplier"] == 3.0


def test_lookahead_scan_finds_common_leaks():
    assert lookahead_scan("def signal(features, params):\n    return features['x'].shift(-1)\n")["passed"] is False
    assert lookahead_scan("def signal(features, params):\n    return features['x'].bfill()\n")["passed"] is False
    assert lookahead_scan("x.rolling(20, center=True).mean()")["passed"] is False
    clean = "def signal(features, params):\n    return features['x'].shift(1).fillna(0)\n"
    assert lookahead_scan(clean)["passed"] is True


def test_future_canary_catches_a_leak_and_accepts_a_safe_signal():
    close = make_prices(n=300, symbols=("AAA",), seed=8)

    def safe(frame: pd.DataFrame) -> pd.DataFrame:
        score = frame.pct_change(10).fillna(0.0)
        return (score > 0).astype(float)

    def leaky(frame: pd.DataFrame) -> pd.DataFrame:
        score = frame.shift(-10) / frame - 1.0
        return (score > 0).astype(float)

    assert future_canary(safe, close)["passed"] is True
    leak = future_canary(leaky, close)
    assert leak["passed"] is False
    assert leak["max_abs_diff"] > 0


def test_regime_analysis_buckets_returns():
    prices = make_prices(n=700, symbols=("AAA",), seed=6, drift=0.0004)
    returns = prices["AAA"].pct_change().dropna()
    result = regime_analysis(returns, prices["AAA"])
    assert result["available"] is True
    labels = {bucket["regime"] for bucket in result["buckets"]}
    assert {"low_vol", "high_vol", "uptrend", "downtrend"}.issubset(labels)


def test_validate_strategy_structure_and_noise_rejection():
    data = make_market_data(n=520, symbols=("AAA", "BBB", "CCC", "DDD"), seed=12, drift=0.0)
    spec = _spec()
    counter = MemoryTrialCounter()
    report = validate_strategy(
        spec,
        data,
        grid={"lookback": [10, 20], "top_n": [1, 2]},
        trial_counter=counter,
        folds=3,
        cpcv_groups=4,
        mc_sims=80,
    )
    payload = report.to_dict()
    assert {"grid", "wfo_rolling", "wfo_anchored", "pbo", "deflated_sharpe", "monte_carlo"} <= set(payload["sections"])
    assert len(report.gates) >= 8
    assert counter.value > 0
    assert report.n_trials_used == counter.value
    assert payload["disclaimer"].startswith("Research only")
    # pure noise must not be promoted
    assert report.passed is False
    assert report.recommendation == "reject"


def test_validate_strategy_rejects_lookahead_signal():
    leaky = """
def signal(features, params, close):
    future = close.shift(-1) / close - 1.0
    return (future > 0).astype(float)
"""
    data = make_market_data(n=420, symbols=("AAA", "BBB", "CCC", "DDD"), seed=13)
    report = validate_strategy(_spec(leaky), data, grid={"lookback": [10], "top_n": [1]}, folds=3, cpcv_groups=4, mc_sims=40)
    assert report.gate("lookahead_scan") is not None
    assert report.gate("lookahead_scan").passed is False
    assert report.passed is False


def test_gate_thresholds_are_explicit():
    assert GATES["dsr_min"] == 0.90
    assert GATES["pbo_max"] == 0.35
    assert GATES["min_years"] >= 1.0


def test_validation_is_deterministic():
    data = make_market_data(n=400, symbols=("AAA", "BBB", "CCC", "DDD"), seed=21)
    spec = _spec()
    first = validate_strategy(spec, data, grid={"lookback": [10], "top_n": [1]}, folds=3, cpcv_groups=4, mc_sims=40)
    second = validate_strategy(spec, data, grid={"lookback": [10], "top_n": [1]}, folds=3, cpcv_groups=4, mc_sims=40)
    assert first.to_dict()["gates"] == second.to_dict()["gates"]


def test_trending_data_can_beat_noise_but_still_faces_gates():
    close = make_trending_prices(n=700)
    from engine import MarketData

    data = MarketData(close=close, benchmark=None)
    report = validate_strategy(
        _spec(),
        data,
        grid={"lookback": [10, 20], "top_n": [1, 2]},
        folds=3,
        cpcv_groups=4,
        mc_sims=60,
    )
    assert report.score > 0.0
    assert report.recommendation in {"reject", "validated", "candidate"}
