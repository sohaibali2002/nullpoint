"""Validation & anti-overfitting.

This module is the product. A backtest is an in-sample fit; promotion requires surviving:

1. walk-forward optimisation (rolling + anchored) with purging/embargo,
2. combinatorial purged cross-validation (CPCV) path distribution,
3. probability of backtest overfitting (CSCV/PBO),
4. deflated Sharpe ratio using the *global* trial count,
5. Monte Carlo: block bootstrap, trade bootstrap, random-entry and signal-shuffle nulls,
6. parameter-plateau stability, cost sensitivity at 2x/3x, execution delay +1 bar,
7. static lookahead scan plus a future-data canary,
8. regime dependence and a minimum track record.

Thresholds are explicit constants so they can be audited and changed deliberately.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import re
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from engine.base import EngineAdapter, MarketData, TrialCounter, get_adapter
from engine.metrics import TRADING_DAYS, sharpe_ratio
from engine.strategy import StrategySpec

EULER_GAMMA = 0.5772156649015329
MAX_CSCV_COMBINATIONS = 500

GATES = {
    "oos_sharpe_min": 0.50,
    "wfo_efficiency_min": 0.40,
    "cpcv_median_sharpe_min": 0.30,
    "pbo_max": 0.35,
    "dsr_min": 0.90,
    "mc_prob_le_zero_max": 0.10,
    "random_entry_p_max": 0.10,
    "cost_3x_sharpe_min": 0.30,
    "delay_sharpe_min": 0.30,
    "plateau_share_min": 0.40,
    "min_trades": 20,
    "min_years": 1.0,
}


# --------------------------------------------------------------------- splits
@dataclass(frozen=True)
class Split:
    fold: int
    train: np.ndarray
    test: np.ndarray


def purged_kfold(n_samples: int, n_splits: int = 5, embargo: int = 5) -> list[Split]:
    """Contiguous test folds; training indices within ``embargo`` of test are removed."""
    if n_splits < 2:
        raise ValueError("n_splits must be >= 2")
    if n_samples < n_splits * 10:
        raise ValueError("need at least 10 samples per fold")
    if embargo < 0:
        raise ValueError("embargo must be >= 0")
    fold_size = n_samples // n_splits
    splits: list[Split] = []
    for i in range(n_splits):
        test_start = i * fold_size
        test_end = n_samples if i == n_splits - 1 else (i + 1) * fold_size
        mask = np.ones(n_samples, dtype=bool)
        mask[test_start:test_end] = False
        mask[max(0, test_start - embargo) : test_start] = False
        mask[test_end : min(n_samples, test_end + embargo)] = False
        splits.append(
            Split(fold=i, train=np.where(mask)[0], test=np.arange(test_start, test_end))
        )
    return splits


def combinatorial_purged_cv(
    n_samples: int, n_groups: int = 6, k_test: int = 2, embargo: int = 2
) -> list[Split]:
    """CPCV: every combination of ``k_test`` contiguous groups is out-of-sample once.

    Number of paths/splits is C(n_groups, k_test), which is why CPCV gives a distribution of
    out-of-sample outcomes instead of a single lucky split.
    """
    if not 1 <= k_test < n_groups:
        raise ValueError("require 1 <= k_test < n_groups")
    if n_samples < n_groups * 10:
        raise ValueError("need at least 10 samples per group")
    groups = np.array_split(np.arange(n_samples), n_groups)
    splits: list[Split] = []
    for fold, combo in enumerate(itertools.combinations(range(n_groups), k_test)):
        test_idx = np.concatenate([groups[g] for g in combo])
        mask = np.ones(n_samples, dtype=bool)
        for g in combo:
            block = groups[g]
            mask[block] = False
            mask[max(0, int(block[0]) - embargo) : int(block[0])] = False
            mask[int(block[-1]) + 1 : min(n_samples, int(block[-1]) + 1 + embargo)] = False
        splits.append(Split(fold=fold, train=np.where(mask)[0], test=test_idx))
    return splits


def sealed_holdout_split(n_samples: int, holdout_fraction: float = 0.2) -> dict[str, Any]:
    """Describe the sealed tail. Visible data ends before it; agents never see the tail."""
    if not 0.05 <= holdout_fraction <= 0.5:
        raise ValueError("holdout_fraction must be in [0.05, 0.5]")
    cut = int(n_samples * (1.0 - holdout_fraction))
    return {
        "visible": [0, cut],
        "sealed": [cut, n_samples],
        "holdout_fraction": holdout_fraction,
        "policy": "sealed until a human explicitly requests final evaluation",
    }


# -------------------------------------------------------------- overfitting
def probabilistic_sharpe_ratio(
    sr: float, n_obs: int, skew: float = 0.0, kurtosis: float = 3.0, benchmark_sr: float = 0.0
) -> float:
    """P(true Sharpe > benchmark) given non-normal returns (Bailey & Lopez de Prado)."""
    if n_obs < 3 or not np.isfinite(sr):
        return 0.0
    denom = 1.0 - skew * sr + (kurtosis - 1.0) / 4.0 * sr**2
    if denom <= 0:
        return 0.0
    sigma = math.sqrt(denom / (n_obs - 1))
    if sigma <= 0:
        return 0.0
    return float(stats.norm.cdf((sr - benchmark_sr) / sigma))


def expected_max_sharpe(
    n_trials: int, n_obs: int, sr_variance: float | None = None
) -> float:
    """Expected maximum Sharpe under the null after ``n_trials`` independent attempts."""
    n_trials = max(1, int(n_trials))
    if n_trials == 1:
        return 0.0
    if n_obs < 3:
        return 0.0
    variance = sr_variance if sr_variance is not None else 1.0 / n_obs
    variance = max(variance, 1e-12)
    z1 = stats.norm.ppf(1.0 - 1.0 / n_trials)
    z2 = stats.norm.ppf(1.0 - 1.0 / (n_trials * math.e))
    return float(math.sqrt(variance) * ((1.0 - EULER_GAMMA) * z1 + EULER_GAMMA * z2))


def deflated_sharpe_ratio(
    sr: float,
    n_trials: int,
    n_obs: int,
    skew: float = 0.0,
    kurtosis: float = 3.0,
    sr_variance: float | None = None,
) -> float:
    """DSR: P(SR exceeds the expected max of ``n_trials`` null draws)."""
    sr0 = expected_max_sharpe(n_trials, n_obs, sr_variance)
    return probabilistic_sharpe_ratio(sr, n_obs, skew, kurtosis, benchmark_sr=sr0)


@dataclass
class PBOResult:
    pbo: float
    n_combinations: int
    n_strategies: int
    n_blocks: int
    logits: list[float] = field(default_factory=list)
    mean_logit: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "pbo": self.pbo,
            "n_combinations": self.n_combinations,
            "n_strategies": self.n_strategies,
            "n_blocks": self.n_blocks,
            "mean_logit": self.mean_logit,
            "logits_head": self.logits[:20],
            "verdict": "overfit" if self.pbo > GATES["pbo_max"] else "acceptable",
        }


def cscv_pbo(returns_matrix: pd.DataFrame, n_blocks: int = 8) -> PBOResult:
    """Combinatorially Symmetric Cross-Validation probability of backtest overfitting.

    Splits the return history into ``n_blocks`` contiguous blocks, uses every half as in-sample,
    picks the IS-best strategy, and measures its OOS rank. PBO is the share of splits where the
    IS winner lands in the bottom half out-of-sample.
    """
    matrix = returns_matrix.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    if matrix.shape[1] < 2:
        raise ValueError("PBO needs at least 2 strategies/trials")
    if matrix.shape[0] < max(50, n_blocks * 8):
        raise ValueError("PBO needs more observations")
    if n_blocks % 2 != 0:
        n_blocks -= 1
    n_blocks = max(4, min(n_blocks, 10))
    blocks = np.array_split(np.arange(len(matrix)), n_blocks)
    combos = list(itertools.combinations(range(n_blocks), n_blocks // 2))
    if len(combos) > MAX_CSCV_COMBINATIONS:
        rng = np.random.default_rng(7)
        picks = rng.choice(len(combos), size=MAX_CSCV_COMBINATIONS, replace=False)
        combos = [combos[i] for i in sorted(picks)]

    logits: list[float] = []
    n_strats = matrix.shape[1]
    for combo in combos:
        is_idx = np.concatenate([blocks[b] for b in combo])
        oos_idx = np.concatenate([blocks[b] for b in range(n_blocks) if b not in combo])
        is_returns = matrix.iloc[is_idx]
        is_sharpe = is_returns.apply(sharpe_ratio)
        if is_sharpe.isna().all():
            continue
        best = is_sharpe.idxmax()
        oos_sharpe = matrix.iloc[oos_idx].apply(sharpe_ratio)
        rank = float(oos_sharpe.rank(method="average").get(best, (n_strats + 1) / 2.0))
        omega = min(max(rank / (n_strats + 1.0), 1e-6), 1 - 1e-6)
        logits.append(math.log(omega / (1.0 - omega)))

    if not logits:
        raise ValueError("PBO could not evaluate any split")
    pbo = float(np.mean(np.array(logits) <= 0.0))
    return PBOResult(
        pbo=pbo,
        n_combinations=len(logits),
        n_strategies=n_strats,
        n_blocks=n_blocks,
        logits=[round(v, 6) for v in logits],
        mean_logit=float(np.mean(logits)),
    )


# ------------------------------------------------------------------- Monte Carlo
@dataclass
class MonteCarloResult:
    method: str
    observed: float
    n_sims: int
    mean: float
    p05: float
    p50: float
    p95: float
    prob_le_zero: float
    p_value: float
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _quantiles(values: np.ndarray) -> tuple[float, float, float, float]:
    return (
        float(np.mean(values)),
        float(np.percentile(values, 5)),
        float(np.percentile(values, 50)),
        float(np.percentile(values, 95)),
    )


def block_bootstrap_returns(
    returns: pd.Series,
    *,
    n_sims: int = 1000,
    block: int = 20,
    seed: int = 7,
) -> MonteCarloResult:
    """Circular block bootstrap of daily returns preserves short-range autocorrelation."""
    series = pd.Series(returns, dtype=float).replace([np.inf, -np.inf], np.nan).dropna().to_numpy()
    n = len(series)
    if n < max(30, block * 2):
        return MonteCarloResult("block_bootstrap", 0.0, 0, 0, 0, 0, 0, 1.0, 1.0)
    observed = sharpe_ratio(pd.Series(series))
    rng = np.random.default_rng(seed)
    sims = np.empty(n_sims)
    n_blocks = max(2, n // block)
    starts = rng.integers(0, n, size=(n_sims, n_blocks))
    offsets = np.arange(block)
    for i in range(n_sims):
        idx = (starts[i][:, None] + offsets[None, :]).ravel() % n
        sample = series[idx][:n]
        sims[i] = _fast_sharpe(sample)
    mean, p05, p50, p95 = _quantiles(sims)
    return MonteCarloResult(
        method="block_bootstrap",
        observed=observed,
        n_sims=n_sims,
        mean=mean,
        p05=p05,
        p50=p50,
        p95=p95,
        prob_le_zero=float(np.mean(sims <= 0.0)),
        p_value=float(np.mean(sims >= observed)),
        extra={"block": block, "seed": seed},
    )


def trade_bootstrap(
    trade_pnls: Sequence[float], *, n_sims: int = 1000, seed: int = 7
) -> MonteCarloResult:
    pnls = np.asarray([p for p in trade_pnls if np.isfinite(p)], dtype=float)
    if len(pnls) < 10:
        return MonteCarloResult("trade_bootstrap", 0.0, 0, 0, 0, 0, 0, 1.0, 1.0, {"n_trades": len(pnls)})
    observed = float(np.mean(pnls))
    rng = np.random.default_rng(seed)
    sims = rng.choice(pnls, size=(n_sims, len(pnls)), replace=True).mean(axis=1)
    mean, p05, p50, p95 = _quantiles(sims)
    return MonteCarloResult(
        method="trade_bootstrap",
        observed=observed,
        n_sims=n_sims,
        mean=mean,
        p05=p05,
        p50=p50,
        p95=p95,
        prob_le_zero=float(np.mean(sims <= 0.0)),
        p_value=float(np.mean(sims >= observed)),
        extra={"n_trades": len(pnls), "seed": seed},
    )


def _fast_sharpe(values: np.ndarray) -> float:
    if len(values) < 2:
        return 0.0
    std = float(np.std(values, ddof=1))
    if std <= 1e-12:
        return 0.0
    return float(np.mean(values) / std * math.sqrt(TRADING_DAYS))


def random_entry_null(
    asset_returns: pd.Series,
    observed_sharpe: float,
    *,
    n_sims: int = 500,
    holding_period: int = 10,
    seed: int = 7,
) -> MonteCarloResult:
    """Random long/short entries with the same holding period: is the signal better than luck?"""
    rets = pd.Series(asset_returns, dtype=float).fillna(0.0).to_numpy()
    n = len(rets)
    if n < 100:
        return MonteCarloResult("random_entry", observed_sharpe, 0, 0, 0, 0, 0, 1.0, 1.0)
    rng = np.random.default_rng(seed)
    sims = np.empty(n_sims)
    horizon = max(2, min(holding_period, n // 10))
    for i in range(n_sims):
        positions = np.zeros(n)
        t = 0
        while t < n:
            length = int(rng.integers(max(2, horizon // 2), horizon * 2 + 1))
            positions[t : t + length] = float(rng.choice([-1.0, 1.0]))
            t += length
        sims[i] = _fast_sharpe(positions * rets)
    mean, p05, p50, p95 = _quantiles(sims)
    return MonteCarloResult(
        method="random_entry",
        observed=observed_sharpe,
        n_sims=n_sims,
        mean=mean,
        p05=p05,
        p50=p50,
        p95=p95,
        prob_le_zero=float(np.mean(sims <= 0.0)),
        p_value=float(np.mean(sims >= observed_sharpe)),
        extra={"holding_period": horizon, "seed": seed},
    )


def signal_shuffle_null(
    positions: pd.DataFrame,
    asset_returns: pd.DataFrame,
    observed_sharpe: float,
    *,
    n_sims: int = 300,
    block: int = 20,
    seed: int = 7,
) -> MonteCarloResult:
    """Block-shuffle the position series: keeps exposure/turnover roughly, destroys timing."""
    if positions.empty or asset_returns.empty:
        return MonteCarloResult("signal_shuffle", observed_sharpe, 0, 0, 0, 0, 0, 1.0, 1.0)
    n = len(positions)
    block = max(2, min(block, n // 4))
    rng = np.random.default_rng(seed)
    sims = np.empty(n_sims)
    n_blocks = max(2, n // block)
    for i in range(n_sims):
        order = rng.permutation(n_blocks)
        shuffled_parts = []
        for b in order:
            start = b * block
            shuffled_parts.append(positions.iloc[start : start + block])
        shuffled = pd.concat(shuffled_parts).iloc[:n]
        shuffled.index = positions.index[: len(shuffled)]
        gross = (shuffled * asset_returns.reindex_like(shuffled)).sum(axis=1)
        sims[i] = _fast_sharpe(gross.fillna(0.0).to_numpy())
    mean, p05, p50, p95 = _quantiles(sims)
    return MonteCarloResult(
        method="signal_shuffle",
        observed=observed_sharpe,
        n_sims=n_sims,
        mean=mean,
        p05=p05,
        p50=p50,
        p95=p95,
        prob_le_zero=float(np.mean(sims <= 0.0)),
        p_value=float(np.mean(sims >= observed_sharpe)),
        extra={"block": block, "seed": seed},
    )


# -------------------------------------------------------------- stability etc.
def parameter_plateau(
    rows: Sequence[dict[str, Any]], tolerance: float = 0.30
) -> dict[str, Any]:
    """Detect a plateau rather than a lucky spike in a 2-D parameter grid."""
    if len(rows) < 4:
        return {"plateau_share": 0.0, "heatmap": None, "note": "grid too small"}
    param_keys = sorted(
        {k for row in rows for k, v in row.get("params", {}).items() if isinstance(v, (int, float))}
    )
    if len(param_keys) < 2:
        return {"plateau_share": 0.0, "heatmap": None, "note": "need two numeric parameters"}
    keys = param_keys[:2]
    best_row = max(rows, key=lambda r: float(r.get("score", -np.inf)))
    best_score = float(best_row.get("score", 0.0))
    if not np.isfinite(best_score):
        return {"plateau_share": 0.0, "heatmap": None, "note": "no finite objective"}
    threshold = best_score - tolerance * max(abs(best_score), 1e-6)
    xs = sorted({float(r["params"][keys[0]]) for r in rows if keys[0] in r.get("params", {})})
    ys = sorted({float(r["params"][keys[1]]) for r in rows if keys[1] in r.get("params", {})})
    lookup: dict[tuple[float, float], float] = {}
    for row in rows:
        params = row.get("params", {})
        if keys[0] in params and keys[1] in params:
            lookup[(float(params[keys[0]]), float(params[keys[1]]))] = float(row.get("score", 0.0))
    z = [[lookup.get((x, y), 0.0) for x in xs] for y in ys]
    share = float(np.mean([value >= threshold for value in lookup.values()])) if lookup else 0.0
    best_cell = None
    for yi, y in enumerate(ys):
        for xi, x in enumerate(xs):
            if (x, y) == (float(best_row["params"].get(keys[0])), float(best_row["params"].get(keys[1]))):
                best_cell = [xi, yi]
    return {
        "plateau_share": share,
        "threshold": threshold,
        "heatmap": {
            "x_param": keys[0],
            "y_param": keys[1],
            "x": xs,
            "y": ys,
            "z": z,
            "best_cell": best_cell,
            "objective": "score",
        },
    }


def cost_sensitivity(
    run: Callable[[float, int], float],
    *,
    multipliers: Sequence[float] = (1.0, 2.0, 3.0),
    delays: Sequence[int] = (1, 2),
) -> dict[str, Any]:
    """Re-run the same strategy with harsher costs and delayed execution."""
    table: list[dict[str, Any]] = []
    for mult in multipliers:
        for delay in delays:
            try:
                value = float(run(mult, delay))
            except Exception as exc:  # noqa: BLE001
                value = float("nan")
                table.append({"cost_multiplier": mult, "delay_bars": delay, "sharpe": None, "error": str(exc)})
                continue
            table.append({"cost_multiplier": mult, "delay_bars": delay, "sharpe": value})
    stressed = [row for row in table if row["cost_multiplier"] >= 3.0 and row["delay_bars"] == 1]
    base = [row for row in table if row["cost_multiplier"] == 1.0 and row["delay_bars"] == 1]
    return {
        "table": table,
        "baseline_sharpe": base[0]["sharpe"] if base else None,
        "stressed_sharpe": stressed[0]["sharpe"] if stressed else None,
        "survives_3x_delay": bool(stressed and (stressed[0]["sharpe"] or 0.0) >= GATES["cost_3x_sharpe_min"]),
    }


def lookahead_scan(code: str) -> dict[str, Any]:
    """Static scan for the usual point-in-time leaks."""
    findings: list[str] = []
    patterns = {
        r"\.shift\(\s*-\s*\d+": "negative shift (future data)",
        r"\.shift\(\s*periods\s*=\s*-": "negative shift (future data)",
        r"\.bfill\(": "backward fill (future data)",
        r"\.backfill\(": "backward fill (future data)",
        r"center\s*=\s*True": "centered rolling window (future data)",
        r"\bffill\(\s*limit\s*=\s*-": "invalid forward fill",
        r"future_": "explicit future reference",
        r"iloc\[\s*\[\s*-1\s*\]\s*\]": "future slice",
        r"rolling\([^)]*\)\.(quantile|rank)\([^)]*\)\.shift\(\s*-": "future quantile",
    }
    for pattern, message in patterns.items():
        if re.search(pattern, code):
            findings.append(message)
    return {
        "findings": sorted(set(findings)),
        "passed": not findings,
        "note": "static only; the future-data canary is the empirical check",
    }


def future_canary(
    build_positions: Callable[[pd.DataFrame], pd.DataFrame],
    close: pd.DataFrame,
    *,
    cut_fraction: float = 0.7,
    seed: int = 7,
) -> dict[str, Any]:
    """Corrupt the future and verify the past signal does not move.

    A strategy that peeks at future prices will change its historical positions when future prices
    change; a point-in-time strategy will not.
    """
    if len(close) < 60:
        return {"passed": False, "max_abs_diff": None, "note": "insufficient data"}
    cut = int(len(close) * cut_fraction)
    base = build_positions(close)
    rng = np.random.default_rng(seed)
    corrupted = close.copy()
    noise = rng.normal(0.0, 0.15, size=(len(corrupted) - cut, corrupted.shape[1]))
    factor = pd.DataFrame(
        1.0 + noise, index=corrupted.index[cut:], columns=corrupted.columns
    ).cumprod()
    corrupted.iloc[cut:] = corrupted.iloc[cut:] * factor
    perturbed = build_positions(corrupted)
    cols = [c for c in base.columns if c in perturbed.columns]
    past = slice(0, max(1, cut - 1))
    diff = (base[cols].iloc[past] - perturbed[cols].iloc[past]).abs().to_numpy()
    max_diff = float(np.nanmax(diff)) if diff.size else 0.0
    return {
        "passed": max_diff < 1e-9,
        "max_abs_diff": max_diff,
        "cut_index": cut,
        "note": "past positions must be unchanged when future prices are corrupted",
    }


def regime_analysis(
    returns: pd.Series,
    close: pd.Series,
    *,
    vol_window: int = 60,
    trend_window: int = 200,
    rates: pd.Series | None = None,
) -> dict[str, Any]:
    """Descriptive regime split. Labels are not used for selection, so lookahead is bounded."""
    rets = pd.Series(returns, dtype=float).dropna()
    price = pd.Series(close, dtype=float).reindex(rets.index).ffill()
    if len(rets) < vol_window * 3:
        return {"available": False, "note": "insufficient history for regime analysis"}

    realized_vol = price.pct_change(fill_method=None).rolling(vol_window).std()
    trailing = realized_vol.shift(1).expanding(min_periods=vol_window)
    q_low = trailing.quantile(1.0 / 3.0)
    q_high = trailing.quantile(2.0 / 3.0)
    vol_regime = pd.Series("mid_vol", index=rets.index)
    vol_regime[realized_vol > q_high] = "high_vol"
    vol_regime[realized_vol <= q_low] = "low_vol"

    trend = price / price.rolling(trend_window, min_periods=trend_window // 2).mean() - 1.0
    trend_regime = pd.Series(np.where(trend >= 0, "uptrend", "downtrend"), index=rets.index)

    def _bucket(mask: pd.Series, label: str) -> dict[str, Any]:
        subset = rets[mask.reindex(rets.index).fillna(False)]
        n = len(subset)
        return {
            "regime": label,
            "n_days": int(n),
            "share": float(n / len(rets)) if len(rets) else 0.0,
            "sharpe": sharpe_ratio(subset) if n >= 20 else None,
            "mean_daily": float(subset.mean()) if n else 0.0,
        }

    buckets = [
        _bucket(vol_regime == "low_vol", "low_vol"),
        _bucket(vol_regime == "mid_vol", "mid_vol"),
        _bucket(vol_regime == "high_vol", "high_vol"),
        _bucket(trend_regime == "uptrend", "uptrend"),
        _bucket(trend_regime == "downtrend", "downtrend"),
    ]
    if rates is not None and len(rates.dropna()) > 100:
        rate_change = pd.Series(rates, dtype=float).reindex(rets.index).ffill().diff(20)
        buckets.append(_bucket(rate_change > 0, "rates_rising"))
        buckets.append(_bucket(rate_change <= 0, "rates_falling"))
    sharpe_by_regime = [b["sharpe"] for b in buckets if b["sharpe"] is not None]
    dependence = (
        float(max(sharpe_by_regime) - min(sharpe_by_regime)) if len(sharpe_by_regime) >= 2 else 0.0
    )
    return {
        "available": True,
        "buckets": buckets,
        "dependence_spread": dependence,
        "note": "Descriptive only: regime labels must not feed signal construction.",
    }


# ------------------------------------------------------------------- verdict
@dataclass
class Gate:
    name: str
    passed: bool
    value: float | None
    threshold: str
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationReport:
    spec_name: str
    spec_hash: str
    data_hash: str
    gates: list[Gate]
    passed: bool
    score: float
    recommendation: str
    trial_index: int
    n_trials_used: int
    sections: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    def gate(self, name: str) -> Gate | None:
        return next((g for g in self.gates if g.name == name), None)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "spec_name": self.spec_name,
            "spec_hash": self.spec_hash,
            "data_hash": self.data_hash,
            "passed": self.passed,
            "score": self.score,
            "recommendation": self.recommendation,
            "trial_index": self.trial_index,
            "n_trials_used": self.n_trials_used,
            "gates": [g.as_dict() for g in self.gates],
            "sections": self.sections,
            "created_at": self.created_at,
            "disclaimer": "Research only. Backtests are hypotheses, not forecasts or advice.",
        }
        payload["report_hash"] = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode()
        ).hexdigest()
        return payload


def _gate(name: str, passed: bool, value: float | None, threshold: str, detail: str = "") -> Gate:
    return Gate(name=name, passed=bool(passed), value=value, threshold=threshold, detail=detail)


def validate_strategy(
    spec: StrategySpec,
    data: MarketData,
    *,
    adapter: EngineAdapter | None = None,
    grid: dict[str, Sequence[Any]] | None = None,
    trial_counter: TrialCounter | None = None,
    folds: int = 4,
    cpcv_groups: int = 4,
    cpcv_k: int = 2,
    embargo: int = 5,
    mc_sims: int = 300,
    seed: int = 7,
    min_trades: int | None = None,
) -> ValidationReport:
    """Run the full gauntlet and produce a pass/fail verdict with receipts."""
    adapter = adapter or get_adapter("native")
    grid = grid or {
        "lookback": [10, 20, 40],
        "top_n": [2, 3],
    }
    min_trades = GATES["min_trades"] if min_trades is None else min_trades
    sections: dict[str, Any] = {}
    gates: list[Gate] = []

    # 1. In-sample grid (also produces the PBO matrix).
    grid_result = adapter.run_grid(
        spec, grid, data, objective="sharpe", trial_counter=trial_counter, min_trades=3
    )
    best = grid_result.best
    sections["grid"] = {
        "n_trials": len(grid_result.results),
        "best_params": grid_result.best_params,
        "best_metrics": best.metrics if best else None,
        "summary_head": grid_result.summary()[:10],
        "failures": grid_result.failures[:5],
    }
    if best is None:
        gates.append(_gate("insufficient_trials", False, 0, f">= 3 trades; >= 1 candidate"))
        return _finalise(spec, data, gates, sections, trial_counter)

    # 2. Walk-forward (rolling and anchored).
    for mode in ("rolling", "anchored"):
        try:
            wfo = adapter.run_walk_forward(
                spec,
                grid,
                data,
                folds=folds,
                mode=mode,
                embargo_bars=embargo,
                objective="sharpe",
                min_trades=3,
                trial_counter=trial_counter,
            )
            sections[f"wfo_{mode}"] = wfo.to_dict()
            if mode == "rolling":
                oos_sharpe = float(wfo.metrics.get("sharpe", 0.0))
                gates.append(
                    _gate(
                        "walk_forward_oos_sharpe",
                        oos_sharpe >= GATES["oos_sharpe_min"],
                        oos_sharpe,
                        f">= {GATES['oos_sharpe_min']}",
                        "out-of-sample Sharpe across rolling folds",
                    )
                )
                efficiencies = [f.get("efficiency") for f in wfo.folds if f.get("efficiency") is not None]
                efficiency = float(np.median(efficiencies)) if efficiencies else 0.0
                gates.append(
                    _gate(
                        "wfo_efficiency",
                        efficiency >= GATES["wfo_efficiency_min"],
                        efficiency,
                        f">= {GATES['wfo_efficiency_min']}",
                        "median OOS/IS Sharpe ratio",
                    )
                )
        except ValueError as exc:
            sections[f"wfo_{mode}"] = {"error": str(exc)}
            gates.append(_gate(f"walk_forward_{mode}", False, None, "runs", str(exc)))

    # 3. CPCV.
    try:
        splits = combinatorial_purged_cv(len(data.index), cpcv_groups, cpcv_k, embargo)
        oos_sharpes = []
        for split in splits:
            train_close = data.close.iloc[split.train]
            test_close = data.close.iloc[split.test]
            if len(train_close) < 40 or len(test_close) < 20:
                continue
            test_data = MarketData(
                close=test_close,
                benchmark=None if data.benchmark is None else data.benchmark.reindex(test_close.index),
            )
            params = best.params
            result = adapter.run_backtest(spec.with_params(params), test_data, trial_counter=trial_counter)
            oos_sharpes.append(float(result.metrics.get("sharpe", 0.0)))
        median = float(np.median(oos_sharpes)) if oos_sharpes else 0.0
        sections["cpcv"] = {
            "n_splits": len(splits),
            "n_evaluated": len(oos_sharpes),
            "median_oos_sharpe": median,
            "p25": float(np.percentile(oos_sharpes, 25)) if oos_sharpes else 0.0,
            "p75": float(np.percentile(oos_sharpes, 75)) if oos_sharpes else 0.0,
            "k_test": cpcv_k,
            "n_groups": cpcv_groups,
        }
        gates.append(
            _gate(
                "cpcv_median_sharpe",
                median >= GATES["cpcv_median_sharpe_min"],
                median,
                f">= {GATES['cpcv_median_sharpe_min']}",
                f"{len(oos_sharpes)} purged CPCV test paths",
            )
        )
    except ValueError as exc:
        sections["cpcv"] = {"error": str(exc)}
        gates.append(_gate("cpcv", False, None, "runs", str(exc)))

    # 4. PBO across the grid.
    try:
        matrix = pd.concat([r.returns.rename(i) for i, r in enumerate(grid_result.results)], axis=1)
        pbo = cscv_pbo(matrix)
        sections["pbo"] = pbo.as_dict()
        gates.append(
            _gate("pbo", pbo.pbo <= GATES["pbo_max"], pbo.pbo, f"<= {GATES['pbo_max']}", "CSCV overfit probability")
        )
    except ValueError as exc:
        sections["pbo"] = {"error": str(exc)}
        gates.append(_gate("pbo", False, None, "computed", str(exc)))

    # 5. Deflated Sharpe with the global trial count.
    n_trials = max(1, trial_counter.value if trial_counter else len(grid_result.results))
    observed_sr = float(best.metrics.get("sharpe", 0.0))
    n_obs = int(best.metrics.get("n_periods", 0))
    sr_var = float(np.var([r.metrics.get("sharpe", 0.0) for r in grid_result.results], ddof=1)) if len(grid_result.results) > 1 else None
    dsr = deflated_sharpe_ratio(
        observed_sr,
        n_trials,
        n_obs,
        skew=float(best.metrics.get("skew", 0.0)),
        kurtosis=float(best.metrics.get("kurtosis", 3.0)) + 3.0,
        sr_variance=sr_var,
    )
    sections["deflated_sharpe"] = {
        "dsr": dsr,
        "observed_sharpe": observed_sr,
        "n_trials": n_trials,
        "expected_max_sharpe": expected_max_sharpe(n_trials, n_obs, sr_var),
        "psr_vs_zero": probabilistic_sharpe_ratio(
            observed_sr, n_obs, float(best.metrics.get("skew", 0.0)), float(best.metrics.get("kurtosis", 3.0)) + 3.0
        ),
        "trial_source": "counter" if trial_counter is not None else "memory",
    }
    gates.append(_gate("deflated_sharpe", dsr >= GATES["dsr_min"], dsr, f">= {GATES['dsr_min']}", f"N={n_trials} trials"))

    # 6. Monte Carlo.
    block_mc = block_bootstrap_returns(best.returns, n_sims=mc_sims, seed=seed)
    sections["monte_carlo"] = {"block_bootstrap": block_mc.as_dict()}
    gates.append(
        _gate(
            "monte_carlo_block_bootstrap",
            block_mc.prob_le_zero <= GATES["mc_prob_le_zero_max"],
            block_mc.prob_le_zero,
            f"P(SR<=0) <= {GATES['mc_prob_le_zero_max']}",
        )
    )
    trade_mc = trade_bootstrap([t.pnl for t in best.trades], n_sims=mc_sims, seed=seed)
    sections["monte_carlo"]["trade_bootstrap"] = trade_mc.as_dict()

    universe_returns = data.close[spec.universe].pct_change(fill_method=None).mean(axis=1)
    re_null = random_entry_null(universe_returns, observed_sr, n_sims=max(100, mc_sims // 2), seed=seed)
    sections["monte_carlo"]["random_entry"] = re_null.as_dict()
    gates.append(
        _gate(
            "random_entry_null",
            re_null.p_value <= GATES["random_entry_p_max"],
            re_null.p_value,
            f"p <= {GATES['random_entry_p_max']}",
        )
    )
    shuffle = signal_shuffle_null(
        best.positions,
        data.close[spec.universe].pct_change(fill_method=None).fillna(0.0),
        observed_sr,
        n_sims=max(100, mc_sims // 2),
        seed=seed,
    )
    sections["monte_carlo"]["signal_shuffle"] = shuffle.as_dict()

    # 7. Lookahead: static + empirical canary.
    scan = lookahead_scan(spec.signal_code)
    sections["lookahead_scan"] = scan
    gates.append(_gate("lookahead_scan", scan["passed"], None, "no findings", "; ".join(scan["findings"])))

    def _positions_for(frame: pd.DataFrame) -> pd.DataFrame:
        local = MarketData(close=frame)
        local_result = get_adapter("native").run_backtest(spec.with_params(best.params), local)
        return local_result.positions

    canary = future_canary(_positions_for, data.close[spec.universe])
    sections["future_canary"] = canary
    gates.append(_gate("future_canary", canary.get("passed", False), canary.get("max_abs_diff"), "no past change"))

    # 8. Cost & execution stress.
    def _runner(mult: float, delay: int) -> float:
        stressed = get_adapter("native").run_backtest(
            spec.with_params(best.params), data, cost_multiplier=mult, delay_bars=delay
        )
        return float(stressed.metrics.get("sharpe", 0.0))

    stress = cost_sensitivity(_runner)
    sections["cost_sensitivity"] = stress
    gates.append(
        _gate(
            "cost_3x",
            bool((stress["stressed_sharpe"] or 0.0) >= GATES["cost_3x_sharpe_min"]),
            stress["stressed_sharpe"],
            f">= {GATES['cost_3x_sharpe_min']} at 3x costs, +1 bar",
        )
    )
    delay_row = next((r for r in stress["table"] if r["cost_multiplier"] == 3.0 and r["delay_bars"] == 2), None)
    gates.append(
        _gate(
            "execution_delay",
            bool(((delay_row or {}).get("sharpe") or 0.0) >= GATES["delay_sharpe_min"]),
            (delay_row or {}).get("sharpe"),
            f">= {GATES['delay_sharpe_min']} with +1 extra bar delay",
        )
    )

    # 9. Parameter plateau.
    plateau = parameter_plateau(grid_result.summary())
    sections["parameter_stability"] = plateau
    gates.append(
        _gate(
            "parameter_plateau",
            plateau["plateau_share"] >= GATES["plateau_share_min"],
            plateau["plateau_share"],
            f">= {GATES['plateau_share_min']}",
        )
    )

    # 10. Track record and regimes.
    years = len(best.returns) / TRADING_DAYS
    n_done = len(best.trades)
    gates.append(
        _gate(
            "minimum_track_record",
            years >= GATES["min_years"] and n_done >= min_trades,
            n_done,
            f">= {GATES['min_years']}y and >= {min_trades} trades",
            f"{years:.2f} years, {n_done} trades",
        )
    )
    benchmark = data.benchmark_series(spec.benchmark)
    sections["regimes"] = regime_analysis(
        best.returns,
        benchmark if benchmark is not None else data.close[spec.universe].mean(axis=1),
    )

    return _finalise(spec, data, gates, sections, trial_counter)


def _finalise(
    spec: StrategySpec,
    data: MarketData,
    gates: list[Gate],
    sections: dict[str, Any],
    trial_counter: TrialCounter | None,
) -> ValidationReport:
    critical = {
        "walk_forward_oos_sharpe",
        "deflated_sharpe",
        "pbo",
        "cost_3x",
        "lookahead_scan",
        "future_canary",
        "monte_carlo_block_bootstrap",
    }
    total = max(1, len(gates))
    passed_count = sum(1 for g in gates if g.passed)
    score = passed_count / total
    critical_ok = all(g.passed for g in gates if g.name in critical)
    passed = critical_ok and score >= 0.70
    if passed and score >= 0.85:
        recommendation = "candidate"
    elif passed:
        recommendation = "validated"
    else:
        recommendation = "reject"
    return ValidationReport(
        spec_name=spec.name,
        spec_hash=spec.spec_hash(),
        data_hash=data.data_hash(),
        gates=gates,
        passed=passed,
        score=score,
        recommendation=recommendation,
        trial_index=trial_counter.value if trial_counter else 0,
        n_trials_used=max(1, trial_counter.value if trial_counter else 0),
        sections=sections,
    )
