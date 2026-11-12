"""Strategy registry, leaderboard, tearsheets, exports and the approval gate."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.db import get_db
from api.deps import can_write, get_principal
from api.models import Experiment, Strategy, StrategyStatus, ValidationReport
from api.schemas import ApproveIn
from api.security import Principal
from api.services import status as status_service
from api.services.serializers import (
    DISCLAIMER,
    experiment_dict,
    strategy_dict,
    validation_dict,
)

router = APIRouter(tags=["strategies"])


def _get(db: Session, org_id: str, strategy_id: str) -> Strategy:
    strategy = db.get(Strategy, strategy_id)
    if strategy is None or strategy.org_id != org_id:
        raise HTTPException(status_code=404, detail="strategy not found")
    return strategy


@router.get("/strategies")
def list_strategies(
    status_filter: str | None = Query(default=None, alias="status"),
    sort: Literal["created", "sharpe", "cagr", "drawdown", "name"] = "created",
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> list[dict[str, Any]]:
    query = select(Strategy).where(Strategy.org_id == principal.org_id)
    if status_filter:
        query = query.where(Strategy.status == status_filter)
    rows = list(db.execute(query.order_by(Strategy.created_at.desc()).limit(limit)).scalars())
    if sort == "name":
        rows.sort(key=lambda s: s.name)
    elif sort == "sharpe":
        rows.sort(key=lambda s: float((s.metrics or {}).get("sharpe", 0.0)), reverse=True)
    elif sort == "cagr":
        rows.sort(key=lambda s: float((s.metrics or {}).get("cagr", 0.0)), reverse=True)
    elif sort == "drawdown":
        rows.sort(key=lambda s: float((s.metrics or {}).get("max_drawdown", 0.0)))
    return [strategy_dict(row) for row in rows]


@router.get("/leaderboard")
def leaderboard(
    limit: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    rows = list(
        db.execute(
            select(Strategy)
            .where(Strategy.org_id == principal.org_id)
            .order_by(Strategy.created_at.desc())
            .limit(300)
        ).scalars()
    )
    promoted = [
        row
        for row in rows
        if row.status in {StrategyStatus.VALIDATED.value, StrategyStatus.CANDIDATE.value, StrategyStatus.PAPER.value}
    ]
    promoted.sort(key=lambda s: float((s.metrics or {}).get("sharpe", 0.0)), reverse=True)
    rejected = [row for row in rows if row.status == StrategyStatus.REJECTED.value][:20]
    return {
        "promoted": [strategy_dict(row) for row in promoted[:limit]],
        "rejected_sample": [strategy_dict(row) for row in rejected],
        "counts": {
            "total": len(rows),
            "promoted": len(promoted),
            "rejected": sum(1 for row in rows if row.status == StrategyStatus.REJECTED.value),
        },
        "ranking_metric": "net Sharpe from the visible sample only; not out-of-sample evidence",
        "disclaimer": DISCLAIMER,
    }


@router.get("/strategies/{strategy_id}")
def get_strategy(
    strategy_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    strategy = _get(db, principal.org_id, strategy_id)
    validations = list(
        db.execute(
            select(ValidationReport)
            .where(ValidationReport.strategy_id == strategy.id)
            .order_by(ValidationReport.created_at.desc())
            .limit(5)
        ).scalars()
    )
    experiments = list(
        db.execute(
            select(Experiment)
            .where(Experiment.strategy_id == strategy.id)
            .order_by(Experiment.created_at.desc())
            .limit(10)
        ).scalars()
    )
    lineage: list[dict[str, Any]] = []
    cursor: Strategy | None = strategy
    seen: set[str] = set()
    while cursor is not None and cursor.id not in seen:
        lineage.append(strategy_dict(cursor))
        seen.add(cursor.id)
        cursor = db.get(Strategy, cursor.parent_id) if cursor.parent_id else None
    return {
        **strategy_dict(strategy, include_spec=True),
        "validation": validation_dict(validations[0]) if validations else None,
        "validations": [validation_dict(v) for v in validations],
        "experiments": [experiment_dict(e) for e in experiments],
        "lineage": lineage,
        "approvals": status_service.approvals_for(db, principal.org_id, strategy.id),
        "data_policy": "visible sample only; sealed holdout requires a human-triggered final check",
    }


@router.get("/strategies/{strategy_id}/validation")
def get_validation(
    strategy_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    strategy = _get(db, principal.org_id, strategy_id)
    report = db.execute(
        select(ValidationReport)
        .where(ValidationReport.strategy_id == strategy.id)
        .order_by(ValidationReport.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if report is None:
        raise HTTPException(status_code=404, detail="no validation report for this strategy")
    return validation_dict(report)


@router.get("/strategies/{strategy_id}/experiments")
def get_experiments(
    strategy_id: str,
    include_series: bool = Query(default=False),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> list[dict[str, Any]]:
    strategy = _get(db, principal.org_id, strategy_id)
    rows = db.execute(
        select(Experiment)
        .where(Experiment.strategy_id == strategy.id)
        .order_by(Experiment.created_at.desc())
        .limit(50)
    ).scalars()
    return [experiment_dict(row, include_series=include_series) for row in rows]


@router.get("/strategies/{strategy_id}/tearsheet", response_class=HTMLResponse)
def get_tearsheet(
    strategy_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> FileResponse:
    strategy = _get(db, principal.org_id, strategy_id)
    path_value = (strategy.metrics or {}).get("tearsheet_path")
    if not path_value:
        raise HTTPException(status_code=404, detail="tearsheet has not been generated yet")
    path = Path(path_value).resolve()
    artifacts = Path("artifacts").resolve()
    if artifacts not in path.parents or not path.exists():
        raise HTTPException(status_code=404, detail="tearsheet not found")
    return FileResponse(path, media_type="text/html", filename=f"{strategy.name[:40]}-tearsheet.html")


@router.post("/strategies/{strategy_id}/approve")
def approve_strategy(
    strategy_id: str,
    payload: ApproveIn,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    if not can_write(principal):
        raise HTTPException(status_code=403, detail="viewer role cannot approve strategies")
    strategy = _get(db, principal.org_id, strategy_id)
    return status_service.approve(db, principal, strategy, payload.target, payload.note)


@router.get("/strategies/{strategy_id}/export")
def export_strategy(
    strategy_id: str,
    format: Literal["python", "json", "pine"] = "python",
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    strategy = _get(db, principal.org_id, strategy_id)
    spec = strategy.spec or {}
    if format == "json":
        content = json.dumps({"spec": spec, "code": strategy.code, "metrics": strategy.metrics}, indent=2)
        language = "json"
    elif format == "pine":
        content = _pine_stub(spec)
        language = "pine"
    else:
        content = _python_export(strategy)
        language = "python"
    return {
        "strategy_id": strategy.id,
        "format": format,
        "language": language,
        "content": content,
        "warning": "Exports are research artifacts. Pine/QuantConnect translations are approximate and must be re-validated.",
    }


@router.get("/strategies/{strategy_id}/diff/{other_id}")
def diff_strategies(
    strategy_id: str,
    other_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    left = _get(db, principal.org_id, strategy_id)
    right = _get(db, principal.org_id, other_id)
    fields = ("params", "risk", "costs", "features", "universe", "hypothesis", "signal_code")
    changes: dict[str, Any] = {}
    for field in fields:
        a = (left.spec or {}).get(field)
        b = (right.spec or {}).get(field)
        if a != b:
            changes[field] = {"from": a, "to": b}
    return {
        "left": strategy_dict(left),
        "right": strategy_dict(right),
        "changes": changes,
        "same_lineage": left.parent_id == right.id or right.parent_id == left.id,
        "note": "Diff of stored specs; metrics are not re-computed here.",
    }


@router.get("/experiments")
def list_experiments(
    strategy_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> list[dict[str, Any]]:
    query = select(Experiment).where(Experiment.org_id == principal.org_id)
    if strategy_id:
        query = query.where(Experiment.strategy_id == strategy_id)
    rows = db.execute(query.order_by(Experiment.created_at.desc()).limit(limit)).scalars()
    return [experiment_dict(row) for row in rows]


@router.get("/experiments/{experiment_id}")
def get_experiment(
    experiment_id: str,
    include_series: bool = Query(default=True),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    row = db.get(Experiment, experiment_id)
    if row is None or row.org_id != principal.org_id:
        raise HTTPException(status_code=404, detail="experiment not found")
    return experiment_dict(row, include_series=include_series)


def _python_export(strategy: Strategy) -> str:
    spec = strategy.spec or {}
    return (
        "# Nullpoint research export — research only, not investment advice.\n"
        "# Re-run with the same data hash and seed to reproduce the ledger entry.\n"
        f"# strategy: {strategy.name} (v{strategy.version}) status={strategy.status}\n"
        f"# code_hash: {strategy.code_hash}\n"
        f"SPEC = {json.dumps(spec, indent=2, sort_keys=True)}\n\n"
        f"{strategy.code}\n\n"
        "# Usage (trusted code only; run agent code through Nullpoint's sandbox):\n"
        "# from engine.strategy import StrategySpec\n"
        "# from engine.native import NativeAdapter\n"
        "# spec = StrategySpec(**SPEC)\n"
        "# result = NativeAdapter().run_backtest(spec, market_data)\n"
    )


def _pine_stub(spec: dict[str, Any]) -> str:
    name = html.escape(str(spec.get("name", "Nullpoint Strategy")))[:60]
    params = spec.get("params", {}) or {}
    lookback = int(params.get("lookback", 20))
    top_n = int(params.get("top_n", 3))
    return f"""//@version=5
// Nullpoint export — APPROXIMATE translation, not validated. Re-test before use.
strategy("{name}", overlay=false, initial_capital=100000, commission_type=strategy.commission.percent, commission_value=0.01)
lookback = input.int({lookback}, "Lookback", minval=2)
topN = input.int({top_n}, "Top N", minval=1)
mom = close / close[lookback] - 1
rank = ta.percentrank(mom, lookback)
longCond = rank > 100 - (topN * 10)
shortCond = rank < (topN * 10)
if longCond
    strategy.entry("L", strategy.long)
if shortCond
    strategy.entry("S", strategy.short)
plot(mom, "momentum")
// WARNING: portfolio sizing, borrow costs and cross-sectional ranking are simplified here.
"""
