"""Sprints, agent streaming, natural-language sprint launch and the dashboard."""

from __future__ import annotations

import asyncio
import io
import json
import re
import zipfile
from collections.abc import AsyncIterator
from html.parser import HTMLParser
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pypdf import PdfReader
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agents.bus import MessageBus
from agents.knowledge import KnowledgeBase
from agents.orchestrator import SprintRunner
from agents.roster import roster_payload
from api.config import settings
from api.db import SessionLocal, get_db
from api.deps import can_write, get_principal
from api.jobs import create_sprint, enqueue_sprint
from api.models import (
    Idea,
    JobAttempt,
    LLMUsage,
    Memory,
    Organization,
    Sprint,
    Strategy,
    StrategyStatus,
    TrialCounter,
)
from api.schemas import ChatSprintIn, IdeaIn, KnowledgeIn, SprintIn
from api.security import Principal
from api.services.alerts import dispatch
from api.services.serializers import idea_dict, sprint_dict, strategy_dict

router = APIRouter(tags=["research"])

SECTOR_ETFS = ["SPY", "QQQ", "XLK", "XLF", "XLE", "XLV", "XLI", "XLP", "XLY", "XLU", "XLB", "XLRE"]
LARGE_CAPS = ["SPY", "QQQ", "DIA", "IWM", "XLK", "XLF", "XLV", "XLY"]
CRYPTO = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
UNIVERSE_STOPWORDS = {
    "US", "USA", "ETF", "ETFS", "AI", "CEO", "CFO", "EPS", "ROE", "PE", "PB",
    "GDP", "CPI", "FED", "SEC", "IPO", "BUY", "SELL", "HOLD", "AND", "OR", "THE",
}


def parse_universe(text: str) -> list[str]:
    """Turn a natural-language objective into a real, tradable universe."""
    lowered = text.lower()
    tokens = [token for token in re.findall(r"\b[A-Z]{2,6}\b", text) if token not in UNIVERSE_STOPWORDS]
    if "crypto" in lowered or "bitcoin" in lowered:
        return CRYPTO
    if any(phrase in lowered for phrase in ("large cap", "mega cap", "blue chip", "us equity", "u.s. equity", "stocks")):
        return LARGE_CAPS
    if "sector" in lowered or "etf" in lowered:
        return SECTOR_ETFS
    if tokens:
        return list(dict.fromkeys(tokens))[:12]
    return SECTOR_ETFS


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


@router.get("/sprints/{sprint_id}/attempts")
def sprint_attempts(
    sprint_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> list[dict[str, Any]]:
    _sprint_or_404(db, principal.org_id, sprint_id)
    rows = db.execute(
        select(JobAttempt)
        .where(JobAttempt.sprint_id == sprint_id)
        .order_by(JobAttempt.attempt.desc())
    ).scalars()
    return [
        {
            "attempt": row.attempt,
            "status": row.status,
            "transient": row.transient,
            "error": row.error,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "ended_at": row.ended_at.isoformat() if row.ended_at else None,
        }
        for row in rows
    ]


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
    universe = parse_universe(text)
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


# ------------------------------------------------------------------ knowledge

def _knowledge_base(db: Session, org_id: str) -> KnowledgeBase:
    return KnowledgeBase(
        db,
        org_id,
        api_key=settings.voyage_key if settings.allow_network else None,
        model=settings.voyage_model,
        base_url=settings.voyage_base_url,
        chunk_chars=settings.knowledge_chunk_chars,
    )


@router.get("/knowledge")
def knowledge_stats(
    db: Session = Depends(get_db), principal: Principal = Depends(get_principal)
) -> dict[str, Any]:
    return _knowledge_base(db, principal.org_id).stats()


@router.get("/memory")
def memory_feed(
    db: Session = Depends(get_db), principal: Principal = Depends(get_principal)
) -> dict[str, Any]:
    """Recent lessons the desk will recall automatically in the next conversation."""
    rows = (
        db.execute(
            select(Memory)
            .where(Memory.org_id == principal.org_id, Memory.kind != "knowledge")
            .order_by(Memory.created_at.desc())
            .limit(20)
        )
        .scalars()
        .all()
    )
    return {
        "items": [
            {
                "id": row.id,
                "kind": row.kind,
                "title": row.title,
                "body": row.body[:600],
                "thread_id": (row.payload or {}).get("thread_id"),
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ],
        "count": len(rows),
    }


@router.post("/knowledge", status_code=201)
def add_knowledge(
    payload: KnowledgeIn,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    if not can_write(principal):
        raise HTTPException(status_code=403, detail="viewer role cannot add knowledge")
    return _knowledge_base(db, principal.org_id).add(
        payload.title,
        payload.body,
        source=payload.source,
        url=payload.url,
        tags=payload.tags,
    )


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self.parts.append(text)


def _book_text(filename: str, data: bytes) -> str:
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if suffix in {"txt", "md", "markdown", "text", "csv", "json"}:
        return data.decode("utf-8", errors="replace")
    if suffix == "pdf":
        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    if suffix == "epub":
        chunks: list[str] = []
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for name in sorted(archive.namelist()):
                if not name.lower().endswith((".xhtml", ".html", ".htm")):
                    continue
                parser = _TextExtractor()
                parser.feed(archive.read(name).decode("utf-8", errors="replace"))
                if parser.parts:
                    chunks.append(" ".join(parser.parts))
        return "\n\n".join(chunks)
    raise HTTPException(
        status_code=415,
        detail="supported uploads are PDF, EPUB, TXT and Markdown. Convert other formats first.",
    )


@router.post("/knowledge/upload", status_code=201)
async def upload_knowledge(
    file: UploadFile = File(...),
    title: str = Form(default=""),
    tags: str = Form(default="upload"),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """Upload a document the operator has the right to use.

    The file is parsed in memory and only extracted text is stored; the original file is not
    retained. Full books are capped to keep one request inside the serverless budget.
    """
    if not can_write(principal):
        raise HTTPException(status_code=403, detail="viewer role cannot upload knowledge")
    filename = (file.filename or "upload.txt").rsplit("/", 1)[-1]
    data = await file.read()
    if len(data) > 12 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="file is larger than 12 MB")
    text = _book_text(filename, data).strip()
    if len(text) < 40:
        raise HTTPException(status_code=400, detail="the file contains too little extractable text")
    text = text[:500_000]
    title_value = (title.strip() or filename.rsplit(".", 1)[0]).strip()[:200]
    tag_list = [tag.strip() for tag in tags.split(",") if tag.strip()][:20]
    return _knowledge_base(db, principal.org_id).add(
        title_value,
        text,
        source="upload",
        url="",
        tags=tag_list,
        metadata={"filename": filename, "uploaded_by": principal.user_id},
    )


@router.get("/knowledge/search")
def search_knowledge(
    q: str = Query(min_length=2, max_length=500),
    limit: int = Query(default=6, ge=1, le=20),
    tags: str = Query(default="", max_length=200),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    tag_list = [tag.strip() for tag in tags.split(",") if tag.strip()]
    hits = _knowledge_base(db, principal.org_id).search(q, limit=limit, tags=tag_list)
    return {"query": q, "hits": hits, "count": len(hits)}
