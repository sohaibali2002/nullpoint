"""Job queue: Arq worker, nightly scheduler and a safe subprocess fallback for local dev."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from arq import cron
from arq.connections import RedisSettings
from sqlalchemy.orm import Session

from agents.orchestrator import SprintRunner
from api.config import settings
from api.db import SessionLocal, init_db
from api.models import Organization, Setting, Sprint

log = structlog.get_logger("nullpoint.jobs")

DEFAULT_OBJECTIVE = "Find robust, cost-aware systematic strategies across the configured universe"


def create_sprint(
    session: Session,
    org_id: str,
    *,
    objective: str,
    universe: list[str],
    budget_usd: float | None = None,
    config: dict[str, Any] | None = None,
    created_by: str = "human",
) -> Sprint:
    sprint = Sprint(
        org_id=org_id,
        objective=objective,
        universe=[s.upper() for s in universe],
        budget_usd=float(budget_usd or settings.sprint_budget_usd),
        config=config or {},
        created_by=created_by,
        status="queued",
    )
    session.add(sprint)
    session.commit()
    session.refresh(sprint)
    return sprint


def _log_path(sprint_id: str) -> Path:
    logs = Path("artifacts/jobs")
    logs.mkdir(parents=True, exist_ok=True)
    return logs / f"{sprint_id}.log"


def enqueue_sprint(sprint_id: str) -> str:
    """Start a sprint job. Returns the mode used: sync | arq | subprocess."""
    mode = settings.job_runner
    if mode == "sync":
        run_sprint_sync(sprint_id)
        return "sync"

    if mode in {"auto", "arq"}:
        try:
            from arq import create_pool

            async def _enqueue() -> None:
                pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
                await pool.enqueue_job("run_sprint_job", sprint_id)
                await pool.close()

            asyncio.run(asyncio.wait_for(_enqueue(), timeout=5))
            return "arq"
        except Exception as exc:
            if mode == "arq":
                raise
            log.info("arq_unavailable_falling_back", error=str(exc))

    log_file = _log_path(sprint_id)
    with log_file.open("ab") as handle:
        subprocess.Popen(
            [sys.executable, "-m", "api.worker_cli", sprint_id],
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            cwd=os.getcwd(),
            env={**os.environ, "NULLPOINT_JOB_CHILD": "1"},
        )
    return "subprocess"


def run_sprint_sync(sprint_id: str) -> dict[str, Any]:
    """Run a sprint in the current process. Used by the worker and by tests only."""
    init_db()
    session = SessionLocal()
    try:
        sprint = session.get(Sprint, sprint_id)
        if sprint is None:
            return {"status": "missing"}
        from api.services.alerts import alert_callback

        runner = SprintRunner(
            session,
            sprint.id,
            settings=settings,
            allow_network=True,
            alert=alert_callback(session, sprint.org_id),
        )
        result = runner.run()
        log.info("sprint_finished", sprint_id=sprint_id, status=result.get("status"))
        return result
    finally:
        session.close()


# ------------------------------------------------------------------ arq hooks
async def run_sprint_job(ctx: dict[str, Any], sprint_id: str) -> dict[str, Any]:
    return await asyncio.to_thread(run_sprint_sync, sprint_id)


async def nightly_research(ctx: dict[str, Any]) -> list[str]:
    """Create and enqueue one sprint per org that opted into nightly research."""
    launched: list[str] = []
    session = SessionLocal()
    try:
        orgs = session.query(Organization).all()
        for org in orgs:
            setting = session.query(Setting).filter(Setting.org_id == org.id).one_or_none()
            payload = (setting.payload if setting else {}) or {}
            if not payload.get("nightly_enabled", False):
                continue
            universe = payload.get("nightly_universe") or ["SPY", "QQQ", "XLK", "XLF", "XLE", "XLV", "XLV"]
            sprint = create_sprint(
                session,
                org.id,
                objective="Nightly autonomous research sprint: test cross-sectional and regime strategies against the null",
                universe=list(universe),
                budget_usd=float(payload.get("sprint_budget_usd") or settings.sprint_budget_usd),
                config={"max_strategies": 4, "source": "nightly"},
                created_by="scheduler",
            )
            await asyncio.to_thread(enqueue_sprint, sprint.id)
            launched.append(sprint.id)
    finally:
        session.close()
    return launched


async def drift_check(ctx: dict[str, Any]) -> int:
    """Mark running paper simulations forward one step to keep drift monitoring live."""
    from api.models import PaperRun
    from api.services.paper import mark_to_market

    session = SessionLocal()
    checked = 0
    try:
        for run in session.query(PaperRun).filter(PaperRun.status == "running").all():
            try:
                mark_to_market(session, run, steps=5)
                checked += 1
            except Exception:
                session.rollback()
    finally:
        session.close()
    return checked


class WorkerSettings:
    functions = [run_sprint_job, nightly_research, drift_check]
    cron_jobs = [
        cron(nightly_research, hour=2, minute=0),
        cron(drift_check, hour=22, minute=0),
    ]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 2

    @staticmethod
    async def on_startup(ctx: dict[str, Any]) -> None:
        init_db()
        log.info("worker_started", queue="nullpoint", time=datetime.now(UTC).isoformat())

    @staticmethod
    async def on_shutdown(ctx: dict[str, Any]) -> None:
        log.info("worker_stopped", time=time.time())
