"""The noise gate.

The platform's claim is that random signals do not get promoted. This test manufactures pure-noise
markets, runs the *real* research pipeline (grid → walk-forward → CPCV → PBO → DSR → Monte Carlo →
stress) and asserts that nothing is promoted. It runs in CI and is deliberately repeated over
several seeds so a single lucky RNG path cannot hide a broken gate.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine import MarketData, MemoryTrialCounter, make_spec
from engine.strategy import RiskRules
from engine.validation import validate_strategy
from tests.conftest import make_prices

RANDOM_SIGNAL = """
def signal(features, params, close):
    z = features["zscore"]
    lookback = int(params.get("lookback", 20))
    mom = features[f"momentum_{lookback}"]
    threshold = float(params.get("threshold", 0.0))
    raw = np.sign(z + threshold) * np.sign(mom)
    return pd.DataFrame(raw, index=close.index, columns=close.columns) * 0.5
"""


def _noise_spec() -> object:
    return make_spec(
        "noise probe",
        "A signal with no economic content, used to verify the validator.",
        ["AAA", "BBB", "CCC", "DDD"],
        signal_code=RANDOM_SIGNAL,
        features={
            "zscore": {"type": "zscore", "window": 20},
            "momentum_10": {"type": "momentum", "window": 10},
            "momentum_20": {"type": "momentum", "window": 20},
            "momentum_40": {"type": "momentum", "window": 40},
            "volatility": {"type": "volatility", "window": 20},
        },
        params={"lookback": 20},
        risk=RiskRules(max_position_weight=1.0, max_gross_exposure=1.0),
        benchmark="AAA",
    )


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_pure_noise_is_never_promoted(seed: int) -> None:
    close = make_prices(n=520, symbols=("AAA", "BBB", "CCC", "DDD"), seed=seed, drift=0.0, vol=0.011)
    data = MarketData(close=close, benchmark=close["AAA"], provider="synthetic-noise")
    counter = MemoryTrialCounter()
    report = validate_strategy(
        _noise_spec(),  # type: ignore[arg-type]
        data,
        grid={"lookback": [10, 20, 40], "threshold": [-0.2, 0.0, 0.2]},
        trial_counter=counter,
        folds=3,
        cpcv_groups=4,
        mc_sims=60,
    )
    assert report.passed is False, f"noise passed on seed {seed}: {[g.as_dict() for g in report.gates]}"
    assert report.recommendation == "reject"
    assert report.score < 0.80
    failed = [g.name for g in report.gates if not g.passed]
    assert failed, "every gate passed on noise"
    assert report.n_trials_used > 5
    # Multiple-testing penalty must reflect the whole search, not one grid.
    assert report.sections["deflated_sharpe"]["n_trials"] == counter.value


def test_validator_rejects_a_random_walk_with_an_impossible_sharpe_report() -> None:
    """A fabricated high in-sample Sharpe must not survive the trial-count penalty."""
    rng = np.random.default_rng(99)
    close = pd.DataFrame(
        100 * np.cumprod(1 + rng.normal(0, 0.01, size=(400, 1)), axis=0),
        index=pd.bdate_range("2019-01-01", periods=400),
        columns=["AAA"],
    )
    data = MarketData(close=close)
    spec = make_spec(
        "impossible",
        "Constant long on a random walk",
        ["AAA"],
        signal_code="def signal(features, params, close):\n    return close * 0 + 1.0\n",
        features={},
        risk=RiskRules(max_position_weight=1.0, max_gross_exposure=1.0),
    )
    report = validate_strategy(spec, data, grid={"dummy": [1, 2, 3]}, folds=3, cpcv_groups=4, mc_sims=40)
    assert report.passed is False
