"""Sprints, agent streaming, natural-language sprint launch and the dashboard."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agents.bus import MessageBus
from agents.orchestrator import SprintRunner
from agents.roster import roster_payload
from api.config import settings
from api.db import SessionLocal, get_db
from api.deps import can_write, get_principal
from api.jobs import create_sprint, enqueue_sprint
from api.models import (
    Idea,
    LLMUsage,
    Organization,
    Sprint,
    Strategy,
    StrategyStatus,
    TrialCounter,
)
from api.schemas import ChatSprintIn, IdeaIn, SprintIn
from api.security import Principal
from api.services.alerts import dispatch
from api.services.serializers import idea_dict, sprint_dict, strategy_dict

router = APIRouter(tags=["research"])

SECTOR_ETFS = ["SPY", "QQQ", "XLK", "XLF", "XLE", "XLV", "XLI", "XLP", "XLY", "XLU", "XLB", "XLRE"]
CRYPTO = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]


def _sprint_or_404(db: Session, org_id: str, sprint_id: str) -> Sprint:
    sprint = db.get(Sprint, sprint_id)
    if sprint is None or sprint.org_id != org_id:
        raise HTTPException(status_code=404, detail="sprint not found")
    return sprint


@router.post("/sprints", status_code=202)
def launch_sprint(
    payload: SprintIn,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    if not can_write(principal):
        raise HTTPException(status_code=403, detail="viewer role cannot launch sprints")
    org = db.get(Organization, principal.org_id)
    if org is None:
        raise HTTPException(status_code=400, detail="organization missing")
    sprint = create_sprint(
        db,
        principal.org_id,
        objective=payload.objective,
        universe=payload.universe,
        budget_usd=payload.budget_usd or min(settings.sprint_budget_usd, org.monthly_budget_usd),
        config={
            "max_strategies": payload.max_strategies,
            "start": payload.start,
            "end": payload.end,
            "source": "api",
        },
        created_by=principal.user_id,
    )
    if settings.job_runner == "sync":
        result = SprintRunner(db, sprint.id, settings=settings, allow_network=settings.allow_network).run()
        db.refresh(sprint)
        return {**sprint_dict(sprint), "queue_mode": "sync", "result_status": result.get("status")}
    mode = enqueue_sprint(sprint.id)
    return {**sprint_dict(sprint), "queue_mode": mode}


@router.get("/sprints")
def list_sprints(
    limit: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> list[dict[str, Any]]:
    rows = db.execute(
        select(Sprint)
        .where(Sprint.org_id == principal.org_id)
        .order_by(Sprint.created_at.desc())
        .limit(limit)
    ).scalars()
    return [sprint_dict(row) for row in rows]


@router.get("/sprints/{sprint_id}")
def get_sprint(
    sprint_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    sprint = _sprint_or_404(db, principal.org_id, sprint_id)
    strategies = db.execute(
        select(Strategy).where(Strategy.sprint_id == sprint.id).order_by(Strategy.created_at)
    ).scalars()
    return {**sprint_dict(sprint), "strategies": [strategy_dict(s) for s in strategies]}


@router.post("/sprints/{sprint_id}/kill")
def kill_sprint(
    sprint_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    if not can_write(principal):
        raise HTTPException(status_code=403, detail="viewer role cannot kill sprints")
    sprint = _sprint_or_404(db, principal.org_id, sprint_id)
    sprint.kill_requested = True
    if sprint.status in {"queued", "running"}:
        sprint.status = "kill_requested"
    db.commit()
    dispatch(db, principal.org_id, "sprint_kill", "Kill requested", f"Sprint {sprint.id} was asked to stop.")
    return {"id": sprint.id, "kill_requested": True, "status": sprint.status}


@router.get("/sprints/{sprint_id}/messages")
def get_messages(
    sprint_id: str,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=2000),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    _sprint_or_404(db, principal.org_id, sprint_id)
    bus = MessageBus(db, principal.org_id, sprint_id)
    messages = bus.messages(after_seq=after, limit=limit)
    return {"sprint_id": sprint_id, "after": after, "messages": messages, "count": len(messages)}


@router.get("/sprints/{sprint_id}/stream")
async def stream_sprint(
    sprint_id: str,
    after: int = Query(default=0, ge=0),
    principal: Principal = Depends(get_principal),
) -> StreamingResponse:
    """Server-sent events over the persisted audit trail. Resumable via ``after``."""
    verify = SessionLocal()
    try:
        sprint = verify.get(Sprint, sprint_id)
        if sprint is None or sprint.org_id != principal.org_id:
            raise HTTPException(status_code=404, detail="sprint not found")
    finally:
        verify.close()

    async def event_source() -> AsyncIterator[str]:
        cursor = after
        idle = 0
        while True:
            session = SessionLocal()
            try:
                sprint_row = session.get(Sprint, sprint_id)
                rows = MessageBus(session, principal.org_id, sprint_id).messages(after_seq=cursor, limit=200)
                terminal = sprint_row is not None and sprint_row.status in {
                    "completed", "failed", "killed", "budget_exhausted"
                }
            finally:
                session.close()
            for row in rows:
                cursor = max(cursor, int(row["seq"]))
                yield f"id: {cursor}\nevent: message\ndata: {json.dumps(row, default=str)}\n\n"
                idle = 0
            if rows:
                continue
            if terminal:
                yield f"event: done\ndata: {json.dumps({'sprint_id': sprint_id, 'seq': cursor})}\n\n"
                return
            idle += 1
            if idle % 15 == 0:
                yield ": keep-alive\n\n"
            if idle > 900:  # ~15 minutes without any new event
                yield "event: timeout\ndata: {}\n\n"
                return
            await asyncio.sleep(1.0)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@router.get("/sprints/{sprint_id}/replay")
def replay_sprint(
    sprint_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    _sprint_or_404(db, principal.org_id, sprint_id)
    return {"sprint_id": sprint_id, "messages": MessageBus.replay(db, sprint_id)}


@router.post("/sprints/from-chat", status_code=202)
def sprint_from_chat(
    payload: ChatSprintIn,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """'Research mean reversion on sector ETFs' → a queued sprint with a parsed universe."""
    text = payload.message.strip()
    lowered = text.lower()
    tokens = re.findall(r"\b[A-Z]{2,6}\b", payload.message)
    if "crypto" in lowered:
        universe = CRYPTO
    elif "sector" in lowered or "etf" in lowered:
        universe = SECTOR_ETFS
    elif tokens:
        universe = tokens[:12]
    else:
        universe = SECTOR_ETFS
    objective = text if len(text) >= 8 else f"Research {text}"
    sprint = create_sprint(
        db,
        principal.org_id,
        objective=objective,
        universe=universe,
        budget_usd=settings.sprint_budget_usd,
        config={"max_strategies": 4, "source": "chat"},
        created_by=principal.user_id,
    )
    if settings.job_runner == "sync":
        SprintRunner(db, sprint.id, settings=settings, allow_network=settings.allow_network).run()
        db.refresh(sprint)
        return {**sprint_dict(sprint), "parsed_universe": universe, "queue_mode": "sync"}
    mode = enqueue_sprint(sprint.id)
    return {**sprint_dict(sprint), "parsed_universe": universe, "queue_mode": mode}


@router.get("/agents")
def list_agents(principal: Principal = Depends(get_principal)) -> dict[str, Any]:
    return {
        "agents": roster_payload(),
        "policy": "tool whitelists enforced by the orchestrator; full audit trail per sprint",
        "models": {
            "orchestrator": settings.model_orchestrator,
            "worker": settings.model_worker,
            "skeptic": settings.model_skeptic,
            "cheap": settings.model_cheap,
        },
        "provider": settings.llm_provider,
    }


@router.get("/dashboard")
def dashboard(
    db: Session = Depends(get_db), principal: Principal = Depends(get_principal)
) -> dict[str, Any]:
    org = db.get(Organization, principal.org_id)
    sprints = db.execute(
        select(Sprint).where(Sprint.org_id == principal.org_id).order_by(Sprint.created_at.desc()).limit(8)
    ).scalars().all()
    status_counts = dict(
        db.execute(
            select(Strategy.status, func.count(Strategy.id))
            .where(Strategy.org_id == principal.org_id)
            .group_by(Strategy.status)
        ).all()
    )
    trials = db.execute(
        select(func.coalesce(func.sum(TrialCounter.count), 0)).where(TrialCounter.org_id == principal.org_id)
    ).scalar_one()
    spend = float(
        db.execute(
            select(func.coalesce(func.sum(LLMUsage.cost_usd), 0.0)).where(LLMUsage.org_id == principal.org_id)
        ).scalar_one()
    )
    leaderboard = db.execute(
        select(Strategy)
        .where(
            Strategy.org_id == principal.org_id,
            Strategy.status.in_([StrategyStatus.VALIDATED.value, StrategyStatus.CANDIDATE.value, StrategyStatus.PAPER.value]),
        )
        .order_by(Strategy.created_at.desc())
        .limit(20)
    ).scalars().all()
    ranked = sorted(
        leaderboard,
        key=lambda s: float((s.metrics or {}).get("sharpe", 0.0)),
        reverse=True,
    )[:5]
    promoted = sum(1 for s in leaderboard if s.status in {"candidate", "paper"})
    return {
        "org": {
            "id": principal.org_id,
            "name": org.name if org else None,
            "monthly_budget_usd": org.monthly_budget_usd if org else 0,
        },
        "sprints": [sprint_dict(s) for s in sprints],
        "strategy_counts": status_counts,
        "leaderboard": [strategy_dict(s) for s in ranked],
        "trials": int(trials),
        "llm_spend_usd": round(spend, 4),
        "cost_per_candidate_usd": round(spend / promoted, 4) if promoted else None,
        "safety": {
            "live_trading_enabled": False,
            "kill_switch": settings.kill_switch,
            "sealed_holdout": True,
        },
        "roster": roster_payload(),
        "disclaimer": "Research only. Not investment advice.",
    }


@router.get("/ideas")
def list_ideas(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> list[dict[str, Any]]:
    rows = db.execute(
        select(Idea).where(Idea.org_id == principal.org_id).order_by(Idea.created_at.desc()).limit(limit)
    ).scalars()
    return [idea_dict(row) for row in rows]


@router.post("/ideas", status_code=201)
def create_idea(
    payload: IdeaIn,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    if not can_write(principal):
        raise HTTPException(status_code=403, detail="viewer role cannot create ideas")
    from agents.embeddings import embed, is_duplicate

    existing = db.execute(select(Idea).where(Idea.org_id == principal.org_id)).scalars().all()
    duplicate, matched, score = is_duplicate(
        f"{payload.title} {payload.hypothesis}", [(row.id, row.embedding) for row in existing if row.embedding]
    )
    idea = Idea(
        org_id=principal.org_id,
        title=payload.title,
        hypothesis=payload.hypothesis,
        family=payload.family,
        tags=payload.tags,
        embedding=embed(f"{payload.title} {payload.hypothesis}"),
        fingerprint=f"human-{payload.title.lower()[:40]}",
        origin="human",
        dedupe_of=matched if duplicate else None,
        dedupe_score=score,
    )
    db.add(idea)
    db.commit()
    db.refresh(idea)
    return {**idea_dict(idea), "duplicate_warning": duplicate, "similarity": round(score, 3)}
