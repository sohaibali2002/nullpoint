"""Job queue: Arq worker, nightly scheduler and a safe subprocess fallback for local dev."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog
from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agents.orchestrator import SprintRunner
from api.config import settings
from api.db import SessionLocal, init_db
from api.models import JobAttempt, Organization, Setting, Sprint, utcnow
from api.observability import (
    SPRINT_ATTEMPTS,
    SPRINT_DURATION,
    SPRINT_JOBS,
    SPRINT_REQUEUES,
    STALE_SPRINTS,
)

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


_SECRET_ENV_MARKERS = (
    "SECRET",
    "TOKEN",
    "PASSWORD",
    "API_KEY",
    "WEBHOOK",
    "PRIVATE_KEY",
    "CLIENT_ID",
    "CREDENTIAL",
)
_WORKER_SAFE_ENV = {
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "TMPDIR",
    "PYTHONPATH",
    "VIRTUAL_ENV",
    "APP_ENV",
    "LOG_LEVEL",
    "DATABASE_URL",
    "REDIS_URL",
    "LAKE_ROOT",
    "ARTIFACTS_DIR",
    "JOB_RUNNER",
    "SANDBOX_BACKEND",
    "ALLOW_NETWORK",
}


def _isolated_worker_env() -> dict[str, str]:
    """Give the worker only non-secret config; never inherit the API's full environment."""
    env = {key: value for key, value in os.environ.items() if key in _WORKER_SAFE_ENV}
    env["NULLPOINT_JOB_CHILD"] = "1"
    return env


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
            env=_isolated_worker_env(),
        )
    return "subprocess"


_TRANSIENT_MARKERS = (
    "timeout",
    "timed out",
    "connection",
    "connection reset",
    "temporarily unavailable",
    "rate limit",
    "too many requests",
    "http 429",
    "http 502",
    "http 503",
    "network",
    "database is locked",
    "service unavailable",
)


def is_transient_error(error: str | None) -> bool:
    """Only retry failures that can plausibly succeed on a fresh worker."""
    lowered = (error or "").lower()
    return any(marker in lowered for marker in _TRANSIENT_MARKERS)


def run_sprint_sync(sprint_id: str) -> dict[str, Any]:
    """Run a sprint in the current process and record a durable attempt ledger row."""
    init_db()
    session = SessionLocal()
    started = time.perf_counter()
    attempt: JobAttempt | None = None
    try:
        sprint = session.get(Sprint, sprint_id)
        if sprint is None:
            return {"status": "missing"}
        attempt = session.execute(
            select(JobAttempt)
            .where(JobAttempt.sprint_id == sprint_id, JobAttempt.status == "queued")
            .order_by(JobAttempt.attempt.desc())
            .limit(1)
        ).scalar_one_or_none()
        if attempt is None:
            attempt_no = int(
                session.execute(
                    select(func.count(JobAttempt.id)).where(JobAttempt.sprint_id == sprint_id)
                ).scalar_one()
            ) + 1
            attempt = JobAttempt(
                org_id=sprint.org_id,
                sprint_id=sprint_id,
                attempt=attempt_no,
                status="running",
            )
            session.add(attempt)
        else:
            attempt_no = attempt.attempt
            attempt.status = "running"
            attempt.started_at = utcnow()
            attempt.ended_at = None
            attempt.error = None
        assert attempt is not None
        sprint.last_heartbeat_at = utcnow()
        session.commit()
        SPRINT_ATTEMPTS.inc()
        from api.services.alerts import alert_callback

        runner = SprintRunner(
            session,
            sprint.id,
            settings=settings,
            allow_network=True,
            alert=alert_callback(session, sprint.org_id),
        )
        result = runner.run()
        status = str(result.get("status") or "unknown")
        error = str(result.get("error") or "") or None
        attempt.status = "failed" if status == "failed" else "succeeded"
        attempt.transient = bool(status == "failed" and is_transient_error(error))
        attempt.error = error
        attempt.ended_at = utcnow()
        sprint.last_heartbeat_at = utcnow()
        session.commit()
        SPRINT_JOBS.labels(status=status).inc()
        log.info("sprint_finished", sprint_id=sprint_id, status=status, attempt=attempt_no)
        return result
    except Exception as exc:
        session.rollback()
        if attempt is not None:
            attempt.status = "failed"
            attempt.error = f"{type(exc).__name__}: {exc}"
            attempt.transient = is_transient_error(attempt.error)
            attempt.ended_at = utcnow()
            session.commit()
        log.exception("sprint_attempt_crashed", sprint_id=sprint_id)
        SPRINT_JOBS.labels(status="crashed").inc()
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    finally:
        SPRINT_DURATION.observe(time.perf_counter() - started)
        session.close()


def reap_stalled_sprints(*, stale_minutes: int | None = None) -> list[str]:
    """Requeue sprints whose worker vanished, bounded by ``job_max_attempts``.

    A sprint that genuinely failed is not retried automatically: deterministic failures would just
    burn trials and duplicate strategies.  This reaper only handles a missing heartbeat, which is
    the signature of a crashed or killed worker.
    """
    session = SessionLocal()
    requeued: list[str] = []
    cutoff = datetime.now(UTC) - timedelta(minutes=stale_minutes or settings.job_stale_minutes)
    try:
        stale = list(
            session.execute(
                select(Sprint).where(
                    Sprint.status == "running",
                    func.coalesce(Sprint.last_heartbeat_at, Sprint.started_at) < cutoff,
                )
            ).scalars()
        )
        STALE_SPRINTS.set(len(stale))
        for sprint in stale:
            attempts = int(
                session.execute(
                    select(func.count(JobAttempt.id)).where(JobAttempt.sprint_id == sprint.id)
                ).scalar_one()
            )
            if attempts >= settings.job_max_attempts:
                sprint.status = "failed"
                sprint.error = f"worker heartbeat stale after {attempts} attempts; retry budget exhausted"
                sprint.ended_at = utcnow()
                session.commit()
                SPRINT_JOBS.labels(status="failed").inc()
                continue
            sprint.status = "queued"
            sprint.started_at = None
            sprint.last_heartbeat_at = None
            sprint.ended_at = None
            sprint.error = "worker heartbeat stale; requeued"
            session.add(
                JobAttempt(
                    org_id=sprint.org_id,
                    sprint_id=sprint.id,
                    attempt=attempts + 1,
                    status="queued",
                    error=sprint.error,
                )
            )
            session.commit()
            try:
                enqueue_sprint(sprint.id)
            except Exception as exc:
                sprint.status = "failed"
                sprint.error = f"requeue failed: {type(exc).__name__}: {exc}"
                session.commit()
                continue
            requeued.append(sprint.id)
            SPRINT_REQUEUES.inc()
            log.warning("sprint_requeued_after_stale_heartbeat", sprint_id=sprint.id, attempts=attempts)
        return requeued
    finally:
        session.close()


# ------------------------------------------------------------------ arq hooks
async def run_sprint_job(ctx: dict[str, Any], sprint_id: str) -> dict[str, Any]:
    return await asyncio.to_thread(run_sprint_sync, sprint_id)


async def reap_stalled_job(ctx: dict[str, Any]) -> list[str]:
    """Arq-compatible wrapper around the synchronous reaper."""
    return await asyncio.to_thread(reap_stalled_sprints)


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
    functions = [run_sprint_job, nightly_research, drift_check, reap_stalled_job]
    cron_jobs = [
        cron(nightly_research, hour=2, minute=0),
        cron(drift_check, hour=22, minute=0),
        cron(reap_stalled_job, minute={0, 15, 30, 45}),
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
