from __future__ import annotations

import pandas as pd
from api.models import (
    HoldoutEvaluation,
    Organization,
    PaperRun,
    Strategy,
    ValidationReport,
    new_id,
)
from api.services.governance import claim_status


def _strategy(session, status: str = "candidate") -> Strategy:
    org = Organization(name="Gate Test", slug=f"gate-{new_id()[:8]}")
    session.add(org)
    session.commit()
    strategy = Strategy(
        org_id=org.id,
        name="Gated candidate",
        status=status,
        spec={"name": "Gated candidate", "hypothesis": "test"},
        metrics={"sharpe": 1.4, "cagr": 0.12},
    )
    session.add(strategy)
    session.commit()
    return strategy


def _validation(session, strategy: Strategy, passed: bool) -> None:
    session.add(
        ValidationReport(
            org_id=strategy.org_id,
            strategy_id=strategy.id,
            passed=passed,
            score=0.9 if passed else 0.2,
            recommendation="validated" if passed else "reject",
            payload={"gates": [{"name": "pbo", "passed": passed}]},
        )
    )
    session.commit()


def test_claim_is_blocked_until_validation_holdout_and_paper_exist(db_session) -> None:
    strategy = _strategy(db_session)
    initial = claim_status(db_session, strategy)
    assert initial["allowed"] is False
    assert initial["metrics"] is None
    assert "no validation report exists" in initial["blockers"]
    assert "sealed holdout has not been opened by a human" in initial["blockers"]

    _validation(db_session, strategy, passed=False)
    failed = claim_status(db_session, strategy)
    assert failed["allowed"] is False
    assert any("validation did not pass" in blocker for blocker in failed["blockers"])

    _validation(db_session, strategy, passed=True)
    db_session.add(
        HoldoutEvaluation(
            org_id=strategy.org_id,
            strategy_id=strategy.id,
            strategy_version=strategy.version,
            opened_by="human.quant@example.com",
            reason="approved final review",
            metrics={"sharpe": 0.8},
        )
    )
    db_session.commit()
    no_paper = claim_status(db_session, strategy)
    assert no_paper["allowed"] is False
    assert no_paper["requirements"]["holdout_opened"] is True
    assert "no paper run exists" in no_paper["blockers"]

    dates = [str(day.date()) for day in pd.bdate_range("2026-01-05", periods=30)]
    run = PaperRun(
        org_id=strategy.org_id,
        strategy_id=strategy.id,
        broker="simulator",
        status="running",
        initial_equity=100_000,
        equity_curve=[{"date": day, "equity": 100_000 + index * 10} for index, day in enumerate(dates)],
        drift={"alert": False},
    )
    db_session.add(run)
    db_session.commit()

    allowed = claim_status(db_session, strategy)
    assert allowed["allowed"] is True, allowed["blockers"]
    assert allowed["metrics"]["sharpe"] == 1.4
    assert allowed["paper"]["synthetic"] is True
    assert allowed["requirements"]["paper_days"] == 30


def test_rejected_strategy_can_never_claim(db_session) -> None:
    strategy = _strategy(db_session, status="rejected")
    _validation(db_session, strategy, passed=True)
    db_session.add(
        HoldoutEvaluation(
            org_id=strategy.org_id,
            strategy_id=strategy.id,
            strategy_version=strategy.version,
            opened_by="human",
            metrics={"sharpe": 1.0},
        )
    )
    db_session.commit()
    result = claim_status(db_session, strategy)
    assert result["allowed"] is False
    assert any("not a live research candidate" in blocker for blocker in result["blockers"])
