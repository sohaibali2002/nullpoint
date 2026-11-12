"""Performance, risk and attribution metrics.

Every function is total on degenerate input (empty, constant, all-zero) and returns finite
numbers. That matters: the validation layer compares metrics across thousands of trials, and a
single NaN propagates into ranks and PBO.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

TRADING_DAYS = 252


@dataclass(frozen=True)
class Trade:
    symbol: str
    direction: int
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    pnl: float
    bars: int


def _clean(values: pd.Series | list[float]) -> pd.Series:
    series = pd.Series(values, dtype=float)
    return series.replace([np.inf, -np.inf], np.nan).dropna()


def total_return(equity: pd.Series) -> float:
    series = _clean(equity)
    if len(series) < 2 or series.iloc[0] <= 0:
        return 0.0
    return float(series.iloc[-1] / series.iloc[0] - 1.0)


def cagr(equity: pd.Series, periods_per_year: int = TRADING_DAYS) -> float:
    series = _clean(equity)
    if len(series) < 2 or series.iloc[0] <= 0:
        return 0.0
    growth = series.iloc[-1] / series.iloc[0]
    years = len(series) / periods_per_year
    if years <= 0:
        return 0.0
    if growth <= 0:
        return -1.0
    return float(growth ** (1.0 / years) - 1.0)


def volatility(returns: pd.Series, periods_per_year: int = TRADING_DAYS) -> float:
    series = _clean(returns)
    if len(series) < 2:
        return 0.0
    return float(series.std(ddof=1) * np.sqrt(periods_per_year))


def sharpe_ratio(
    returns: pd.Series, periods_per_year: int = TRADING_DAYS, risk_free_annual: float = 0.0
) -> float:
    series = _clean(returns)
    if len(series) < 2:
        return 0.0
    excess = series - risk_free_annual / periods_per_year
    std = excess.std(ddof=1)
    if not np.isfinite(std) or std <= 1e-12:
        return 0.0
    return float(excess.mean() / std * np.sqrt(periods_per_year))


def sortino_ratio(
    returns: pd.Series, periods_per_year: int = TRADING_DAYS, target_annual: float = 0.0
) -> float:
    series = _clean(returns)
    if len(series) < 2:
        return 0.0
    downside = series - target_annual / periods_per_year
    downside = downside[downside < 0]
    if len(downside) < 2:
        return 0.0
    dd = float(np.sqrt((downside**2).mean()))
    if dd <= 1e-12:
        return 0.0
    return float(series.mean() / dd * np.sqrt(periods_per_year))


def drawdown_series(equity: pd.Series) -> pd.Series:
    series = _clean(equity)
    if series.empty:
        return pd.Series(dtype=float)
    return series / series.cummax() - 1.0


def max_drawdown(equity: pd.Series) -> float:
    dd = drawdown_series(equity)
    return float(dd.min()) if len(dd) else 0.0


def drawdown_duration(equity: pd.Series) -> int:
    """Longest number of periods below a prior equity peak."""
    series = _clean(equity)
    if len(series) < 2:
        return 0
    under = (series < series.cummax()).to_numpy()
    longest = current = 0
    for flag in under:
        current = current + 1 if flag else 0
        longest = max(longest, current)
    return int(longest)


def calmar_ratio(equity: pd.Series, periods_per_year: int = TRADING_DAYS) -> float:
    mdd = max_drawdown(equity)
    if mdd >= -1e-9:
        return 0.0
    return float(cagr(equity, periods_per_year) / abs(mdd))


def profit_factor(pnls: list[float]) -> float:
    gains = sum(p for p in pnls if p > 0)
    losses = sum(-p for p in pnls if p < 0)
    if losses <= 1e-12:
        return float(gains > 0) * 10.0
    return float(gains / losses)


def win_rate(pnls: list[float]) -> float:
    if not pnls:
        return 0.0
    return float(sum(1 for p in pnls if p > 0) / len(pnls))


def expectancy(pnls: list[float]) -> float:
    return float(np.mean(pnls)) if pnls else 0.0


def turnover(positions: pd.DataFrame, periods_per_year: int = TRADING_DAYS) -> float:
    if positions.empty:
        return 0.0
    traded = positions.diff().abs().fillna(positions.abs()).sum(axis=1)
    return float(traded.mean() * periods_per_year)


def exposure(positions: pd.DataFrame) -> float:
    if positions.empty:
        return 0.0
    return float(positions.abs().sum(axis=1).mean())


def tail_ratio(returns: pd.Series) -> float:
    series = _clean(returns)
    if len(series) < 20:
        return 0.0
    right = float(np.nanpercentile(series, 95))
    left = abs(float(np.nanpercentile(series, 5)))
    if left <= 1e-12:
        return 0.0
    return right / left


def skewness(returns: pd.Series) -> float:
    series = _clean(returns)
    return float(series.skew()) if len(series) > 2 and series.std(ddof=1) > 0 else 0.0


def kurtosis(returns: pd.Series) -> float:
    series = _clean(returns)
    return float(series.kurtosis()) if len(series) > 3 and series.std(ddof=1) > 0 else 0.0


def var_cvar(returns: pd.Series, level: float = 0.05) -> tuple[float, float]:
    series = _clean(returns)
    if len(series) < 20:
        return 0.0, 0.0
    var = float(np.nanquantile(series, level))
    tail = series[series <= var]
    cvar = float(tail.mean()) if len(tail) else var
    return var, cvar


def alpha_beta(
    returns: pd.Series, benchmark: pd.Series, periods_per_year: int = TRADING_DAYS
) -> tuple[float, float]:
    frame = pd.concat([_clean(returns), _clean(benchmark)], axis=1, keys=["r", "b"]).dropna()
    if len(frame) < 20 or frame["b"].var(ddof=1) <= 1e-12:
        return 0.0, 0.0
    beta = float(frame["r"].cov(frame["b"]) / frame["b"].var(ddof=1))
    alpha = float((frame["r"].mean() - beta * frame["b"].mean()) * periods_per_year)
    return alpha, beta


def factor_attribution(
    returns: pd.Series, factors: dict[str, pd.Series], periods_per_year: int = TRADING_DAYS
) -> dict[str, Any]:
    """OLS of strategy returns on factor returns. Reported, never used to claim causation."""
    columns = {name: _clean(series) for name, series in factors.items()}
    columns["strategy"] = _clean(returns)
    frame = pd.DataFrame(columns).dropna()
    if len(frame) < max(20, 3 * len(factors)):
        return {"alpha_annual": 0.0, "betas": {}, "t_stats": {}, "r2": 0.0, "n_obs": len(frame)}
    y = frame["strategy"].to_numpy()
    names = [c for c in frame.columns if c != "strategy"]
    X = np.column_stack([np.ones(len(frame)), *[frame[c].to_numpy() for c in names]])
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ coef
    dof = max(1, len(y) - X.shape[1])
    sigma2 = float(resid @ resid / dof)
    try:
        cov = sigma2 * np.linalg.inv(X.T @ X)
        se = np.sqrt(np.diag(cov))
    except np.linalg.LinAlgError:  # pragma: no cover - singular design
        se = np.full(X.shape[1], np.nan)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - float(resid @ resid) / ss_tot if ss_tot > 1e-12 else 0.0
    return {
        "alpha_annual": float(coef[0] * periods_per_year),
        "betas": {name: float(coef[i + 1]) for i, name in enumerate(names)},
        "t_stats": {
            "alpha": float(coef[0] / se[0]) if se[0] > 0 else 0.0,
            **{name: float(coef[i + 1] / se[i + 1]) for i, name in enumerate(names)},
        },
        "r2": float(r2),
        "n_obs": int(len(frame)),
    }


def compute_metrics(
    returns: pd.Series,
    equity: pd.Series,
    *,
    positions: pd.DataFrame | None = None,
    trades: list[Trade] | None = None,
    benchmark: pd.Series | None = None,
    periods_per_year: int = TRADING_DAYS,
) -> dict[str, Any]:
    rets = _clean(returns)
    pnls = [t.pnl for t in (trades or [])]
    var, cvar = var_cvar(rets)
    metrics: dict[str, Any] = {
        "total_return": total_return(equity),
        "cagr": cagr(equity, periods_per_year),
        "volatility": volatility(rets, periods_per_year),
        "sharpe": sharpe_ratio(rets, periods_per_year),
        "sortino": sortino_ratio(rets, periods_per_year),
        "calmar": calmar_ratio(equity, periods_per_year),
        "max_drawdown": max_drawdown(equity),
        "max_drawdown_duration": drawdown_duration(equity),
        "n_periods": int(len(rets)),
        "n_trades": len(pnls),
        "profit_factor": profit_factor(pnls),
        "win_rate": win_rate(pnls),
        "expectancy": expectancy(pnls),
        "turnover_annual": turnover(positions, periods_per_year) if positions is not None else 0.0,
        "exposure": exposure(positions) if positions is not None else 0.0,
        "tail_ratio": tail_ratio(rets),
        "skew": skewness(rets),
        "kurtosis": kurtosis(rets),
        "var_95": var,
        "cvar_95": cvar,
        "best_day": float(rets.max()) if len(rets) else 0.0,
        "worst_day": float(rets.min()) if len(rets) else 0.0,
        "hit_rate_daily": float((rets > 0).mean()) if len(rets) else 0.0,
    }
    if benchmark is not None and len(benchmark) > 0:
        alpha, beta = alpha_beta(rets, benchmark, periods_per_year)
        bench_clean = _clean(benchmark)
        metrics.update(
            {
                "alpha_annual": alpha,
                "beta": beta,
                "benchmark_sharpe": sharpe_ratio(bench_clean, periods_per_year),
                "excess_sharpe": metrics["sharpe"] - sharpe_ratio(bench_clean, periods_per_year),
            }
        )
    return metrics


def trades_to_dicts(trades: list[Trade]) -> list[dict[str, Any]]:
    return [asdict(t) for t in trades]
