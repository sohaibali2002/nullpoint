"""Tool nodes available to agents, with whitelists, audit logging and DB-backed trial counting.

Agents never touch the ORM or the engine directly: every action goes through ``ToolContext.invoke``,
which enforces the agent's tool whitelist, times the call, and writes a replayable audit row.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from api.config import Settings
from api.models import (
    DataAsset,
    Idea,
    Memory,
    Strategy,
    TrialCounter,
    new_id,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agents.bus import BudgetGuard, MessageBus
from agents.embeddings import embed, is_duplicate
from agents.llm import LLMClient, build_llm
from agents.tearsheet import render_tearsheet
from data.lake import DataLake
from data.pipeline import Dataset, load_universe
from engine.base import BacktestResult, EngineAdapter, GridResult, get_adapter
from engine.portfolio import construct, portfolio_metrics
from engine.strategy import StrategySpec
from engine.validation import ValidationReport, validate_strategy
from sandbox.scanner import scan_code as run_scan


class DBTrialCounter:
    """Org-scoped global trial counter. This is the only honest source of N."""

    def __init__(self, session: Session, org_id: str, scope: str = "global") -> None:
        self.session = session
        self.org_id = org_id
        self.scope = scope

    @property
    def value(self) -> int:
        count = self.session.execute(
            select(TrialCounter.count).where(
                TrialCounter.org_id == self.org_id, TrialCounter.scope == self.scope
            )
        ).scalar_one_or_none()
        return int(count or 0)

    def increment(self, n: int = 1) -> int:
        if n <= 0:
            return self.value
        row = self.session.execute(
            select(TrialCounter).where(
                TrialCounter.org_id == self.org_id, TrialCounter.scope == self.scope
            )
        ).scalar_one_or_none()
        if row is None:
            row = TrialCounter(id=new_id(), org_id=self.org_id, scope=self.scope, count=int(n))
            self.session.add(row)
        else:
            row.count = int(row.count) + int(n)
        self.session.commit()
        return int(row.count)


@dataclass(frozen=True)
class AgentSpec:
    key: str
    name: str
    model_key: str
    purpose: str
    system_prompt: str
    tools: tuple[str, ...]
    budget_share: float = 0.1


class ToolContext:
    TOOL_NAMES = (
        "load_data",
        "scan_code",
        "grid",
        "validate",
        "risk_report",
        "build_portfolio",
        "tearsheet",
        "record_idea",
        "list_ideas",
        "remember",
        "recall",
    )

    def __init__(
        self,
        session: Session,
        org_id: str,
        sprint_id: str,
        *,
        settings: Settings,
        bus: MessageBus,
        budget: BudgetGuard,
        lake: DataLake | None = None,
        adapter: EngineAdapter | None = None,
        allow_network: bool = True,
    ) -> None:
        self.session = session
        self.org_id = org_id
        self.sprint_id = sprint_id
        self.settings = settings
        self.bus = bus
        self.budget = budget
        self.lake = lake or DataLake(settings.lake_root)
        self.adapter = adapter or get_adapter("native")
        self.allow_network = allow_network
        self.trial_counter = DBTrialCounter(session, org_id)
        self._dataset: Dataset | None = None
        self._llms: dict[str, LLMClient] = {}

    # ------------------------------------------------------------- plumbing
    def llm(self, model_key: str) -> LLMClient:
        if model_key not in self._llms:
            model = {
                "orchestrator": self.settings.model_orchestrator,
                "worker": self.settings.model_worker,
                "cheap": self.settings.model_cheap,
                "skeptic": self.settings.model_skeptic,
            }.get(model_key, self.settings.model_worker)
            self._llms[model_key] = build_llm(model, self.settings.llm_provider, self.settings.anthropic_key)
        return self._llms[model_key]

    def narrate(self, agent: AgentSpec, prompt: str) -> str:
        client = self.llm(agent.model_key)
        response = client.complete(
            system=agent.system_prompt,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
        )
        self.budget.spend(response.cost_usd, response.tokens_in + response.tokens_out)
        return response.text.strip()

    def invoke(self, agent: AgentSpec, tool: str, /, **kwargs: Any) -> Any:
        if tool not in agent.tools:
            raise PermissionError(f"agent {agent.key!r} is not allowed to call {tool!r}")
        started = time.perf_counter()
        result = getattr(self, tool)(**kwargs)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        self.bus.tool(
            agent.key,
            tool,
            {"kwargs": _safe_kwargs(kwargs), "elapsed_ms": elapsed_ms},
            result,
        )
        return result

    # ---------------------------------------------------------------- tools
    def load_data(self, symbols: list[str], start: str, end: str) -> Dataset:
        if self._dataset is not None and set(symbols).issubset(set(self._dataset.data.symbols)):
            return self._dataset
        dataset = load_universe(
            symbols,
            start=start,
            end=end,
            lake=self.lake,
            allow_network=self.allow_network,
            benchmark=symbols[0] if symbols else None,
        )
        self._dataset = dataset
        for entry in dataset.quality:
            self.session.add(
                DataAsset(
                    org_id=self.org_id,
                    provider=entry.get("provider") or dataset.provider,
                    symbol=entry.get("symbol") or ",".join(dataset.data.symbols),
                    start=entry.get("start") or "",
                    end=entry.get("end") or "",
                    rows=int(entry.get("rows") or 0),
                    content_hash=dataset.content_hash,
                    path=",".join(dataset.lake_paths)[:500],
                    quality=entry,
                    synthetic=dataset.synthetic,
                )
            )
        self.session.commit()
        return dataset

    def scan_code(self, code: str) -> dict[str, Any]:
        report = run_scan(code)
        return {"ok": True, "report": report.as_dict()}

    def grid(self, spec: StrategySpec, data: Any, grid: dict[str, list[Any]]) -> GridResult:
        return self.adapter.run_grid(
            spec,
            grid,
            data,
            objective="sharpe",
            trial_counter=self.trial_counter,
            min_trades=3,
        )

    def validate(
        self, spec: StrategySpec, data: Any, grid: dict[str, list[Any]]
    ) -> ValidationReport:
        return validate_strategy(
            spec,
            data,
            adapter=self.adapter,
            grid=grid,
            trial_counter=self.trial_counter,
            folds=3,
            cpcv_groups=4,
            cpcv_k=2,
            embargo=5,
            mc_sims=200,
            seed=7,
        )

    def risk_report(
        self,
        result: BacktestResult,
        candidate_returns: dict[str, pd.Series],
        dollar_volume: pd.Series | None = None,
    ) -> dict[str, Any]:
        metrics = dict(result.metrics)
        correlations: dict[str, float] = {}
        for name, series in candidate_returns.items():
            aligned = pd.concat([result.returns, series], axis=1).dropna()
            if len(aligned) > 30:
                value = aligned.iloc[:, 0].corr(aligned.iloc[:, 1])
                correlations[name] = float(value) if np.isfinite(value) else 0.0
        capacity = None
        if dollar_volume is not None and len(dollar_volume.dropna()) > 0:
            adv_usd = float(dollar_volume.dropna().median())
            gross = max(float(result.metrics.get("exposure", 1.0)), 1e-6)
            capacity = float(adv_usd * 0.01 / gross) if np.isfinite(adv_usd) else None
        return {
            "metrics": {
                key: metrics.get(key)
                for key in (
                    "max_drawdown",
                    "max_drawdown_duration",
                    "cvar_95",
                    "var_95",
                    "tail_ratio",
                    "exposure",
                    "turnover_annual",
                    "skew",
                )
            },
            "correlations": correlations,
            "max_correlation": max(correlations.values()) if correlations else 0.0,
            "capacity_usd_estimate": capacity,
            "capacity_note": "1% of average dollar volume"
            if capacity is not None
            else "volume unavailable for this dataset",
            "concentration": float(abs(result.positions).max().max()) if not result.positions.empty else 0.0,
        }

    def build_portfolio(
        self, returns: pd.DataFrame, method: str = "hrp", max_weight: float = 0.4
    ) -> dict[str, Any]:
        weights = construct(returns, method=method, max_weight=max_weight)
        metrics = portfolio_metrics(weights, returns)
        return {"method": method, "weights": weights, "metrics": metrics}

    def tearsheet(
        self,
        strategy: Strategy,
        spec: StrategySpec,
        result: BacktestResult,
        report: ValidationReport,
        dataset_meta: dict[str, Any],
    ) -> dict[str, Any]:
        return render_tearsheet(
            strategy_name=strategy.name,
            strategy_id=strategy.id,
            status=strategy.status,
            spec=spec,
            result=result,
            report=report,
            dataset_meta=dataset_meta,
            out_dir=Path("artifacts/tearsheets"),
        )

    def record_idea(
        self,
        title: str,
        hypothesis: str,
        family: str,
        tags: list[str],
        template_key: str,
    ) -> dict[str, Any]:
        fingerprint = hashlib.sha256(
            f"{template_key}|{title}|{hypothesis}".lower().encode()
        ).hexdigest()[:32]
        existing = self.session.execute(select(Idea).where(Idea.org_id == self.org_id)).scalars().all()
        for row in existing:
            if row.fingerprint == fingerprint:
                return {"created": False, "duplicate_of": row.id, "similarity": 1.0, "reason": "exact"}
        duplicate, matched, score = is_duplicate(
            f"{title} {hypothesis}",
            [(row.id, row.embedding) for row in existing if row.embedding],
        )
        if duplicate and matched is not None:
            return {"created": False, "duplicate_of": matched, "similarity": round(score, 3), "reason": "near"}
        idea = Idea(
            org_id=self.org_id,
            title=title[:200],
            hypothesis=hypothesis,
            family=family,
            tags=tags,
            embedding=embed(f"{title} {hypothesis}"),
            fingerprint=fingerprint,
            status="new",
            origin="agent",
        )
        self.session.add(idea)
        self.session.commit()
        return {"created": True, "idea_id": idea.id, "fingerprint": fingerprint, "similarity": round(score, 3)}

    def list_ideas(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = (
            self.session.execute(
                select(Idea).where(Idea.org_id == self.org_id).order_by(Idea.created_at.desc()).limit(limit)
            )
            .scalars()
            .all()
        )
        return [
            {"id": row.id, "title": row.title, "family": row.family, "hypothesis": row.hypothesis}
            for row in rows
        ]

    def remember(
        self,
        title: str,
        body: str,
        *,
        kind: str = "lesson",
        strategy_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        memory = Memory(
            org_id=self.org_id,
            sprint_id=self.sprint_id,
            strategy_id=strategy_id,
            kind=kind,
            title=title[:200],
            body=body,
            embedding=embed(f"{title} {body}"),
            payload=payload or {},
        )
        self.session.add(memory)
        self.session.commit()
        return {"memory_id": memory.id, "kind": kind}

    def recall(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        rows = (
            self.session.execute(
                select(Memory).where(Memory.org_id == self.org_id).order_by(Memory.created_at.desc()).limit(200)
            )
            .scalars()
            .all()
        )
        vector = embed(query)
        scored: list[tuple[float, Memory]] = []
        for row in rows:
            if not row.embedding:
                continue
            score = float(np.dot(vector, row.embedding))
            scored.append((score, row))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            {"id": row.id, "title": row.title, "body": row.body, "kind": row.kind, "score": round(score, 3)}
            for score, row in scored[:limit]
        ]

    # ------------------------------------------------------------ accounting
    def trial_delta(self) -> int:
        start = self.budget.snapshot()["trials"]
        return max(0, self.trial_counter.value - start)

    def sprint_trials(self) -> int:
        return self.trial_counter.value


def _safe_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in kwargs.items():
        if isinstance(value, (str, int, float, bool, type(None))):
            safe[key] = value[:2000] if isinstance(value, str) else value
        elif isinstance(value, (list, tuple)):
            safe[key] = [v for v in value if isinstance(v, (str, int, float, bool))][:20]
        elif hasattr(value, "model_dump"):
            safe[key] = f"<{type(value).__name__}>"
        elif hasattr(value, "symbols"):
            safe[key] = value.symbols
        elif hasattr(value, "metadata") and isinstance(value.metadata, dict):
            safe[key] = value.metadata
        else:
            safe[key] = type(value).__name__
    return safe


def org_trial_total(session: Session, org_id: str) -> int:
    count = session.execute(
        select(func.coalesce(func.sum(TrialCounter.count), 0)).where(TrialCounter.org_id == org_id)
    ).scalar_one()
    return int(count or 0)
