"""Paper-trading engine and drift monitoring. There is no live-trading path.

Brokers:
* ``simulator`` — deterministic bootstrap of the strategy's own out-of-sample returns, used for
  UI flow and tests. Explicitly labelled synthetic.
* ``alpaca_paper`` — Alpaca *paper* account only. Refuses any non-paper base URL; no order routing
  is implemented in v0, so it is used for account state, not execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import numpy as np
import pandas as pd
from fastapi import HTTPException
from fastapi import status as http_status
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.config import settings
from api.models import Experiment, PaperRun, Strategy, StrategyStatus, utcnow


def _latest_experiment(session: Session, strategy_id: str) -> Experiment | None:
    return session.execute(
        select(Experiment)
        .where(Experiment.strategy_id == strategy_id, Experiment.kind.in_(["grid", "validation", "backtest"]))
        .order_by(Experiment.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def start_paper(
    session: Session,
    org_id: str,
    strategy: Strategy,
    *,
    initial_equity: float = 100_000.0,
    broker: str = "simulator",
) -> PaperRun:
    if strategy.org_id != org_id:
        raise HTTPException(status_code=404, detail="strategy not found")
    if StrategyStatus(strategy.status) != StrategyStatus.PAPER:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="strategy must be approved into PAPER status before paper trading",
        )
    experiment = _latest_experiment(session, strategy.id)
    if experiment is None:
        raise HTTPException(status_code=409, detail="strategy has no backtest history to compare against")
    if broker not in {"simulator", "alpaca_paper"}:
        raise HTTPException(status_code=400, detail="broker must be simulator or alpaca_paper")
    if broker == "alpaca_paper":
        _alpaca_guard()

    run = PaperRun(
        org_id=org_id,
        strategy_id=strategy.id,
        broker=broker,
        status="running",
        initial_equity=initial_equity,
        equity_curve=[],
        fills=[],
        drift={},
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _alpaca_guard() -> None:
    if not settings.alpaca_paper:
        raise HTTPException(status_code=400, detail="Alpaca live trading is not supported")
    if "paper-api" not in settings.alpaca_paper_base_url:
        raise HTTPException(status_code=400, detail="only Alpaca paper endpoints are permitted")


def mark_to_market(session: Session, run: PaperRun, *, steps: int = 10) -> PaperRun:
    """Advance the simulator by bootstrapping the strategy's last OOS returns.

    This is a *simulated* forward window, not observed market performance, and every payload says so.
    """
    experiment = _latest_experiment(session, run.strategy_id)
    if experiment is None or not experiment.returns:
        raise HTTPException(status_code=409, detail="no returns available to simulate")
    if run.status != "running":
        raise HTTPException(status_code=409, detail="paper run is not running")
    rng = np.random.default_rng(int(run.id[:8], 16))
    returns = np.asarray(experiment.returns, dtype=float)
    sampled = rng.choice(returns, size=steps, replace=True)
    start_date = (
        pd.Timestamp(run.equity_curve[-1]["date"]) + pd.Timedelta(days=1)
        if run.equity_curve
        else utcnow()
    )
    dates = pd.bdate_range(start=start_date, periods=steps)
    equity = run.equity_curve[-1]["equity"] if run.equity_curve else run.initial_equity
    curve = list(run.equity_curve)
    for date, ret in zip(dates, sampled, strict=True):
        equity *= 1.0 + float(ret)
        curve.append({"date": str(date.date()), "equity": round(equity, 2), "ret": round(float(ret), 6)})
    run.equity_curve = curve
    run.drift = drift_report(run, experiment)
    session.commit()
    session.refresh(run)
    return run


def drift_report(run: PaperRun, experiment: Experiment) -> dict[str, Any]:
    expected = pd.Series(experiment.returns, dtype=float).dropna()
    realized = pd.Series([row["ret"] for row in run.equity_curve], dtype=float).dropna()
    if expected.empty or realized.empty:
        return {"available": False, "note": "insufficient observations"}
    diff = realized.mean() - expected.mean()
    se = np.sqrt(expected.var(ddof=1) / max(len(realized), 1) + expected.var(ddof=1) / max(len(expected), 1))
    t_stat = float(diff / se) if se > 0 else 0.0
    tracking_error = float((realized - expected.mean()).std(ddof=1) * np.sqrt(252)) if len(realized) > 1 else 0.0
    return {
        "available": True,
        "synthetic": True,
        "note": "Simulated forward returns bootstrapped from the strategy's own backtest. Not observed performance.",
        "n_observations": len(realized),
        "realized_mean_daily": float(realized.mean()),
        "expected_mean_daily": float(expected.mean()),
        "difference": float(diff),
        "t_stat": t_stat,
        "tracking_error_annual": tracking_error,
        "alert": bool(abs(t_stat) > 2.0),
        "verdict": "drift detected" if abs(t_stat) > 2.0 else "within backtest noise",
    }


def stop_paper(session: Session, run: PaperRun) -> PaperRun:
    run.status = "stopped"
    run.ended_at = utcnow()
    session.commit()
    session.refresh(run)
    return run


def alpaca_account() -> dict[str, Any]:
    """Read-only account check against the paper endpoint (no order placement)."""
    _alpaca_guard()
    if not settings.alpaca_api_key or not settings.alpaca_api_secret:
        raise HTTPException(status_code=400, detail="ALPACA_API_KEY / ALPACA_API_SECRET are not configured")
    headers = {
        "APCA-API-KEY-ID": settings.alpaca_api_key.get_secret_value(),
        "APCA-API-SECRET-KEY": settings.alpaca_api_secret.get_secret_value(),
    }
    try:
        response = httpx.get(f"{settings.alpaca_paper_base_url}/v2/account", headers=headers, timeout=10)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"alpaca paper API error: {exc}") from exc
    data = response.json()
    return {
        "broker": "alpaca_paper",
        "status": data.get("status"),
        "equity": data.get("equity"),
        "buying_power": data.get("buying_power"),
        "paper": True,
        "note": "Read-only account snapshot. Nullpoint cannot place live orders.",
    }


@dataclass
class PaperSummary:
    run_id: str
    strategy_id: str
    status: str
    broker: str
    equity: float
    return_pct: float
    drift: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "strategy_id": self.strategy_id,
            "status": self.status,
            "broker": self.broker,
            "equity": self.equity,
            "return_pct": self.return_pct,
            "drift": self.drift,
            "disclaimer": "Paper trading only. Simulated unless a paper broker is connected.",
        }


def summarise(run: PaperRun) -> dict[str, Any]:
    equity = run.equity_curve[-1]["equity"] if run.equity_curve else run.initial_equity
    return PaperSummary(
        run_id=run.id,
        strategy_id=run.strategy_id,
        status=run.status,
        broker=run.broker,
        equity=float(equity),
        return_pct=float(equity / run.initial_equity - 1.0),
        drift=run.drift or {},
    ).as_dict()


def curve_frame(run: PaperRun) -> tuple[list[str], list[float]]:
    dates = [row["date"] for row in run.equity_curve]
    values = [row["equity"] for row in run.equity_curve]
    return dates, values


__all__ = [
    "alpaca_account",
    "curve_frame",
    "drift_report",
    "mark_to_market",
    "start_paper",
    "stop_paper",
    "summarise",
]
