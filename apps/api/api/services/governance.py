"""Governance gates that sit after validation and before any performance claim.

A passing backtest is not a result.  Three human-visible facts must exist first:

1. the latest validation report passed;
2. a human opened the sealed holdout exactly once and its metrics were recorded;
3. a paper run has accumulated the configured minimum number of forward days without a drift alert.

This module deliberately returns blockers instead of raising so the UI can explain what is missing.
It never exposes headline metrics until every blocker is cleared.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from api.config import settings
from api.models import (
    HoldoutEvaluation,
    PaperRun,
    Strategy,
    StrategyStatus,
    ValidationReport,
)


def latest_validation(session: Session, strategy_id: str) -> ValidationReport | None:
    return session.execute(
        select(ValidationReport)
        .where(ValidationReport.strategy_id == strategy_id)
        .order_by(ValidationReport.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def holdout_for(session: Session, strategy: Strategy) -> HoldoutEvaluation | None:
    return session.execute(
        select(HoldoutEvaluation).where(
            HoldoutEvaluation.strategy_id == strategy.id,
            HoldoutEvaluation.strategy_version == strategy.version,
        )
    ).scalar_one_or_none()


def holdout_dict(row: HoldoutEvaluation | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "strategy_version": row.strategy_version,
        "opened_by": row.opened_by,
        "reason": row.reason,
        "report_hash": row.report_hash,
        "metrics": row.metrics or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def paper_days(run: PaperRun | None) -> int:
    if run is None:
        return 0
    return len({str(row.get("date")) for row in (run.equity_curve or []) if row.get("date")})


def latest_paper_run(session: Session, strategy_id: str) -> PaperRun | None:
    return session.execute(
        select(PaperRun)
        .where(PaperRun.strategy_id == strategy_id)
        .order_by(PaperRun.started_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def claim_status(session: Session, strategy: Strategy) -> dict[str, Any]:
    """Return the final claim gate. Headline metrics appear only when ``allowed`` is true."""
    blockers: list[str] = []
    if strategy.status in {StrategyStatus.REJECTED.value, StrategyStatus.ARCHIVED.value}:
        blockers.append(f"strategy is {strategy.status}, not a live research candidate")

    report = latest_validation(session, strategy.id)
    if report is None:
        blockers.append("no validation report exists")
    elif not report.passed:
        failed = [
            gate.get("name", "unknown")
            for gate in (report.payload or {}).get("gates", [])
            if not gate.get("passed", False)
        ]
        blockers.append("validation did not pass" + (f" (failed: {', '.join(failed)})" if failed else ""))

    holdout = holdout_for(session, strategy)
    if holdout is None:
        blockers.append("sealed holdout has not been opened by a human")
    elif not (holdout.metrics or {}):
        blockers.append("holdout was opened but no metrics were recorded")

    paper = latest_paper_run(session, strategy.id)
    days = paper_days(paper)
    if paper is None:
        blockers.append("no paper run exists")
    elif paper.status not in {"running", "stopped"}:
        blockers.append(f"paper run is {paper.status}")
    elif days < settings.min_paper_days:
        blockers.append(f"paper run has {days} distinct days; {settings.min_paper_days} required")
    drift = (paper.drift or {}) if paper else {}
    if drift.get("alert"):
        blockers.append("paper drift alert is active")

    allowed = not blockers
    return {
        "strategy_id": strategy.id,
        "strategy_status": strategy.status,
        "allowed": allowed,
        "blockers": blockers,
        "requirements": {
            "validation_passed": bool(report and report.passed),
            "holdout_opened": holdout is not None,
            "holdout_metrics_recorded": bool(holdout and holdout.metrics),
            "paper_days": days,
            "paper_days_required": settings.min_paper_days,
            "drift_alert": bool(drift.get("alert")),
        },
        "holdout": holdout_dict(holdout),
        "paper": {
            "run_id": paper.id if paper else None,
            "broker": paper.broker if paper else None,
            "status": paper.status if paper else None,
            "synthetic": True if paper and paper.broker == "simulator" else None,
            "days": days,
            "drift": drift,
        }
        if paper
        else None,
        # Withhold headline metrics until every gate is clear.
        "metrics": (strategy.metrics or {}) if allowed else None,
        "generated_at": datetime.now(UTC).isoformat(),
        "disclaimer": (
            "Research only. Paper results from the simulator are synthetic and are not observed "
            "market performance. Not investment advice."
        ),
    }


__all__ = [
    "claim_status",
    "holdout_dict",
    "holdout_for",
    "latest_paper_run",
    "latest_validation",
    "paper_days",
]
