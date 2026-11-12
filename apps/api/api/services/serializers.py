"""Serialization helpers shared by routers and jobs (keeps API payloads consistent)."""

from __future__ import annotations

from typing import Any

from api.models import Experiment, Idea, Sprint, Strategy, ValidationReport
from engine.base import BacktestResult

DISCLAIMER = "Research only. Not investment advice. Backtests are hypotheses, not forecasts."


def sprint_dict(sprint: Sprint) -> dict[str, Any]:
    return {
        "id": sprint.id,
        "objective": sprint.objective,
        "universe": sprint.universe or [],
        "status": sprint.status,
        "budget_usd": sprint.budget_usd,
        "cost_usd": sprint.cost_usd,
        "tokens_used": sprint.tokens_used,
        "trials_used": sprint.trials_used,
        "strategies_found": sprint.strategies_found,
        "kill_requested": sprint.kill_requested,
        "result": sprint.result or {},
        "error": sprint.error,
        "created_at": sprint.created_at.isoformat() if sprint.created_at else None,
        "started_at": sprint.started_at.isoformat() if sprint.started_at else None,
        "ended_at": sprint.ended_at.isoformat() if sprint.ended_at else None,
        "disclaimer": DISCLAIMER,
    }


def strategy_dict(strategy: Strategy, *, include_spec: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": strategy.id,
        "name": strategy.name,
        "version": strategy.version,
        "status": strategy.status,
        "idea_id": strategy.idea_id,
        "sprint_id": strategy.sprint_id,
        "parent_id": strategy.parent_id,
        "tags": strategy.tags or [],
        "metrics": strategy.metrics or {},
        "trial_index": strategy.trial_index,
        "hypothesis": (strategy.spec or {}).get("hypothesis"),
        "universe": (strategy.spec or {}).get("universe"),
        "created_by": strategy.created_by,
        "created_at": strategy.created_at.isoformat() if strategy.created_at else None,
        "updated_at": strategy.updated_at.isoformat() if strategy.updated_at else None,
        "disclaimer": DISCLAIMER,
    }
    if include_spec:
        payload["spec"] = strategy.spec
        payload["code"] = strategy.code
        payload["code_hash"] = strategy.code_hash
    return payload


def experiment_dict(exp: Experiment, *, include_series: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": exp.id,
        "strategy_id": exp.strategy_id,
        "sprint_id": exp.sprint_id,
        "kind": exp.kind,
        "engine": exp.engine,
        "params": exp.params,
        "metrics": exp.metrics,
        "data_hash": exp.data_hash,
        "code_hash": exp.code_hash,
        "seed": exp.seed,
        "trial_index": exp.trial_index,
        "is_oos": exp.is_oos,
        "split": exp.split,
        "env": exp.env,
        "created_at": exp.created_at.isoformat() if exp.created_at else None,
    }
    if include_series:
        payload.update({"dates": exp.dates, "returns": exp.returns, "equity": exp.equity_curve})
    return payload


def validation_dict(report: ValidationReport) -> dict[str, Any]:
    return {
        "id": report.id,
        "strategy_id": report.strategy_id,
        "experiment_id": report.experiment_id,
        "passed": report.passed,
        "score": report.score,
        "recommendation": report.recommendation,
        "payload": report.payload,
        "created_at": report.created_at.isoformat() if report.created_at else None,
        "disclaimer": DISCLAIMER,
    }


def idea_dict(idea: Idea) -> dict[str, Any]:
    return {
        "id": idea.id,
        "title": idea.title,
        "hypothesis": idea.hypothesis,
        "family": idea.family,
        "tags": idea.tags or [],
        "status": idea.status,
        "origin": idea.origin,
        "dedupe_of": idea.dedupe_of,
        "dedupe_score": idea.dedupe_score,
        "created_at": idea.created_at.isoformat() if idea.created_at else None,
    }


def result_dict(result: BacktestResult, include_series: bool = False) -> dict[str, Any]:
    return result.to_dict(include_series=include_series)
