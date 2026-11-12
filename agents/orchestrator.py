"""Autonomous research sprint: plan → ideate → code → backtest → falsify → rank.

The runner owns the state machine and the safety gates:
* a sealed holdout tail is never touched by agents (only a human-triggered final evaluation may),
* a strategy reaches CANDIDATE at most; PAPER needs an explicit approval record,
* every trial increments the org-wide counter used by the deflated Sharpe ratio,
* budget, wall-clock and kill checks run between every agent step.
"""

from __future__ import annotations

import hashlib
import traceback
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from agents.bus import BudgetExceeded, BudgetGuard, MessageBus
from agents.roster import (
    AGENT_SPECS,
    BacktesterAgent,
    DataEngineerAgent,
    IdeatorAgent,
    LibrarianAgent,
    OrchestratorAgent,
    PortfolioConstructorAgent,
    ReporterAgent,
    RiskAnalystAgent,
    SkepticAgent,
    StrategyCoderAgent,
)
from agents.tools import ToolContext
from api.config import Settings, settings as default_settings
from api.models import (
    ALLOWED_TRANSITIONS,
    Experiment,
    Portfolio,
    Sprint,
    Strategy,
    StrategyStatus,
    ValidationReport as ValidationReportRow,
    utcnow,
)
from engine.base import BacktestResult, EngineAdapter
from engine.validation import ValidationReport as EngineValidationReport, sealed_holdout_split
from agents.templates import get_template
from sandbox.scanner import ScanError

DISCLAIMER = "Research only. Not investment advice. Backtests are hypotheses, not forecasts."


class SprintRunner:
    def __init__(
        self,
        session: Session,
        sprint_id: str,
        *,
        settings: Settings | None = None,
        adapter: EngineAdapter | None = None,
        allow_network: bool = True,
        alert: Callable[[str, str, str], None] | None = None,
        sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.session = session
        self.sprint_id = sprint_id
        self.settings = settings or default_settings
        self.adapter = adapter
        self.allow_network = allow_network
        self.alert = alert
        self.sink = sink

    # ------------------------------------------------------------------ setup
    def _load_sprint(self, *, refresh: bool = False) -> Sprint:
        sprint = self.session.get(Sprint, self.sprint_id, populate_existing=refresh)
        if sprint is None:
            raise ValueError(f"sprint {self.sprint_id} not found")
        return sprint

    def _alert(self, event: str, subject: str, body: str) -> None:
        if self.alert is None:
            return
        try:
            self.alert(event, subject, body)
        except Exception:  # noqa: BLE001 - alerts must never break a sprint
            pass

    # -------------------------------------------------------------------- run
    def run(self) -> dict[str, Any]:
        sprint = self._load_sprint()
        if sprint.status in {"completed", "failed", "killed", "budget_exhausted"}:
            return sprint.result or {"status": sprint.status, "skipped": True}

        started = utcnow()
        sprint.status = "running"
        sprint.started_at = started
        self.session.commit()

        bus = MessageBus(self.session, sprint.org_id, sprint.id, sink=self.sink)
        trials_start = int(sprint.trials_used or 0)
        budget = BudgetGuard(
            budget_usd=float(sprint.budget_usd or self.settings.sprint_budget_usd),
            wall_clock_minutes=float(self.settings.sprint_wall_clock_minutes),
            max_iterations=int(self.settings.max_agent_iterations),
            spent_usd=float(sprint.cost_usd or 0.0),
            tokens=int(sprint.tokens_used or 0),
            trials=trials_start,
            kill_check=lambda: bool(self.settings.kill_switch) or self._killed(),
        )
        ctx = ToolContext(
            self.session,
            sprint.org_id,
            sprint.id,
            settings=self.settings,
            bus=bus,
            budget=budget,
            adapter=self.adapter,
            allow_network=self.allow_network,
        )
        plan_agent = OrchestratorAgent(ctx)
        ideator = IdeatorAgent(ctx)
        data_engineer = DataEngineerAgent(ctx)
        coder = StrategyCoderAgent(ctx)
        backtester = BacktesterAgent(ctx)
        skeptic = SkepticAgent(ctx)
        risk_analyst = RiskAnalystAgent(ctx)
        portfolio_agent = PortfolioConstructorAgent(ctx)
        reporter = ReporterAgent(ctx)
        librarian = LibrarianAgent(ctx)

        bus.emit(
            "orchestrator",
            f"Sprint started for objective: {sprint.objective}. "
            f"Budget ${budget.budget_usd:.2f}, wall clock {self.settings.sprint_wall_clock_minutes}m, "
            f"LLM provider {self.settings.llm_provider}. {DISCLAIMER}",
            role="system",
            tool_payload={"models": {
                "orchestrator": self.settings.model_orchestrator,
                "worker": self.settings.model_worker,
                "skeptic": self.settings.model_skeptic,
                "cheap": self.settings.model_cheap,
            }},
        )

        candidates: dict[str, dict[str, Any]] = {}
        rejected: list[dict[str, Any]] = []
        tearsheets: list[dict[str, Any]] = []
        dataset_meta: dict[str, Any] = {}
        sealed_meta: dict[str, Any] = {}
        portfolio_result: dict[str, Any] | None = None
        status_override: str | None = None
        error: str | None = None

        try:
            config = sprint.config or {}
            universe = list(sprint.universe or []) or ["SPY", "QQQ", "XLK", "XLF", "XLE", "XLV"]
            start = str(config.get("start") or "2016-01-01")
            end = str(config.get("end") or datetime.now(timezone.utc).date())
            max_strategies = int(config.get("max_strategies") or self.settings.max_strategies_per_sprint)

            plan = plan_agent.plan(sprint.objective, universe, max_strategies)
            sprint.config = {**config, "plan": plan, "sealed_policy": "agents never see the tail"}
            bus.emit(
                "orchestrator",
                f"Plan: {max_strategies} strategies across {', '.join(plan['families'])}. "
                f"Max iterations per strategy {plan['max_iterations']}.",
                tool_payload={"plan": plan},
            )
            self.session.commit()

            dataset = data_engineer.prepare(universe, start, end)
            dataset_meta = dataset.metadata
            total_rows = len(dataset.data.close)
            split = sealed_holdout_split(total_rows, 0.2)
            visible_end = int(split["visible"][1])
            sealed_meta = {
                "visible": [str(dataset.data.index[0].date()), str(dataset.data.index[visible_end - 1].date())],
                "sealed": [str(dataset.data.index[visible_end].date()), str(dataset.data.index[-1].date())],
                "policy": "sealed holdout is not evaluated by agents; requires a human-triggered final check",
            }
            visible = dataset.data.slice(0, visible_end)
            dollar_volume = None
            if dataset.data.volume is not None and not dataset.data.volume.empty:
                dollar_volume = (dataset.data.volume * dataset.data.close).sum(axis=1).reindex(visible.index)

            bus.emit(
                "data_engineer",
                f"Agents see {len(visible.close)} bars ({sealed_meta['visible'][0]} → "
                f"{sealed_meta['visible'][1]}). {total_rows - visible_end} bars are sealed. "
                f"Provider={dataset.provider}, synthetic={dataset.synthetic}.",
                tool_payload={"dataset": dataset_meta, "sealed": sealed_meta},
            )

            proposals = ideator.propose(sprint.objective, max_strategies)
            bus.emit(
                "ideator",
                f"Proposed {len(proposals)} distinct hypotheses: "
                + "; ".join(p["title"] for p in proposals),
                tool_payload={"proposals": proposals},
            )

            for proposal_index, proposal in enumerate(proposals):
                budget.check()
                idea_record = ctx.invoke(ideator.spec, "record_idea",
                                         title=proposal["title"],
                                         hypothesis=proposal["hypothesis"],
                                         family=proposal["family"],
                                         tags=proposal["tags"],
                                         template_key=proposal["template_key"])
                if not idea_record.get("created"):
                    bus.emit(
                        "ideator",
                        f"Skipping duplicate idea ({idea_record.get('reason')}, similarity "
                        f"{idea_record.get('similarity')}).",
                    )
                    continue
                idea_id = idea_record["idea_id"]
                template = get_template(proposal["template_key"])

                strategy: Strategy | None = None
                spec = None
                result: BacktestResult | None = None
                report: EngineValidationReport | None = None
                grid = None
                validated = False
                attempt = 0

                for attempt in range(max(1, int(plan["max_iterations"]))):
                    budget.check()
                    if attempt == 0:
                        spec, _scan = coder.draft(proposal, universe, 1)
                        parent_id = None
                    else:
                        if result is None:
                            break
                        spec = coder.refine(spec, grid, attempt)  # type: ignore[arg-type]
                        parent_id = strategy.id if strategy else None
                    assert spec is not None

                    try:
                        ctx.invoke(coder.spec, "scan_code", code=spec.signal_code)
                    except ScanError as exc:
                        bus.emit("strategy_coder", f"AST scan rejected the strategy: {exc}. Killed.")
                        break

                    strategy = Strategy(
                        org_id=sprint.org_id,
                        idea_id=idea_id,
                        sprint_id=sprint.id,
                        name=f"{spec.name} v{spec.version}",
                        version=spec.version,
                        status=StrategyStatus.IDEA.value,
                        spec=spec.model_dump(mode="json"),
                        code=spec.signal_code,
                        code_hash=spec.code_hash(),
                        parent_id=parent_id,
                        created_by="agent",
                        tags=proposal["tags"],
                        trial_index=ctx.trial_counter.value,
                    )
                    self.session.add(strategy)
                    self.session.commit()

                    grid = backtester.run(spec, visible, template.grid)
                    self.session.commit()
                    self._sync_budget(sprint, budget, ctx)
                    if grid.best is None:
                        bus.emit("backtester", "No grid candidate met the minimum trade count. Rejecting idea.")
                        self._transition(strategy, StrategyStatus.REJECTED)
                        break

                    result = grid.best
                    self._save_experiment(sprint, strategy, "grid", result, visible, ctx,
                                          {"grid": template.grid, "best_params": grid.best_params})
                    strategy.metrics = dict(result.metrics)
                    strategy.trial_index = ctx.trial_counter.value
                    self._transition(strategy, StrategyStatus.BACKTESTED)

                    report = skeptic.review(spec, visible, template.grid)
                    self._save_validation(sprint, strategy, report)
                    self._sync_budget(sprint, budget, ctx)
                    if report.passed:
                        validated = True
                        self._transition(strategy, StrategyStatus.VALIDATED)
                        bus.emit("skeptic", f"Promoting to VALIDATED: {report.recommendation.upper()}.")
                        break
                    bus.emit(
                        "skeptic",
                        f"Rejected attempt {attempt + 1}/{plan['max_iterations']} "
                        f"(score {report.score:.2f}). Iterating with tighter risk and harsher costs.",
                    )
                    self._transition(strategy, StrategyStatus.REJECTED)
                    if attempt + 1 < int(plan["max_iterations"]):
                        continue

                if validated and strategy is not None and result is not None and report is not None and spec is not None:
                    risk = risk_analyst.assess(
                        result,
                        {sid: meta["returns"] for sid, meta in candidates.items()},
                        dollar_volume,
                    )
                    strategy.metrics = {**strategy.metrics, "risk": risk}
                    self._transition(strategy, StrategyStatus.CANDIDATE)
                    strategy.metrics["trial_index"] = ctx.trial_counter.value
                    self.session.commit()

                    tearsheet = reporter.report(
                        strategy,
                        spec,
                        result,
                        report,
                        dataset_meta,
                    )
                    tearsheets.append(tearsheet)
                    strategy.metrics["tearsheet_path"] = tearsheet["path"]
                    self.session.commit()

                    librarian.record(
                        f"Validated: {strategy.name}",
                        f"{spec.hypothesis} Survived {sum(1 for g in report.gates if g.passed)}/"
                        f"{len(report.gates)} gates. Sharpe {result.metrics.get('sharpe', 0):.2f}, "
                        f"PBO {report.sections.get('pbo', {}).get('pbo')}. "
                        f"Risk: max correlation {risk['max_correlation']:.2f}.",
                        kind="result",
                        strategy_id=strategy.id,
                        payload={"report_hash": report.to_dict()["report_hash"], "metrics": result.metrics},
                    )
                    candidates[strategy.id] = {
                        "name": strategy.name,
                        "sharpe": float(result.metrics.get("sharpe", 0.0)),
                        "gates": f"{sum(1 for g in report.gates if g.passed)}/{len(report.gates)}",
                        "returns": result.returns,
                        "tearsheet": tearsheet["path"],
                        "spec": spec.model_dump(mode="json"),
                        "metrics": result.metrics,
                    }
                    sprint.strategies_found = len(candidates)
                    self.session.commit()
                    self._alert(
                        "promotion",
                        f"{strategy.name} reached CANDIDATE",
                        f"Sharpe {result.metrics.get('sharpe', 0):.2f}; "
                        f"human approval required before paper trading.",
                    )
                else:
                    failed_gates = [g.name for g in report.gates if not g.passed] if report else ["no candidate"]
                    rejected_entry = {
                        "idea": proposal["title"],
                        "reason": ", ".join(failed_gates[:5]),
                        "score": report.score if report else 0.0,
                    }
                    rejected.append(rejected_entry)
                    if strategy is not None:
                        if strategy.status not in {StrategyStatus.REJECTED.value, StrategyStatus.ARCHIVED.value}:
                            self._transition(strategy, StrategyStatus.REJECTED)
                        if spec is not None and result is not None and report is not None:
                            # A rejected strategy still gets a tearsheet: negative results are results.
                            rejected_sheet = reporter.report(strategy, spec, result, report, dataset_meta)
                            tearsheets.append(rejected_sheet)
                            strategy.metrics["tearsheet_path"] = rejected_sheet["path"]
                            rejected_entry["tearsheet"] = rejected_sheet["path"]
                            self.session.commit()
                    librarian.record(
                        f"Rejected: {proposal['title']}",
                        f"Hypothesis: {proposal['hypothesis']} Failed gates: {', '.join(failed_gates)}. "
                        f"Trial count {ctx.trial_counter.value}.",
                        kind="lesson",
                        strategy_id=strategy.id if strategy else None,
                        payload={"report_hash": report.to_dict()["report_hash"] if report else None},
                    )
                    self.session.commit()

                self._sync_budget(sprint, budget, ctx)
                self.session.commit()

            # Portfolio across promoted candidates.
            if len(candidates) >= 2:
                returns = pd.DataFrame({sid: meta["returns"] for sid, meta in candidates.items()})
                portfolio_result = portfolio_agent.construct(returns, method="hrp")
                self.session.add(
                    Portfolio(
                        org_id=sprint.org_id,
                        name=f"Sprint {sprint.id[:8]} portfolio",
                        method=portfolio_result["method"],
                        weights=portfolio_result["weights"],
                        metrics=portfolio_result["metrics"],
                    )
                )
                self.session.commit()
            elif candidates:
                bus.emit("portfolio_constructor", "Only one candidate; no portfolio to construct.")
            else:
                bus.emit("portfolio_constructor", "No candidates survived; the portfolio is empty.")

            sprint.status = "completed"
            sprint.ended_at = utcnow()
            sprint.result = {
                "objective": sprint.objective,
                "status": "completed",
                "dataset": dataset_meta,
                "sealed_holdout": sealed_meta,
                "candidates": {
                    sid: {k: v for k, v in meta.items() if k != "returns"}
                    for sid, meta in candidates.items()
                },
                "rejected": rejected,
                "portfolio": portfolio_result,
                "tearsheets": tearsheets,
                "budget": budget.snapshot(),
                "trials": ctx.trial_counter.value,
                "disclaimer": DISCLAIMER,
            }
            self.session.commit()
            bus.emit(
                "orchestrator",
                f"Sprint complete: {len(candidates)} candidate(s), {len(rejected)} rejected. "
                f"Trials {ctx.trial_counter.value}; spend ${budget.snapshot()['spent_usd']:.4f}. "
                f"Candidates still require human approval before paper trading.",
                tool_payload={"result": {k: v for k, v in sprint.result.items() if k != "tearsheets"}},
            )
            self._alert(
                "sprint_completed",
                f"Sprint finished: {len(candidates)} candidate(s)",
                f"Objective: {sprint.objective}. Rejected: {len(rejected)}.",
            )
            return sprint.result

        except BudgetExceeded as exc:
            self.session.rollback()
            status_override = "killed" if self._killed() else "budget_exhausted"
            error = str(exc)
            bus.emit("orchestrator", f"Stopped: {error}. Partial results were preserved.", role="system")
            sprint.status = status_override
            sprint.error = error
            sprint.ended_at = utcnow()
            sprint.result = {
                "status": status_override,
                "error": error,
                "candidates": len(candidates),
                "rejected": rejected,
                "trials": ctx.trial_counter.value,
                "budget": budget.snapshot(),
                "dataset": dataset_meta,
                "sealed_holdout": sealed_meta,
                "disclaimer": DISCLAIMER,
            }
            self.session.commit()
            return sprint.result

        except Exception as exc:  # noqa: BLE001 - surface failures without losing the audit trail
            self.session.rollback()
            error = f"{type(exc).__name__}: {exc}"
            bus.emit(
                "orchestrator",
                f"Sprint failed: {error}",
                role="system",
                tool_payload={"traceback": traceback.format_exc()[-4000:]},
            )
            sprint.status = "failed"
            sprint.error = error
            sprint.ended_at = utcnow()
            sprint.result = {
                "status": "failed",
                "error": error,
                "candidates": len(candidates),
                "rejected": rejected,
                "trials": ctx.trial_counter.value,
                "disclaimer": DISCLAIMER,
            }
            self.session.commit()
            self._alert("sprint_failed", f"Sprint failed: {error}", sprint.objective)
            return sprint.result

    # --------------------------------------------------------------- helpers
    def _killed(self) -> bool:
        sprint = self.session.get(Sprint, self.sprint_id, populate_existing=True)
        return bool(sprint and sprint.kill_requested)

    def _sync_budget(self, sprint: Sprint, budget: BudgetGuard, ctx: ToolContext) -> None:
        snapshot = budget.snapshot()
        sprint.cost_usd = float(snapshot["spent_usd"])
        sprint.tokens_used = int(snapshot["tokens"])
        sprint.trials_used = int(ctx.trial_counter.value)
        budget.set_trials(sprint.trials_used)
        self.session.commit()

    def _transition(self, strategy: Strategy, target: StrategyStatus) -> None:
        current = StrategyStatus(strategy.status)
        if target == current:
            return
        if target not in ALLOWED_TRANSITIONS[current]:
            raise ValueError(f"illegal strategy transition {current.value} → {target.value}")
        strategy.status = target.value
        self.session.commit()

    def _save_experiment(
        self,
        sprint: Sprint,
        strategy: Strategy,
        kind: str,
        result: BacktestResult,
        data: Any,
        ctx: ToolContext,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.session.add(
            Experiment(
                org_id=sprint.org_id,
                sprint_id=sprint.id,
                strategy_id=strategy.id,
                strategy_version=strategy.version,
                kind=kind,
                engine=result.engine,
                params={**(result.params or {}), **(extra or {})},
                metrics=result.metrics,
                equity_curve=[float(v) for v in result.equity.fillna(1.0)][-1000:],
                returns=[float(v) for v in result.returns.fillna(0.0)][-1000:],
                dates=result.dates[-1000:],
                data_hash=data.data_hash(),
                code_hash=strategy.code_hash,
                seed=result.seed,
                env={
                    "python": "3.11+",
                    "engine": result.engine,
                    "spec_version": strategy.version,
                    "cost_bps": strategy.spec.get("costs") if strategy.spec else {},
                },
                trial_index=ctx.trial_counter.value,
                split={"role": "visible"},
            )
        )
        self.session.commit()

    def _save_validation(
        self, sprint: Sprint, strategy: Strategy, report: EngineValidationReport
    ) -> None:
        payload = report.to_dict()
        self.session.add(
            ValidationReportRow(
                org_id=sprint.org_id,
                sprint_id=sprint.id,
                strategy_id=strategy.id,
                passed=report.passed,
                score=report.score,
                recommendation=report.recommendation,
                payload=payload,
            )
        )
        self.session.commit()


def run_sprint(
    session: Session,
    sprint_id: str,
    *,
    settings: Settings | None = None,
    allow_network: bool = True,
    alert: Callable[[str, str, str], None] | None = None,
) -> dict[str, Any]:
    return SprintRunner(
        session, sprint_id, settings=settings, allow_network=allow_network, alert=alert
    ).run()


def spec_fingerprint(spec: Any) -> str:
    return hashlib.sha256(str(spec).encode()).hexdigest()[:16]


__all__ = ["SprintRunner", "run_sprint", "AGENT_SPECS"]
