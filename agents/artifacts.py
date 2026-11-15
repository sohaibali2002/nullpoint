"""Typed research artifacts and the pre-registration funnel.

The old pipeline passed free-form dictionaries between agents.  That makes it impossible to prove
what was predicted before a result was seen, or to count trials honestly.  These models are the
contract for the next-stage loop:

proposal → critique → pre-registration → implementation → falsification → portfolio fit → release

The funnel is deliberately mechanical: a hypothesis cannot skip a stage, cannot be pre-registered
twice, and cannot spend more trials than its registered budget.  The quality-diversity archive keeps
one best result per behaviour cell so the search does not collapse onto one crowded idea.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FunnelStage(StrEnum):
    PROPOSED = "proposed"
    CRITIQUED = "critiqued"
    PRE_REGISTERED = "pre_registered"
    IMPLEMENTED = "implemented"
    FALSIFIED = "falsified"
    PORTFOLIO_FIT = "portfolio_fit"
    RELEASED = "released"


_ALLOWED: dict[FunnelStage, set[FunnelStage]] = {
    FunnelStage.PROPOSED: {FunnelStage.CRITIQUED, FunnelStage.FALSIFIED},
    FunnelStage.CRITIQUED: {FunnelStage.PRE_REGISTERED, FunnelStage.FALSIFIED},
    FunnelStage.PRE_REGISTERED: {FunnelStage.IMPLEMENTED, FunnelStage.FALSIFIED},
    FunnelStage.IMPLEMENTED: {FunnelStage.FALSIFIED, FunnelStage.PORTFOLIO_FIT},
    FunnelStage.PORTFOLIO_FIT: {FunnelStage.FALSIFIED, FunnelStage.RELEASED},
    FunnelStage.FALSIFIED: set(),
    FunnelStage.RELEASED: set(),
}


def canonical_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


class HypothesisArtifact(BaseModel):
    """A falsifiable claim, frozen before any code or backtest is run."""

    model_config = ConfigDict(extra="forbid")

    hypothesis_id: str = Field(min_length=6, max_length=80)
    title: str = Field(min_length=4, max_length=200)
    family: str = Field(min_length=2, max_length=60)
    mechanism: str = Field(min_length=10, max_length=2000)
    prediction: str = Field(min_length=10, max_length=2000)
    falsifier: str = Field(min_length=10, max_length=2000)
    universe: list[str] = Field(min_length=1, max_length=40)
    parameters: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("universe")
    @classmethod
    def _upper(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip().upper() for item in value if item.strip()]
        if not cleaned:
            raise ValueError("universe cannot be empty")
        return list(dict.fromkeys(cleaned))

    def fingerprint(self) -> str:
        return canonical_hash(
            {
                "title": self.title,
                "family": self.family,
                "mechanism": self.mechanism,
                "prediction": self.prediction,
                "falsifier": self.falsifier,
                "universe": sorted(self.universe),
                "parameters": self.parameters,
            }
        )


class CritiqueVerdict(StrEnum):
    PASS = "pass"
    REVISE = "revise"
    KILL = "kill"


class CritiqueArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hypothesis_id: str
    verdict: CritiqueVerdict
    threats: list[str] = Field(default_factory=list, max_length=20)
    required_changes: list[str] = Field(default_factory=list, max_length=20)
    reviewer: str = "skeptic"

    def permits_registration(self) -> bool:
        return self.verdict != CritiqueVerdict.KILL


class TrialPlanArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hypothesis_id: str
    tests: list[str] = Field(min_length=1, max_length=20)
    success_criteria: str = Field(min_length=5, max_length=1000)
    failure_criteria: str = Field(min_length=5, max_length=1000)
    max_trials: int = Field(default=24, ge=1, le=1000)
    grid_hash: str = ""

    def fingerprint(self) -> str:
        return canonical_hash(self.model_dump(mode="json"))


class TrialReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hypothesis_id: str
    trial_index: int
    stage: FunnelStage
    params: dict[str, Any] = Field(default_factory=dict)
    score: float | None = None
    passed: bool | None = None
    reason: str = ""


class FunnelRecord(BaseModel):
    hypothesis: HypothesisArtifact
    critique: CritiqueArtifact
    plan: TrialPlanArtifact
    stage: FunnelStage = FunnelStage.PRE_REGISTERED
    fingerprint: str
    trial_indices: list[int] = Field(default_factory=list)
    receipts: list[TrialReceipt] = Field(default_factory=list)


class DuplicateHypothesis(ValueError):
    """The same frozen claim was registered twice."""


class TrialBudgetExceeded(RuntimeError):
    """A hypothesis or the desk-wide trial cap was exceeded."""


class IllegalStage(ValueError):
    """A hypothesis tried to skip a funnel stage."""


@dataclass
class ArchiveEntry:
    hypothesis_id: str
    behavior: tuple[float, ...]
    score: float
    stage: FunnelStage


@dataclass
class QualityDiversityArchive:
    """One best result per discretised behaviour cell (MAP-Elites style)."""

    resolution: float = 1.0
    cells: dict[tuple[int, ...], ArchiveEntry] = field(default_factory=dict)

    def _cell(self, behavior: tuple[float, ...]) -> tuple[int, ...]:
        return tuple(round(value / self.resolution) for value in behavior)

    def add(self, hypothesis_id: str, behavior: tuple[float, ...], score: float, stage: FunnelStage) -> bool:
        if not behavior or not all(abs(value) < 1e6 for value in behavior):
            return False
        cell = self._cell(behavior)
        current = self.cells.get(cell)
        if current is not None and current.score >= score:
            return False
        self.cells[cell] = ArchiveEntry(hypothesis_id, behavior, float(score), stage)
        return True

    def best(self) -> list[dict[str, Any]]:
        return [
            {
                "hypothesis_id": entry.hypothesis_id,
                "behavior": list(entry.behavior),
                "score": entry.score,
                "stage": entry.stage.value,
            }
            for entry in sorted(self.cells.values(), key=lambda item: item.score, reverse=True)
        ]


class TrialFunnel:
    def __init__(self, *, max_total_trials: int = 100_000, archive: QualityDiversityArchive | None = None) -> None:
        self.max_total_trials = int(max_total_trials)
        self.archive = archive or QualityDiversityArchive()
        self.records: dict[str, FunnelRecord] = {}
        self._fingerprints: dict[str, str] = {}
        self.total_trials = 0

    def pre_register(
        self, hypothesis: HypothesisArtifact, critique: CritiqueArtifact, plan: TrialPlanArtifact
    ) -> FunnelRecord:
        if hypothesis.hypothesis_id in self.records:
            raise DuplicateHypothesis(f"hypothesis {hypothesis.hypothesis_id!r} already registered")
        if not critique.permits_registration():
            raise IllegalStage("a killed critique cannot be pre-registered")
        if critique.hypothesis_id != hypothesis.hypothesis_id:
            raise ValueError("critique does not belong to this hypothesis")
        if plan.hypothesis_id != hypothesis.hypothesis_id:
            raise ValueError("test plan does not belong to this hypothesis")
        fingerprint = hypothesis.fingerprint()
        if fingerprint in self._fingerprints:
            raise DuplicateHypothesis(
                f"identical claim already registered as {self._fingerprints[fingerprint]!r}"
            )
        record = FunnelRecord(
            hypothesis=hypothesis,
            critique=critique,
            plan=plan,
            fingerprint=fingerprint,
            stage=FunnelStage.PRE_REGISTERED,
        )
        self.records[hypothesis.hypothesis_id] = record
        self._fingerprints[fingerprint] = hypothesis.hypothesis_id
        return record

    def record_trial(
        self,
        hypothesis_id: str,
        *,
        params: dict[str, Any] | None = None,
        score: float | None = None,
        stage: FunnelStage = FunnelStage.IMPLEMENTED,
    ) -> TrialReceipt:
        record = self._record(hypothesis_id)
        if record.stage in {FunnelStage.FALSIFIED, FunnelStage.RELEASED}:
            raise IllegalStage(f"cannot spend trials on a {record.stage.value} hypothesis")
        if len(record.receipts) >= record.plan.max_trials:
            raise TrialBudgetExceeded(
                f"{hypothesis_id!r} exhausted its registered {record.plan.max_trials} trials"
            )
        if self.total_trials >= self.max_total_trials:
            raise TrialBudgetExceeded("desk-wide trial cap reached")
        self.total_trials += 1
        receipt = TrialReceipt(
            hypothesis_id=hypothesis_id,
            trial_index=self.total_trials,
            stage=stage,
            params=params or {},
            score=score,
        )
        record.trial_indices.append(self.total_trials)
        record.receipts.append(receipt)
        return receipt

    def advance(self, hypothesis_id: str, stage: FunnelStage, *, reason: str = "") -> FunnelRecord:
        record = self._record(hypothesis_id)
        if stage not in _ALLOWED[record.stage]:
            raise IllegalStage(f"{record.stage.value} → {stage.value} is not permitted")
        record.stage = stage
        if reason:
            record.receipts.append(
                TrialReceipt(
                    hypothesis_id=hypothesis_id,
                    trial_index=self.total_trials,
                    stage=stage,
                    reason=reason,
                )
            )
        return record

    def _record(self, hypothesis_id: str) -> FunnelRecord:
        try:
            return self.records[hypothesis_id]
        except KeyError as exc:
            raise KeyError(f"unknown hypothesis {hypothesis_id!r}") from exc

    def summary(self) -> dict[str, Any]:
        stages: dict[str, int] = {}
        for record in self.records.values():
            stages[record.stage.value] = stages.get(record.stage.value, 0) + 1
        return {
            "n_hypotheses": len(self.records),
            "stages": stages,
            "total_trials": self.total_trials,
            "archive_cells": len(self.archive.cells),
            "survivors": stages.get(FunnelStage.PORTFOLIO_FIT.value, 0)
            + stages.get(FunnelStage.RELEASED.value, 0),
        }


__all__ = [
    "ArchiveEntry",
    "CritiqueArtifact",
    "CritiqueVerdict",
    "DuplicateHypothesis",
    "FunnelRecord",
    "FunnelStage",
    "HypothesisArtifact",
    "IllegalStage",
    "QualityDiversityArchive",
    "TrialBudgetExceeded",
    "TrialFunnel",
    "TrialPlanArtifact",
    "TrialReceipt",
    "canonical_hash",
]
