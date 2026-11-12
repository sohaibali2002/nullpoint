"""Portfolio construction: risk parity, HRP, shrunk mean-variance and Kelly capping.

All methods return long-only weights that sum to 1 (research portfolios; shorting a basket of
validated strategies is out of scope for v0). Covariance estimation always uses daily returns and
a shrinkage/intensity parameter so the optimizer cannot invert pure noise.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import linkage, to_tree
from scipy.spatial.distance import squareform

TRADING_DAYS = 252
METHODS = ("inverse_vol", "risk_parity", "hrp", "mean_variance")


def _clean(returns: pd.DataFrame) -> pd.DataFrame:
    frame = returns.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    usable = [c for c in frame.columns if frame[c].std(ddof=1) > 0]
    return frame[usable]


def _normalise(weights: np.ndarray, max_weight: float) -> dict[str, float]:
    w = np.clip(weights, 0.0, None)
    if w.sum() <= 1e-12:
        w = np.ones_like(w)
    w = w / w.sum()
    # Iterative cap redistribution keeps the sum at 1 without a solver.
    for _ in range(20):
        over = w > max_weight
        if not over.any():
            break
        excess = (w[over] - max_weight).sum()
        w[over] = max_weight
        under = ~over
        if not under.any():
            break
        w[under] += excess * w[under] / w[under].sum()
    return {k: float(v) for k, v in zip(range(len(w)), w)}


def shrink_cov(returns: pd.DataFrame, shrink: float = 0.25) -> pd.DataFrame:
    cov = returns.cov()
    diag = pd.DataFrame(np.diag(np.diag(cov)), index=cov.index, columns=cov.columns)
    return (1.0 - shrink) * cov + shrink * diag


def inverse_vol_weights(returns: pd.DataFrame, max_weight: float = 0.4) -> dict[str, float]:
    frame = _clean(returns)
    if frame.empty:
        return {}
    vol = frame.std(ddof=1)
    raw = (1.0 / vol.replace(0, np.nan)).fillna(0.0).to_numpy()
    named = _normalise(raw, max_weight)
    return {column: named[i] for i, column in enumerate(frame.columns)}


def risk_parity_weights(
    returns: pd.DataFrame, max_weight: float = 0.4, iterations: int = 300
) -> dict[str, float]:
    """Equal risk contribution via a simple fixed-point algorithm on the covariance."""
    frame = _clean(returns)
    if frame.empty:
        return {}
    cov = shrink_cov(frame).to_numpy()
    n = cov.shape[0]
    w = np.ones(n) / n
    for _ in range(iterations):
        marginal = cov @ w
        budget = 1.0 / n
        w_new = w * (budget / np.maximum(marginal * w, 1e-12)) ** 0.5
        w_new = np.clip(w_new, 1e-8, None)
        w = w_new / w_new.sum()
    named = _normalise(w, max_weight)
    return {column: named[i] for i, column in enumerate(frame.columns)}


def _quasi_diagonal(cov: pd.DataFrame) -> list[int]:
    corr = cov.corr().fillna(0.0)
    dist = np.sqrt(np.clip(0.5 * (1.0 - corr.to_numpy()), 0.0, None))
    condensed = squareform(dist, checks=False)
    link = linkage(condensed, method="single")
    root = to_tree(link, rd=False)
    order: list[int] = []

    def _walk(node: Any) -> None:
        if node.is_leaf():
            order.append(int(node.id))
        else:
            _walk(node.get_left())
            _walk(node.get_right())

    _walk(root)
    return order


def hrp_weights(returns: pd.DataFrame, max_weight: float = 0.4) -> dict[str, float]:
    """Hierarchical Risk Parity (Lopez de Prado): cluster, quasi-diagonalise, bisect."""
    frame = _clean(returns)
    if frame.shape[1] < 2:
        return {c: 1.0 for c in frame.columns}
    cov = shrink_cov(frame)
    order = _quasi_diagonal(cov)
    ordered = cov.iloc[order, order]
    weights = pd.Series(1.0, index=ordered.index)
    clusters: list[list[int]] = [list(range(len(order)))]
    while clusters:
        next_clusters: list[list[int]] = []
        for cluster in clusters:
            if len(cluster) <= 1:
                continue
            split = len(cluster) // 2
            left, right = cluster[:split], cluster[split:]
            var_left = _cluster_variance(ordered, left)
            var_right = _cluster_variance(ordered, right)
            alpha = 1.0 - var_left / (var_left + var_right) if (var_left + var_right) > 0 else 0.5
            weights.iloc[left] *= alpha
            weights.iloc[right] *= 1.0 - alpha
            next_clusters.extend([left, right])
        clusters = next_clusters
    named = _normalise(weights.to_numpy(), max_weight)
    return {column: named[i] for i, column in enumerate(ordered.index)}


def _cluster_variance(cov: pd.DataFrame, indices: list[int]) -> float:
    sub = cov.iloc[indices, indices].to_numpy()
    inv_diag = 1.0 / np.maximum(np.diag(sub), 1e-12)
    w = inv_diag / inv_diag.sum()
    return float(w @ sub @ w)


def mean_variance_weights(
    returns: pd.DataFrame,
    max_weight: float = 0.4,
    risk_aversion: float = 2.0,
    shrink: float = 0.3,
) -> dict[str, float]:
    """Long-only mean-variance with a shrinkage covariance and a 5% return floor per asset."""
    frame = _clean(returns)
    if frame.empty:
        return {}
    mu = frame.mean().to_numpy() * TRADING_DAYS
    cov = shrink_cov(frame, shrink).to_numpy() * TRADING_DAYS
    try:
        raw = np.linalg.solve(cov + np.eye(len(mu)) * 1e-6, mu) / max(risk_aversion, 1e-6)
    except np.linalg.LinAlgError:  # pragma: no cover - defensive
        raw = np.ones(len(mu))
    named = _normalise(raw, max_weight)
    return {column: named[i] for i, column in enumerate(frame.columns)}


def kelly_cap(weights: dict[str, float], mean_return: float, variance: float, cap: float = 0.25) -> dict[str, float]:
    """Scale total exposure by fractional Kelly, never above ``cap`` gross."""
    if variance <= 0:
        return weights
    kelly = mean_return / variance
    scale = float(min(max(kelly, 0.0) / 2.0, cap))
    return {key: value * scale for key, value in weights.items()}


def construct(
    returns: pd.DataFrame, method: str = "hrp", max_weight: float = 0.4
) -> dict[str, float]:
    if method == "inverse_vol":
        return inverse_vol_weights(returns, max_weight)
    if method == "risk_parity":
        return risk_parity_weights(returns, max_weight)
    if method == "hrp":
        return hrp_weights(returns, max_weight)
    if method == "mean_variance":
        return mean_variance_weights(returns, max_weight)
    raise ValueError(f"unknown portfolio method {method!r}; choose from {METHODS}")


def portfolio_returns(weights: dict[str, float], returns: pd.DataFrame) -> pd.Series:
    if not weights:
        return pd.Series(dtype=float)
    columns = [c for c in weights if c in returns.columns]
    w = pd.Series({c: weights[c] for c in columns})
    return (returns[columns] * w).sum(axis=1)


def portfolio_metrics(weights: dict[str, float], returns: pd.DataFrame) -> dict[str, Any]:
    from engine.metrics import compute_metrics

    series = portfolio_returns(weights, returns)
    if series.empty:
        return {"sharpe": 0.0, "n_assets": 0, "weights": weights}
    equity = (1.0 + series.fillna(0.0)).cumprod()
    metrics = compute_metrics(series, equity)
    diversification = 1.0 - float(sum(v**2 for v in weights.values()))
    metrics.update(
        {
            "n_assets": len(weights),
            "weights": weights,
            "diversification": diversification,
            "portfolio_var_95": metrics["var_95"],
        }
    )
    return metrics
