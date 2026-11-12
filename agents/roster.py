"""The ten Nullpoint agents.

Each agent = a system prompt, a model slot, a tool whitelist and structured I/O. In offline mode the
reasoning text is scripted but every decision is still made by executing the real tools: the roster
is not a mock pipeline, only the natural-language layer is.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from agents.templates import TEMPLATES, StrategyTemplate, get_template
from agents.tools import AgentSpec, ToolContext
from engine.base import GridResult
from engine.strategy import Costs, RiskRules, StrategySpec, make_spec
from engine.validation import ValidationReport

VOICE = (
    "You are part of Nullpoint, a skeptical quantitative research desk. Be precise and calm. "
    "Never promise returns, never call a backtest a forecast, and state uncertainty explicitly. "
    "Prefer saying 'this did not survive' over defending a weak result."
)

ORCHESTRATOR = AgentSpec(
    key="orchestrator",
    name="Orchestrator",
    model_key="orchestrator",
    purpose="Plans research sprints, assigns work, enforces budgets and decides promote/kill.",
    system_prompt=VOICE
    + " You are the research lead. Design a diverse sprint, cap the number of ideas, and kill weak "
    "lines of work early. You never approve a live-trading action.",
    tools=("load_data", "list_ideas", "recall"),
)

IDEATOR = AgentSpec(
    key="ideator",
    name="Ideator",
    model_key="worker",
    purpose="Generates hypotheses from factor, regime, seasonality and cross-asset logic without repeats.",
    system_prompt=VOICE
    + " You propose testable hypotheses, each with an economic mechanism and a falsifiable prediction. "
    "Do not propose ideas already in the registry or lessons the librarian says have failed.",
    tools=("list_ideas", "recall", "record_idea"),
)

DATA_ENGINEER = AgentSpec(
    key="data_engineer",
    name="Data Engineer",
    model_key="cheap",
    purpose="Fetches, validates and aligns data; reports gaps, splits and survivorship bias.",
    system_prompt=VOICE
    + " You are responsible for data integrity. Report every quality warning to the desk and never "
    "hide synthetic or stale data.",
    tools=("load_data",),
)

CODER = AgentSpec(
    key="strategy_coder",
    name="Strategy Coder",
    model_key="worker",
    purpose="Turns a hypothesis into a declarative, scanned strategy with unit-level sanity checks.",
    system_prompt=VOICE
    + " You write simple, point-in-time-safe pandas/numpy signal code. No future data, no I/O, no "
    "randomness, no hidden state. Size positions through risk rules, not leverage in the signal.",
    tools=("scan_code",),
)

BACKTESTER = AgentSpec(
    key="backtester",
    name="Backtester",
    model_key="cheap",
    purpose="Runs in-sample grids and walk-forward optimisation with honest trial counting.",
    system_prompt=VOICE
    + " You run the grid and the walk-forward. Every parameter combination counts as a trial. "
    "Never report an in-sample number as evidence.",
    tools=("grid", "validate"),
)

SKEPTIC = AgentSpec(
    key="skeptic",
    name="Skeptic / Red Team",
    model_key="skeptic",
    purpose="Tries to break results: leakage, fragility, costs, regimes, multiple testing.",
    system_prompt=VOICE
    + " Your job is to falsify. Assume the result is an artifact until the validation gates pass. "
    "Name the specific gate or mechanism that fails, and never soften a rejection.",
    tools=("validate", "grid", "scan_code"),
)

RISK = AgentSpec(
    key="risk_analyst",
    name="Risk Analyst",
    model_key="worker",
    purpose="Measures drawdown, tail risk, exposure, correlation and capacity.",
    system_prompt=VOICE
    + " You quantify what can go wrong. Correlations and capacity are first-class results, not footnotes.",
    tools=("risk_report",),
)

PORTFOLIO = AgentSpec(
    key="portfolio_constructor",
    name="Portfolio Constructor",
    model_key="worker",
    purpose="Combines surviving strategies with HRP / risk parity and Kelly caps.",
    system_prompt=VOICE
    + " You combine only validated strategies, keep concentration bounded and report the diversification "
    "you actually achieved.",
    tools=("build_portfolio",),
)

REPORTER = AgentSpec(
    key="reporter",
    name="Reporter",
    model_key="cheap",
    purpose="Writes tearsheets and plain-English summaries with the caveats attached.",
    system_prompt=VOICE
    + " You write the tearsheet. Lead with what was measured, then the caveats. Never imply a result is "
    "actionable investment advice.",
    tools=("tearsheet",),
)

LIBRARIAN = AgentSpec(
    key="librarian",
    name="Librarian",
    model_key="cheap",
    purpose="Long-term memory: what worked, what failed and why, fed back to the Ideator.",
    system_prompt=VOICE
    + " You keep the memory honest. Record failures with their mechanism, not just their metrics.",
    tools=("remember", "recall"),
)

AGENT_SPECS: dict[str, AgentSpec] = {
    spec.key: spec
    for spec in (
        ORCHESTRATOR,
        IDEATOR,
        DATA_ENGINEER,
        CODER,
        BACKTESTER,
        SKEPTIC,
        RISK,
        PORTFOLIO,
        REPORTER,
        LIBRARIAN,
    )
}


def roster_payload() -> list[dict[str, Any]]:
    return [
        {
            "key": spec.key,
            "name": spec.name,
            "purpose": spec.purpose,
            "model_slot": spec.model_key,
            "tools": list(spec.tools),
        }
        for spec in AGENT_SPECS.values()
    ]


# --------------------------------------------------------------------- agents
class OrchestratorAgent:
    spec = ORCHESTRATOR

    def __init__(self, ctx: ToolContext) -> None:
        self.ctx = ctx

    def plan(self, objective: str, universe: list[str], max_strategies: int) -> dict[str, Any]:
        families = ["cross_sectional", "mean_reversion", "trend", "defensive", "regime", "seasonality"]
        prior = self.ctx.invoke(self.spec, "recall", query=objective, limit=5)
        narrative = self.ctx.narrate(
            self.spec,
            f"Sprint objective: {objective}\nUniverse: {universe}\nPrior lessons: {prior}\n"
            f"Plan at most {max_strategies} strategies across these families: {families}.",
        )
        return {
            "objective": objective,
            "universe": universe,
            "families": families,
            "max_strategies": max_strategies,
            "max_iterations": self.ctx.settings.max_agent_iterations,
            "budget_usd": self.ctx.budget.budget_usd,
            "narrative": narrative,
            "prior_lessons": prior,
            "policy": "research-only; candidate status requires human approval before paper trading",
        }


class IdeatorAgent:
    spec = IDEATOR

    def __init__(self, ctx: ToolContext) -> None:
        self.ctx = ctx

    def propose(self, objective: str, count: int) -> list[dict[str, Any]]:
        existing = self.ctx.invoke(self.spec, "list_ideas", limit=100)
        lessons = self.ctx.invoke(self.spec, "recall", query="failed rejected overfit", limit=8)
        failed_keys = {str(item.get("title", "")) for item in lessons}
        templates = list(TEMPLATES.values())
        lowered = objective.lower()
        if "momentum" in lowered:
            templates.sort(key=lambda t: "momentum" not in t.tags)
        if "mean reversion" in lowered or "reversion" in lowered:
            templates.sort(key=lambda t: "mean-reversion" not in t.tags)
        ranked = sorted(
            templates,
            key=lambda t: (t.name in failed_keys, -_family_priority(t.family), t.key),
        )
        proposals: list[dict[str, Any]] = []
        for template in ranked:
            if len(proposals) >= count:
                break
            variant = _variant(template, len(existing) + len(proposals))
            proposals.append(
                {
                    "template_key": template.key,
                    "title": f"{template.name} · v{len(proposals) + 1}",
                    "hypothesis": template.hypothesis,
                    "family": template.family,
                    "tags": list(template.tags),
                    "rationale": template.rationale,
                    "params": variant,
                }
            )
        text = self.ctx.narrate(
            self.spec,
            f"Objective: {objective}\nExisting ideas: {len(existing)}\nLessons: {lessons}\n"
            f"Proposed: {[p['title'] for p in proposals]}",
        )
        for proposal in proposals:
            proposal["narrative"] = text
        return proposals


class DataEngineerAgent:
    spec = DATA_ENGINEER

    def __init__(self, ctx: ToolContext) -> None:
        self.ctx = ctx

    def prepare(self, symbols: list[str], start: str, end: str) -> Any:
        dataset = self.ctx.invoke(self.spec, "load_data", symbols=symbols, start=start, end=end)
        warnings = dataset.warnings[:8]
        self.ctx.bus.emit(
            self.spec.key,
            f"Loaded {len(dataset.data.symbols)} symbols from {dataset.provider} "
            f"({len(dataset.data.close)} aligned bars). "
            + ("SYNTHETIC FALLBACK." if dataset.synthetic else "Live free data, quality-checked.")
            + (f" Warnings: {'; '.join(warnings)}" if warnings else ""),
            tool_name="load_data",
            tool_payload={"metadata": dataset.metadata},
        )
        return dataset


class StrategyCoderAgent:
    spec = CODER

    def __init__(self, ctx: ToolContext) -> None:
        self.ctx = ctx

    def draft(self, proposal: dict[str, Any], universe: list[str], version: int) -> tuple[StrategySpec, dict[str, Any]]:
        template = get_template(proposal["template_key"])
        spec = _spec_from_template(template, proposal, universe, version)
        scan = self.ctx.invoke(self.spec, "scan_code", code=spec.signal_code)
        self.ctx.bus.emit(
            self.spec.key,
            f"Wrote {spec.name} v{spec.version}: {len(spec.signal_code.splitlines())} lines, "
            f"features {list(spec.features)}, grid {template.grid}. AST scan passed.",
            tool_name="write_strategy",
            tool_payload={"spec": spec.model_dump(mode="json"), "scan": scan},
        )
        return spec, scan

    def refine(self, spec: StrategySpec, grid: GridResult, attempt: int) -> StrategySpec:
        ranked = grid.summary()
        next_params = ranked[attempt]["params"] if len(ranked) > attempt else {}
        risk = spec.risk.model_copy(
            update={
                "max_gross_exposure": max(0.25, spec.risk.max_gross_exposure * 0.8),
                "vol_target_annual": spec.risk.vol_target_annual or 0.12,
            }
        )
        refined = spec.model_copy(
            update={
                "version": spec.version + 1,
                "params": {**spec.params, **next_params},
                "risk": risk,
                "costs": Costs(
                    commission_bps=spec.costs.commission_bps * 1.5,
                    slippage_bps=spec.costs.slippage_bps * 1.5,
                    spread_bps=spec.costs.spread_bps,
                    borrow_bps_annual=spec.costs.borrow_bps_annual,
                ),
            }
        )
        self.ctx.bus.emit(
            self.spec.key,
            f"Revised {spec.name} → v{refined.version}: params {refined.params}, gross "
            f"{refined.risk.max_gross_exposure:.2f}, costs +50%. Re-running the gauntlet.",
        )
        return refined


class BacktesterAgent:
    spec = BACKTESTER

    def __init__(self, ctx: ToolContext) -> None:
        self.ctx = ctx

    def run(self, spec: StrategySpec, data: Any, grid: dict[str, list[Any]]) -> GridResult:
        result = self.ctx.invoke(self.spec, "grid", spec=spec, data=data, grid=grid)
        if result.best is not None:
            best = result.best
            self.ctx.bus.emit(
                self.spec.key,
                f"Grid {len(result.results)} trials. Best {result.best_params}: "
                f"Sharpe {best.metrics.get('sharpe', 0):.2f}, CAGR {best.metrics.get('cagr', 0):.1%}, "
                f"maxDD {best.metrics.get('max_drawdown', 0):.1%}. All in-sample; not evidence.",
            )
        else:
            self.ctx.bus.emit(self.spec.key, "Grid produced no candidate meeting the minimum trade count.")
        return result


class SkepticAgent:
    spec = SKEPTIC

    def __init__(self, ctx: ToolContext) -> None:
        self.ctx = ctx

    def review(self, spec: StrategySpec, data: Any, grid: dict[str, list[Any]]) -> ValidationReport:
        report = self.ctx.invoke(self.spec, "validate", spec=spec, data=data, grid=grid)
        failed = [gate for gate in report.gates if not gate.passed]
        objections = [
            f"{gate.name}: {gate.value} vs {gate.threshold} ({gate.detail})".strip()
            for gate in failed
        ]
        verdict = "PASS" if report.passed else "REJECT"
        self.ctx.bus.emit(
            self.spec.key,
            f"{verdict} — {sum(1 for g in report.gates if g.passed)}/{len(report.gates)} gates. "
            + (
                "Objections: " + " | ".join(objections[:6])
                if objections
                else "No gate failed; recommendation: " + report.recommendation
            ),
            tool_payload={"report_hash": report.to_dict()["report_hash"], "score": report.score},
        )
        return report


class RiskAnalystAgent:
    spec = RISK

    def __init__(self, ctx: ToolContext) -> None:
        self.ctx = ctx

    def assess(
        self,
        result: Any,
        candidate_returns: dict[str, pd.Series],
        dollar_volume: pd.Series | None,
    ) -> dict[str, Any]:
        report = self.ctx.invoke(
            self.spec,
            "risk_report",
            result=result,
            candidate_returns=candidate_returns,
            dollar_volume=dollar_volume,
        )
        self.ctx.bus.emit(
            self.spec.key,
            f"Risk: maxDD {result.metrics.get('max_drawdown', 0):.1%}, "
            f"CVaR95 {result.metrics.get('cvar_95', 0):.2%}, "
            f"max correlation {report['max_correlation']:.2f}, "
            f"capacity {report['capacity_usd_estimate'] or 'unknown'}.",
        )
        return report


class PortfolioConstructorAgent:
    spec = PORTFOLIO

    def __init__(self, ctx: ToolContext) -> None:
        self.ctx = ctx

    def construct(self, returns: pd.DataFrame, method: str = "hrp") -> dict[str, Any]:
        result = self.ctx.invoke(self.spec, "build_portfolio", returns=returns, method=method)
        weights = ", ".join(f"{k} {v:.0%}" for k, v in list(result["weights"].items())[:8])
        self.ctx.bus.emit(self.spec.key, f"{method.upper()} portfolio: {weights or 'no candidates'}.")
        return result


class ReporterAgent:
    spec = REPORTER

    def __init__(self, ctx: ToolContext) -> None:
        self.ctx = ctx

    def report(
        self,
        strategy: Any,
        spec: Any,
        result: Any,
        report: Any,
        dataset_meta: dict[str, Any],
    ) -> dict[str, Any]:
        rendered = self.ctx.invoke(
            self.spec,
            "tearsheet",
            strategy=strategy,
            spec=spec,
            result=result,
            report=report,
            dataset_meta=dataset_meta,
        )
        self.ctx.bus.emit(
            self.spec.key, f"Tearsheet written: {rendered['path']}. {rendered['summary']}"
        )
        return rendered


class LibrarianAgent:
    spec = LIBRARIAN

    def __init__(self, ctx: ToolContext) -> None:
        self.ctx = ctx

    def record(self, title: str, body: str, **kwargs: Any) -> dict[str, Any]:
        result = self.ctx.invoke(self.spec, "remember", title=title, body=body, **kwargs)
        self.ctx.bus.emit(self.spec.key, f"Remembered: {title}.")
        return result

    def recall(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        return self.ctx.invoke(self.spec, "recall", query=query, limit=limit)


# ------------------------------------------------------------------- helpers
def _spec_from_template(
    template: StrategyTemplate,
    proposal: dict[str, Any],
    universe: list[str],
    version: int,
) -> StrategySpec:
    risk = RiskRules(**{**template.risk})
    costs = Costs(commission_bps=1.0, slippage_bps=2.0, spread_bps=0.5, borrow_bps_annual=50.0)
    return make_spec(
        name=f"{template.name}",
        hypothesis=template.hypothesis,
        universe=universe,
        signal_code=template.signal_code,
        features=template.features,
        params={**template.params, **proposal.get("params", {})},
        costs=costs,
        risk=risk,
        benchmark=universe[0],
        version=version,
    )


def _variant(template: StrategyTemplate, index: int) -> dict[str, Any]:
    """Deterministic parameter variation so repeated sprints are not carbon copies."""
    params: dict[str, Any] = {}
    grid_items = list(template.grid.items())
    if not grid_items:
        return params
    key, values = grid_items[index % len(grid_items)]
    params[key] = values[(index // len(grid_items)) % len(values)]
    return params


def _family_priority(family: str) -> int:
    order = ["cross_sectional", "trend", "mean_reversion", "regime", "defensive", "seasonality"]
    return order.index(family) if family in order else len(order)


def sprint_id_seed(sprint_id: str) -> int:
    digest = hashlib.sha256(sprint_id.encode()).digest()
    return int.from_bytes(digest[:4], "big") % (2**31)


def utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()
