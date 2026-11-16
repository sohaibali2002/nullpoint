"""Agent chat: persistent conversations with the research roster."""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agents.roster import AGENT_SPECS
from api.config import settings
from api.db import SessionLocal, get_db
from api.deps import can_write, get_principal
from api.models import ChatMessage, ChatThread, new_id
from api.schemas import ChatMessageIn, ChatThreadIn
from api.security import Principal
from api.services import chat as chat_service

router = APIRouter(prefix="/chats", tags=["chat"])


def _thread_or_404(db: Session, org_id: str, user_id: str, thread_id: str) -> ChatThread:
    thread = db.get(ChatThread, thread_id)
    if thread is None or thread.org_id != org_id or thread.user_id != user_id:
        raise HTTPException(status_code=404, detail="conversation not found")
    return thread


def _summary(db: Session, thread: ChatThread) -> dict[str, Any]:
    last = db.execute(
        select(ChatMessage)
        .where(ChatMessage.thread_id == thread.id)
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    count = db.execute(
        select(func.count(ChatMessage.id)).where(ChatMessage.thread_id == thread.id)
    ).scalar_one()
    return chat_service.thread_dict(
        thread,
        last_message=chat_service._snippet(last.content) if last else None,
        message_count=int(count or 0),
    )


@router.get("")
def list_threads(
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> list[dict[str, Any]]:
    rows = (
        db.execute(
            select(ChatThread)
            .where(ChatThread.org_id == principal.org_id, ChatThread.user_id == principal.user_id)
            .order_by(ChatThread.updated_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return [_summary(db, row) for row in rows]


@router.post("", status_code=201)
def create_thread(
    payload: ChatThreadIn,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    if not can_write(principal):
        raise HTTPException(status_code=403, detail="viewer role cannot start conversations")

    requested = [item.strip().lower() for item in payload.agents if item.strip()]
    if payload.auto:
        thread = ChatThread(
            id=new_id(),
            org_id=principal.org_id,
            user_id=principal.user_id,
            agent="orchestrator",
            mode="auto",
            participants=[],
            title=(payload.title or "New auto research").strip(),
        )
    elif requested:
        participants = list(dict.fromkeys(item for item in requested if item != "orchestrator"))
        unknown = [item for item in participants if item not in AGENT_SPECS]
        if unknown:
            raise HTTPException(status_code=400, detail=f"unknown agents: {unknown}")
        if len(participants) < 2:
            raise HTTPException(status_code=400, detail="a research room needs at least two specialists")
        if len(participants) > 5:
            raise HTTPException(status_code=400, detail="a research room supports at most five specialists")
        thread = ChatThread(
            id=new_id(),
            org_id=principal.org_id,
            user_id=principal.user_id,
            agent="orchestrator",
            mode="room",
            participants=participants,
            title=(payload.title or "New research room").strip(),
        )
    else:
        agent = (payload.agent or "orchestrator").strip().lower()
        if agent not in AGENT_SPECS:
            raise HTTPException(status_code=400, detail=f"unknown agent: {payload.agent}")
        thread = ChatThread(
            id=new_id(),
            org_id=principal.org_id,
            user_id=principal.user_id,
            agent=agent,
            mode="solo",
            participants=[],
            title=(payload.title or f"New conversation with {AGENT_SPECS[agent].name}").strip(),
        )
    db.add(thread)
    db.commit()
    db.refresh(thread)
    return chat_service.detail(db, thread)


@router.get("/{thread_id}")
def get_thread(
    thread_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    return chat_service.detail(
        db, _thread_or_404(db, principal.org_id, principal.user_id, thread_id)
    )


@router.delete("/{thread_id}", status_code=204)
def delete_thread(
    thread_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> Response:
    if not can_write(principal):
        raise HTTPException(status_code=403, detail="viewer role cannot delete conversations")
    thread = _thread_or_404(db, principal.org_id, principal.user_id, thread_id)
    db.delete(thread)
    db.commit()
    return Response(status_code=204)


def _run_generation(thread_id: str, use_web: bool, holder: dict[str, str]) -> None:
    session = SessionLocal()
    try:
        thread = session.get(ChatThread, thread_id)
        if thread is None:
            holder["error"] = "conversation disappeared"
            return
        if thread.mode in {"room", "auto"}:
            chat_service.generate_room_round(
                session, thread, settings=settings, use_web=use_web
            )
        else:
            chat_service.generate_reply(
                session, thread, settings=settings, use_web=use_web
            )
    except Exception as exc:  # surfaced to the client as an SSE error event
        holder["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        session.close()


@router.post("/{thread_id}/messages/stream")
def stream_message(
    thread_id: str,
    payload: ChatMessageIn,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> StreamingResponse:
    """Run a turn while emitting each persisted agent message as SSE.

    The generation runs in a local worker thread so the event stream can flush evidence, each
    specialist, and the synthesis as soon as they commit. The blocking endpoint remains for
    clients that do not support streaming.
    """
    if not can_write(principal):
        raise HTTPException(status_code=403, detail="viewer role cannot send messages")
    thread = _thread_or_404(db, principal.org_id, principal.user_id, thread_id)
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="message cannot be empty")
    chat_service.append_user_message(db, thread, content)
    seen = {
        row[0]
        for row in db.execute(
            select(ChatMessage.id).where(ChatMessage.thread_id == thread.id)
        ).all()
    }
    run_id = thread.id

    async def event_source() -> AsyncIterator[str]:
        holder: dict[str, str] = {}
        worker = threading.Thread(
            target=_run_generation,
            args=(run_id, payload.use_web, holder),
            daemon=True,
        )
        worker.start()
        yield f"event: start\ndata: {json.dumps({'thread_id': run_id})}\n\n"
        while True:
            session = SessionLocal()
            try:
                rows = (
                    session.execute(
                        select(ChatMessage)
                        .where(ChatMessage.thread_id == run_id)
                        .order_by(ChatMessage.created_at, ChatMessage.id)
                    )
                    .scalars()
                    .all()
                )
            finally:
                session.close()
            for row in rows:
                if row.id in seen:
                    continue
                seen.add(row.id)
                yield f"event: message\ndata: {json.dumps(chat_service.message_dict(row), default=str)}\n\n"
            if not worker.is_alive():
                break
            await asyncio.sleep(0.35)
        if holder.get("error"):
            yield f"event: error\ndata: {json.dumps({'detail': holder['error']})}\n\n"
        yield f"event: done\ndata: {json.dumps({'thread_id': run_id})}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@router.post("/{thread_id}/messages")
def send_message(
    thread_id: str,
    payload: ChatMessageIn,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    if not can_write(principal):
        raise HTTPException(status_code=403, detail="viewer role cannot send messages")
    thread = _thread_or_404(db, principal.org_id, principal.user_id, thread_id)
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="message cannot be empty")
    chat_service.append_user_message(db, thread, content)
    try:
        if thread.mode in {"room", "auto"}:
            chat_service.generate_room_round(
                db, thread, settings=settings, use_web=payload.use_web
            )
        else:
            chat_service.generate_reply(db, thread, settings=settings, use_web=payload.use_web)
    except Exception as exc:
        # Keep the user's turn and any completed room turns; the next attempt can continue.
        raise HTTPException(
            status_code=502,
            detail=f"agent reply failed: {type(exc).__name__}: {exc}",
        ) from exc
    return chat_service.detail(db, thread)
