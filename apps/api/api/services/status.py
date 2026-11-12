"""Strategy status transitions and the human approval gate.

There is no live-trading execution anywhere in this codebase. ``live_ready`` exists as a label
requiring explicit admin approval, and starting paper trading requires an approval record plus a
CANDIDATE strategy.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException
from fastapi import status as http_status
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.models import (
    ALLOWED_TRANSITIONS,
    Approval,
    Strategy,
    StrategyStatus,
)
from api.security import Principal


def transition(session: Session, strategy: Strategy, target: StrategyStatus) -> Strategy:
    current = StrategyStatus(strategy.status)
    if target == current:
        return strategy
    if target not in ALLOWED_TRANSITIONS[current]:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=f"illegal transition {current.value} → {target.value}",
        )
    strategy.status = target.value
    strategy.updated_at = datetime.now(UTC)
    session.commit()
    session.refresh(strategy)
    return strategy


def approve(
    session: Session, principal: Principal, strategy: Strategy, target: str, note: str = ""
) -> dict[str, object]:
    if target == "live_ready":
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=(
                "Nullpoint has no live-trading hook. Live-ready is a manual, out-of-band decision; "
                "this endpoint only records paper-trading approval."
            ),
        )
    if target != "paper":
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="target must be 'paper'")
    if StrategyStatus(strategy.status) != StrategyStatus.CANDIDATE:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="only CANDIDATE strategies can be approved for paper trading",
        )
    existing = session.execute(
        select(Approval).where(
            Approval.strategy_id == strategy.id,
            Approval.requested_status == "paper",
        )
    ).scalar_one_or_none()
    if existing is None:
        session.add(
            Approval(
                org_id=strategy.org_id,
                strategy_id=strategy.id,
                requested_status="paper",
                approved_by=principal.user_id,
                note=note[:1000],
            )
        )
        session.commit()
    transition(session, strategy, StrategyStatus.PAPER)
    return {
        "strategy_id": strategy.id,
        "status": strategy.status,
        "approved_by": principal.user_id,
        "approved_at": datetime.now(UTC).isoformat(),
        "note": note,
        "disclaimer": "Paper trading only. No live orders can be placed by this platform.",
    }


def approvals_for(session: Session, org_id: str, strategy_id: str) -> list[dict]:
    rows = session.execute(
        select(Approval).where(Approval.org_id == org_id, Approval.strategy_id == strategy_id)
    ).scalars()
    return [
        {
            "id": row.id,
            "requested_status": row.requested_status,
            "approved_by": row.approved_by,
            "approved_at": row.approved_at.isoformat() if row.approved_at else None,
            "note": row.note,
        }
        for row in rows
    ]
