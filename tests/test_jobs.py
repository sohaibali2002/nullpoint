from __future__ import annotations

from datetime import UTC, datetime, timedelta

from api import jobs
from api.models import JobAttempt, Organization, Sprint
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker


def _sprint(session, *, status: str = "running", age_minutes: int = 240) -> Sprint:
    org = Organization(name="Jobs Test", slug=f"jobs-{datetime.now(UTC).timestamp()}")
    session.add(org)
    session.commit()
    sprint = Sprint(
        org_id=org.id,
        objective="Test durable worker retries",
        universe=["SPY"],
        status=status,
        started_at=datetime.now(UTC) - timedelta(minutes=age_minutes),
        last_heartbeat_at=datetime.now(UTC) - timedelta(minutes=age_minutes),
    )
    session.add(sprint)
    session.commit()
    session.add(JobAttempt(org_id=org.id, sprint_id=sprint.id, attempt=1, status="running"))
    session.commit()
    return sprint


def _patch_reaper(monkeypatch, db_session):
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(jobs, "SessionLocal", factory)
    requeued: list[str] = []
    monkeypatch.setattr(jobs, "enqueue_sprint", lambda sprint_id: requeued.append(sprint_id) or "test")
    return requeued


def test_stale_sprint_is_requeued_once(monkeypatch, db_session) -> None:
    sprint = _sprint(db_session)
    requeued = _patch_reaper(monkeypatch, db_session)
    monkeypatch.setattr(jobs.settings, "job_max_attempts", 3)

    result = jobs.reap_stalled_sprints(stale_minutes=60)

    assert result == [sprint.id]
    assert requeued == [sprint.id]
    fresh = db_session.get(Sprint, sprint.id, populate_existing=True)
    assert fresh is not None
    assert fresh.status == "queued"
    attempts = list(
        db_session.execute(
            select(JobAttempt).where(JobAttempt.sprint_id == sprint.id).order_by(JobAttempt.attempt)
        ).scalars()
    )
    assert [attempt.attempt for attempt in attempts] == [1, 2]
    assert attempts[-1].status == "queued"


def test_stale_sprint_exhausts_retry_budget(monkeypatch, db_session) -> None:
    sprint = _sprint(db_session)
    for attempt in (2, 3):
        db_session.add(JobAttempt(org_id=sprint.org_id, sprint_id=sprint.id, attempt=attempt, status="failed"))
    db_session.commit()
    requeued = _patch_reaper(monkeypatch, db_session)
    monkeypatch.setattr(jobs.settings, "job_max_attempts", 3)

    result = jobs.reap_stalled_sprints(stale_minutes=60)

    assert result == []
    assert requeued == []
    fresh = db_session.get(Sprint, sprint.id, populate_existing=True)
    assert fresh is not None
    assert fresh.status == "failed"
    assert "retry budget exhausted" in (fresh.error or "")


def test_worker_claims_reserved_attempt_instead_of_creating_another(monkeypatch, db_session) -> None:
    sprint = _sprint(db_session, age_minutes=1)
    db_session.add(
        JobAttempt(org_id=sprint.org_id, sprint_id=sprint.id, attempt=2, status="queued")
    )
    db_session.commit()
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(jobs, "SessionLocal", factory)
    monkeypatch.setattr(jobs, "init_db", lambda: None)

    class FakeRunner:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def run(self) -> dict[str, str]:
            return {"status": "completed"}

    monkeypatch.setattr(jobs, "SprintRunner", FakeRunner)
    result = jobs.run_sprint_sync(sprint.id)

    assert result == {"status": "completed"}
    attempts = list(
        db_session.execute(
            select(JobAttempt).where(JobAttempt.sprint_id == sprint.id).order_by(JobAttempt.attempt)
        ).scalars()
    )
    assert [attempt.attempt for attempt in attempts] == [1, 2]
    assert attempts[-1].status == "succeeded"


def test_transient_classifier_does_not_retry_logic_errors() -> None:
    assert jobs.is_transient_error("ConnectionError: connection reset by peer")
    assert jobs.is_transient_error("HTTP 503 service unavailable")
    assert not jobs.is_transient_error("ValueError: illegal strategy transition")
    assert not jobs.is_transient_error(None)
