from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from engine.metrics import (
    Trade,
    alpha_beta,
    cagr,
    calmar_ratio,
    compute_metrics,
    drawdown_duration,
    drawdown_series,
    factor_attribution,
    max_drawdown,
    profit_factor,
    sharpe_ratio,
    sortino_ratio,
    turnover,
    var_cvar,
    win_rate,
)


def test_sharpe_matches_closed_form():
    returns = pd.Series([0.01, -0.005] * 100)
    expected = returns.mean() / returns.std(ddof=1) * math.sqrt(252)
    assert sharpe_ratio(returns) == pytest.approx(expected)


def test_degenerate_series_return_zero():
    assert sharpe_ratio(pd.Series([])) == 0.0
    assert sharpe_ratio(pd.Series([0.001])) == 0.0
    assert sharpe_ratio(pd.Series([0.002] * 50)) == 0.0
    assert sortino_ratio(pd.Series([0.002] * 50)) == 0.0
    assert max_drawdown(pd.Series(dtype=float)) == 0.0


def test_max_drawdown_known_answer():
    equity = pd.Series([1.0, 1.1, 0.99, 1.2, 0.9])
    assert max_drawdown(equity) == pytest.approx(0.9 / 1.2 - 1.0)
    dd = drawdown_series(equity)
    assert dd.iloc[2] == pytest.approx(0.99 / 1.1 - 1.0)
    assert drawdown_duration(equity) == 1
    path = pd.Series([1.0, 1.1, 1.05, 1.02, 1.2])
    assert drawdown_duration(path) == 2


def test_cagr_known_answer():
    equity = pd.Series(np.cumprod([1.0] + [1.001] * 251))
    assert cagr(equity) == pytest.approx(equity.iloc[-1] - 1.0, rel=1e-9)
    assert cagr(pd.Series([1.0, 0.5])) == pytest.approx(0.5 ** (252 / 2) - 1)


def test_calmar_is_cagr_over_drawdown():
    equity = pd.Series([1.0, 1.2, 0.9, 1.3])
    expected = cagr(equity) / abs(max_drawdown(equity))
    assert calmar_ratio(equity) == pytest.approx(expected)


def test_trade_statistics():
    pnls = [0.1, -0.05, 0.2, -0.1]
    assert profit_factor(pnls) == pytest.approx(2.0)
    assert win_rate(pnls) == pytest.approx(0.5)
    metrics = compute_metrics(
        pd.Series([0.01, -0.01] * 50),
        pd.Series(np.cumprod([1.0] + [0.01, -0.01] * 50)),
        trades=[Trade("AAA", 1, "2020-01-01", "2020-02-01", 100, 110, p, 20) for p in pnls],
    )
    assert metrics["n_trades"] == 4
    assert metrics["profit_factor"] == pytest.approx(2.0)
    assert metrics["expectancy"] == pytest.approx(0.0375)


def test_alpha_beta_recovers_known_exposure():
    rng = np.random.default_rng(1)
    bench = pd.Series(rng.normal(0.0004, 0.01, 400))
    strategy = 2.0 * bench + 0.0002
    alpha, beta = alpha_beta(strategy, bench)
    assert beta == pytest.approx(2.0, rel=1e-6)
    assert alpha == pytest.approx(0.0002 * 252, rel=1e-6)


def test_factor_attribution_recovers_linear_model():
    rng = np.random.default_rng(2)
    f1 = pd.Series(rng.normal(0.0003, 0.008, 500))
    f2 = pd.Series(rng.normal(0.0001, 0.006, 500))
    strategy = 0.0001 + 0.8 * f1 - 0.4 * f2
    result = factor_attribution(strategy, {"mkt": f1, "value": f2})
    assert result["betas"]["mkt"] == pytest.approx(0.8, abs=1e-6)
    assert result["betas"]["value"] == pytest.approx(-0.4, abs=1e-6)
    assert result["r2"] == pytest.approx(1.0, abs=1e-6)


def test_tail_and_var_metrics():
    rng = np.random.default_rng(3)
    returns = pd.Series(rng.normal(0.0, 0.01, 2000))
    var, cvar = var_cvar(returns)
    assert var < 0
    assert cvar <= var
    metrics = compute_metrics(returns, (1 + returns).cumprod())
    assert metrics["tail_ratio"] > 0
    assert metrics["skew"] == pytest.approx(float(returns.skew()), rel=1e-6)


def test_turnover_uses_position_changes():
    positions = pd.DataFrame({"A": [0.0, 1.0, 1.0, 0.0], "B": [0.0, 0.0, 0.0, 0.0]})
    traded = positions.diff().abs().fillna(positions.abs()).sum(axis=1)
    assert turnover(positions) == pytest.approx(traded.mean() * 252)


def test_compute_metrics_has_required_keys_without_nans():
    rng = np.random.default_rng(4)
    returns = pd.Series(rng.normal(0.0005, 0.01, 300))
    metrics = compute_metrics(returns, (1 + returns).cumprod(), benchmark=returns * 0.5)
    required = {
        "cagr", "volatility", "sharpe", "sortino", "calmar", "max_drawdown",
        "max_drawdown_duration", "profit_factor", "win_rate", "expectancy",
        "turnover_annual", "exposure", "tail_ratio", "skew", "kurtosis",
        "var_95", "cvar_95", "alpha_annual", "beta",
    }
    assert required.issubset(metrics)
    for key in required:
        assert np.isfinite(metrics[key])
