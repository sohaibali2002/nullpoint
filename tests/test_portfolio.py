from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine.portfolio import (
    construct,
    hrp_weights,
    inverse_vol_weights,
    kelly_cap,
    mean_variance_weights,
    portfolio_metrics,
    risk_parity_weights,
    shrink_cov,
)


@pytest.fixture()
def returns() -> pd.DataFrame:
    rng = np.random.default_rng(5)
    index = pd.bdate_range("2020-01-01", periods=500)
    return pd.DataFrame(
        {
            "A": rng.normal(0.0006, 0.008, 500),
            "B": rng.normal(0.0002, 0.014, 500),
            "C": rng.normal(0.0004, 0.020, 500),
            "D": rng.normal(0.0001, 0.006, 500),
        },
        index=index,
    )


@pytest.mark.parametrize("method", ["inverse_vol", "risk_parity", "hrp", "mean_variance"])
def test_weights_are_valid(returns: pd.DataFrame, method: str) -> None:
    weights = construct(returns, method=method, max_weight=0.5)
    assert set(weights) == set(returns.columns)
    assert sum(weights.values()) == pytest.approx(1.0, abs=1e-9)
    assert all(0.0 <= w <= 0.5 + 1e-9 for w in weights.values())


def test_hrp_prefers_low_volatility_and_diversifies(returns: pd.DataFrame) -> None:
    weights = hrp_weights(returns, max_weight=0.9)
    assert weights["D"] > weights["C"]  # D is the least volatile
    assert max(weights.values()) < 0.6


def test_inverse_vol_is_exactly_inverse(returns: pd.DataFrame) -> None:
    weights = inverse_vol_weights(returns, max_weight=1.0)
    vol = returns.std()
    ratio = weights["A"] / weights["B"]
    assert ratio == pytest.approx(float(vol["B"] / vol["A"]), rel=0.05)


def test_shrinkage_moves_covariance_toward_diagonal(returns: pd.DataFrame) -> None:
    shrunk = shrink_cov(returns, shrink=0.9)
    off_diagonal = shrunk.to_numpy()[~np.eye(4, dtype=bool)]
    assert np.abs(off_diagonal).max() < np.abs(returns.cov().to_numpy()[~np.eye(4, dtype=bool)]).max()


def test_kelly_cap_limits_gross(returns: pd.DataFrame) -> None:
    weights = inverse_vol_weights(returns, max_weight=0.5)
    capped = kelly_cap(weights, mean_return=0.15, variance=0.04, cap=0.25)
    assert sum(capped.values()) <= 0.25 + 1e-9


def test_portfolio_metrics(returns: pd.DataFrame) -> None:
    weights = risk_parity_weights(returns)
    metrics = portfolio_metrics(weights, returns)
    assert metrics["n_assets"] == 4
    assert 0.0 <= metrics["diversification"] < 1.0
    assert np.isfinite(metrics["sharpe"])


def test_single_asset_and_empty_edge_cases() -> None:
    single = pd.DataFrame({"A": [0.01, -0.01, 0.02] * 40})
    assert hrp_weights(single) == {"A": 1.0}
    assert construct(pd.DataFrame(), method="hrp") == {}
    with pytest.raises(ValueError):
        construct(single, method="black_litterman")
