from __future__ import annotations

import pytest

from agents.artifacts import (
    CritiqueArtifact,
    CritiqueVerdict,
    DuplicateHypothesis,
    FunnelStage,
    HypothesisArtifact,
    IllegalStage,
    QualityDiversityArchive,
    TrialBudgetExceeded,
    TrialFunnel,
    TrialPlanArtifact,
)


def _hypothesis(**overrides) -> HypothesisArtifact:
    payload = {
        "hypothesis_id": "h-momentum-1",
        "title": "Momentum persists",
        "family": "cross_sectional",
        "mechanism": "Underreaction to news creates multi-week persistence.",
        "prediction": "Top-decile momentum outperforms the median by 30bp/month.",
        "falsifier": "Reject if walk-forward Sharpe <= 0 or PBO > 0.35.",
        "universe": ["spy", "qqq", "xlk"],
        "parameters": {"lookback": 20},
    }
    payload.update(overrides)
    return HypothesisArtifact(**payload)


def _critique(hypothesis_id: str = "h-momentum-1", verdict=CritiqueVerdict.PASS) -> CritiqueArtifact:
    return CritiqueArtifact(
        hypothesis_id=hypothesis_id,
        verdict=verdict,
        threats=["momentum crash risk"],
        required_changes=[],
    )


def _plan(hypothesis_id: str = "h-momentum-1", max_trials: int = 2) -> TrialPlanArtifact:
    return TrialPlanArtifact(
        hypothesis_id=hypothesis_id,
        tests=["walk-forward", "cost stress"],
        success_criteria="Sharpe > 0.5 net of 3x costs",
        failure_criteria="PBO > 0.35",
        max_trials=max_trials,
    )


def test_fingerprint_is_stable_and_freezes_parameters() -> None:
    first = _hypothesis()
    second = _hypothesis(universe=["XLK", "SPY", "QQQ"])
    assert first.fingerprint() == second.fingerprint()
    changed = _hypothesis(parameters={"lookback": 40})
    assert changed.fingerprint() != first.fingerprint()


def test_duplicate_and_killed_hypotheses_are_refused() -> None:
    funnel = TrialFunnel()
    funnel.pre_register(_hypothesis(), _critique(), _plan())
    with pytest.raises(DuplicateHypothesis):
        funnel.pre_register(_hypothesis(), _critique(), _plan())
    with pytest.raises(IllegalStage):
        TrialFunnel().pre_register(_hypothesis(), _critique(verdict=CritiqueVerdict.KILL), _plan())


def test_trial_budget_and_stage_order_are_enforced() -> None:
    funnel = TrialFunnel(max_total_trials=3)
    funnel.pre_register(_hypothesis(), _critique(), _plan(max_trials=2))
    funnel.record_trial("h-momentum-1", params={"lookback": 20})
    funnel.record_trial("h-momentum-1", params={"lookback": 40})
    with pytest.raises(TrialBudgetExceeded):
        funnel.record_trial("h-momentum-1")
    with pytest.raises(IllegalStage):
        funnel.advance("h-momentum-1", FunnelStage.RELEASED)
    funnel.advance("h-momentum-1", FunnelStage.FALSIFIED, reason="PBO failed")
    with pytest.raises(IllegalStage):
        funnel.record_trial("h-momentum-1")


def test_quality_diversity_archive_keeps_best_per_cell() -> None:
    archive = QualityDiversityArchive(resolution=0.5)
    assert archive.add("a", (0.01, 0.01), 1.0, FunnelStage.IMPLEMENTED)
    assert not archive.add("b", (0.02, 0.02), 0.5, FunnelStage.IMPLEMENTED)
    assert archive.add("c", (0.02, 0.02), 1.5, FunnelStage.PORTFOLIO_FIT)
    best = archive.best()
    assert len(best) == 1
    assert best[0]["hypothesis_id"] == "c"
