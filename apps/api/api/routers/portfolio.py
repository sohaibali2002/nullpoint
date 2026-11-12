"""Portfolio what-if builder and paper-trading endpoints."""

from __future__ import annotations

from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.db import get_db
from api.deps import can_write, get_principal
from api.models import Experiment, PaperRun, Portfolio, Strategy
from api.schemas import PaperIn, PortfolioIn
from api.security import Principal
from api.services import paper as paper_service
from engine.portfolio import METHODS, construct, portfolio_metrics

router = APIRouter(tags=["portfolio"])


@router.get("/portfolios")
def list_portfolios(
    db: Session = Depends(get_db), principal: Principal = Depends(get_principal)
) -> list[dict[str, Any]]:
    rows = db.execute(
        select(Portfolio).where(Portfolio.org_id == principal.org_id).order_by(Portfolio.created_at.desc())
    ).scalars()
    return [
        {
            "id": row.id,
            "name": row.name,
            "method": row.method,
            "weights": row.weights,
            "metrics": row.metrics,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]


@router.post("/portfolios", status_code=201)
def create_portfolio(
    payload: PortfolioIn,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    if not can_write(principal):
        raise HTTPException(status_code=403, detail="viewer role cannot build portfolios")
    if payload.method not in METHODS:
        raise HTTPException(status_code=400, detail=f"method must be one of {METHODS}")
    strategies = []
    for strategy_id in payload.strategy_ids:
        strategy = db.get(Strategy, strategy_id)
        if strategy is None or strategy.org_id != principal.org_id:
            raise HTTPException(status_code=404, detail=f"strategy {strategy_id} not found")
        strategies.append(strategy)

    series: dict[str, pd.Series] = {}
    names: dict[str, str] = {}
    for strategy in strategies:
        experiment = db.execute(
            select(Experiment)
            .where(Experiment.strategy_id == strategy.id, Experiment.returns.isnot(None))
            .order_by(Experiment.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if experiment is None or not experiment.returns:
            raise HTTPException(status_code=409, detail=f"strategy {strategy.name} has no returns to combine")
        dates = pd.to_datetime(experiment.dates) if experiment.dates else pd.RangeIndex(len(experiment.returns))
        series[strategy.id] = pd.Series(experiment.returns, index=dates, name=strategy.id)
        names[strategy.id] = strategy.name

    frame = pd.concat(series.values(), axis=1).dropna(how="all").fillna(0.0)
    frame.columns = list(series.keys())
    weights = construct(frame, method=payload.method, max_weight=payload.max_weight)
    metrics = portfolio_metrics(weights, frame)
    record = Portfolio(
        org_id=principal.org_id,
        name=f"{payload.method.upper()} · {len(strategies)} strategies",
        method=payload.method,
        weights={names[k]: v for k, v in weights.items()},
        metrics=metrics,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return {
        "id": record.id,
        "method": payload.method,
        "weights": record.weights,
        "metrics": metrics,
        "n_observations": len(frame),
        "note": "Weights are constructed on backtest returns, not live performance. Research only.",
    }


@router.get("/paper")
def list_paper_runs(
    db: Session = Depends(get_db), principal: Principal = Depends(get_principal)
) -> list[dict[str, Any]]:
    rows = db.execute(
        select(PaperRun).where(PaperRun.org_id == principal.org_id).order_by(PaperRun.started_at.desc())
    ).scalars()
    return [paper_service.summarise(row) for row in rows]


@router.post("/paper", status_code=201)
def start_paper(
    payload: PaperIn,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    if not can_write(principal):
        raise HTTPException(status_code=403, detail="viewer role cannot start paper trading")
    strategy = db.get(Strategy, payload.strategy_id)
    if strategy is None or strategy.org_id != principal.org_id:
        raise HTTPException(status_code=404, detail="strategy not found")
    run = paper_service.start_paper(
        db, principal.org_id, strategy, initial_equity=payload.initial_equity
    )
    return {
        **paper_service.summarise(run),
        "note": "Simulator bootstraps the strategy's own backtest returns. It is not live performance.",
    }


@router.get("/paper/{run_id}")
def get_paper_run(
    run_id: str, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)
) -> dict[str, Any]:
    run = db.get(PaperRun, run_id)
    if run is None or run.org_id != principal.org_id:
        raise HTTPException(status_code=404, detail="paper run not found")
    return {
        **paper_service.summarise(run),
        "curve": run.equity_curve,
        "fills": run.fills,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "ended_at": run.ended_at.isoformat() if run.ended_at else None,
    }


@router.post("/paper/{run_id}/mark")
def mark_paper_run(
    run_id: str,
    steps: int = 10,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    run = db.get(PaperRun, run_id)
    if run is None or run.org_id != principal.org_id:
        raise HTTPException(status_code=404, detail="paper run not found")
    updated = paper_service.mark_to_market(db, run, steps=max(1, min(steps, 60)))
    return paper_service.summarise(updated)


@router.post("/paper/{run_id}/stop")
def stop_paper_run(
    run_id: str, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)
) -> dict[str, Any]:
    run = db.get(PaperRun, run_id)
    if run is None or run.org_id != principal.org_id:
        raise HTTPException(status_code=404, detail="paper run not found")
    return paper_service.summarise(paper_service.stop_paper(db, run))


@router.get("/paper/alpaca/account")
def alpaca_account(principal: Principal = Depends(get_principal)) -> dict[str, Any]:
    return paper_service.alpaca_account()
