"""Chat-driven backtesting: a plain request becomes a measured, stored strategy.

The chat desk is allowed to do one thing it cannot fake: run the real engine on real prices and
report what happened.  A request such as "Create me a robust nasdaq trading strategy" selects a
universe and a small set of pre-registered templates, runs the in-sample grid, then puts every
candidate through the same gauntlet a sprint uses (walk-forward, CPCV, PBO, deflated Sharpe,
nulls, cost/delay stress, regime robustness).  The last 20% of the sample stays sealed.

Idea generation and narration stay with the LLM agents; every number below comes from the engine.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agents.roster import _spec_from_template
from agents.templates import TEMPLATES, get_template
from agents.tools import DBTrialCounter
from api.config import Settings
from api.models import Experiment, Strategy, StrategyStatus, ValidationReport
from data.lake import DataLake
from data.pipeline import load_universe
from engine.base import get_adapter
from engine.validation import sealed_holdout_split, validate_strategy
from sandbox.scanner import scan_code

# Liquid Nasdaq-100 names plus the index ETF as the benchmark.  This is a current-constituent
# universe, so survivorship bias applies and the report says so.
NASDAQ_100 = ["QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AVGO", "TSLA", "COST"]
US_LARGE_CAP = ["SPY", "QQQ", "DIA", "IWM", "XLK", "XLF", "XLV", "XLY"]
SECTOR_ETFS = ["SPY", "QQQ", "XLK", "XLF", "XLE", "XLV", "XLI", "XLP", "XLY", "XLU", "XLB", "XLRE"]
CRYPTO_MAJORS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]

DEFAULT_TEMPLATES = ("xs_momentum", "vol_regime_risk_on", "low_vol_defensive")
MAX_CANDIDATES = 3
MAX_GRID_TRIALS = 24
MC_SIMS = 150

ACTION_WORDS = (
    "create",
    "build",
    "make",
    "design",
    "develop",
    "construct",
    "produce",
    "generate",
    "write",
    "run",
    "test",
    "backtest",
    "find",
    "give me",
    "show me",
)
SUBJECT_WORDS = (
    "strateg",
    "trading system",
    "trading model",
    "signal",
    "backtest",
    "portfolio",
    "systematic",
    "rotation",
    "momentum",
    "mean reversion",
)

TEMPLATE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("xs_momentum", ("momentum", "relative strength", "rotation")),
    ("xs_mean_reversion", ("mean revers", "reversion", "oversold", "reversal")),
    ("trend_following", ("trend follow", "trend-follow", "moving average", "time series momentum")),
    ("low_vol_defensive", ("low vol", "low-vol", "defensive", "minimum variance")),
    ("vol_regime_risk_on", ("regime", "risk-on", "risk on", "volatility filter")),
    ("turn_of_month", ("seasonal", "seasonality", "calendar", "month-end", "turn of month")),
    ("beta_neutral_momentum", ("beta neutral", "beta-neutral", "market neutral")),
)


@dataclass
class Candidate:
    key: str
    name: str
    params: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)
    passed: bool = False
    score: float = 0.0
    gates_passed: int = 0
    gates_total: int = 0
    failed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class BacktestRun:
    strategy_id: str | None
    name: str
    template: str
    symbols: list[str]
    start: str
    visible_end: str
    sealed: list[str]
    provider: str
    synthetic: bool
    metrics: dict[str, float]
    params: dict[str, Any]
    gates: list[dict[str, Any]]
    passed: bool
    score: float
    status: str
    candidates: list[Candidate]
    chart: list[dict[str, Any]]
    trials: int
    disclaimer: str = "Research output. Not investment advice."


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _clean(payload: dict[str, Any]) -> dict[str, float]:
    """Keep only finite numbers so the JSON column and the chart stay usable."""
    return {
        key: _finite(value)
        for key, value in payload.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }


def detect_backtest_request(text: str) -> bool:
    """Only a request that asks for a strategy or a backtest launches the engine."""
    lowered = " ".join(str(text or "").lower().split())
    if not lowered:
        return False
    ask = bool(re.search(r"\b(?:" + "|".join(re.escape(word) for word in ACTION_WORDS) + r")\b", lowered))
    subject = any(word in lowered for word in SUBJECT_WORDS)
    explicit = "backtest" in lowered or "back-test" in lowered or "back test" in lowered
    question_only = lowered.startswith(("what is", "what are", "why ", "how does", "explain"))
    return bool(subject) and bool(explicit or ask) and not question_only


def parse_universe(text: str) -> tuple[list[str], str]:
    lowered = str(text or "").lower()
    upper = re.findall(r"\b[A-Z]{2,5}\b", str(text or ""))
    tickers = [token for token in upper if token not in {"US", "ETF", "AI", "CEO", "IPO", "AND", "THE", "USA", "USD"}]
    if tickers and not any(word in lowered for word in ("nasdaq", "s&p", "sector", "crypto", "bitcoin")):
        return list(dict.fromkeys(tickers))[:12], "custom tickers"
    if "crypto" in lowered or "bitcoin" in lowered or "btc" in lowered:
        return CRYPTO_MAJORS, "crypto majors"
    if "nasdaq" in lowered or "tech" in lowered or "megacap" in lowered or "mega cap" in lowered:
        return NASDAQ_100, "Nasdaq-100 majors"
    if "sector" in lowered:
        return SECTOR_ETFS, "US sector ETFs"
    return US_LARGE_CAP, "US large-cap ETFs"


def parse_window(text: str, *, today: datetime | None = None) -> tuple[str, str]:
    current = today or datetime.now(UTC)
    lowered = str(text or "").lower()
    years = sorted({int(match) for match in re.findall(r"\b(19\d{2}|20\d{2})\b", lowered)})
    years = [year for year in years if 2000 <= year <= current.year]
    if len(years) >= 2:
        return f"{years[0]}-01-01", f"{years[-1]}-12-31"
    if years:
        return f"{years[0]}-01-01", current.date().isoformat()
    trailing = re.search(r"(?:last|past)\s+(\d{1,2})\s+year", lowered)
    if trailing:
        years_back = max(1, min(int(trailing.group(1)), 25))
        return f"{current.year - years_back}-01-01", current.date().isoformat()
    return f"{current.year - 8}-01-01", current.date().isoformat()


def parse_templates(text: str) -> list[str]:
    lowered = str(text or "").lower()
    picks = [key for key, words in TEMPLATE_KEYWORDS if any(word in lowered for word in words)]
    for key in DEFAULT_TEMPLATES:
        if len(picks) >= MAX_CANDIDATES:
            break
        if key not in picks:
            picks.append(key)
    return [key for key in picks if key in TEMPLATES][:MAX_CANDIDATES]


def _normalise_chart(equity: Any, benchmark_returns: Any, limit: int = 140) -> list[dict[str, Any]]:
    """Down-sample the equity curve to a chart payload: candidate index vs benchmark index."""
    if equity is None or len(equity) == 0:
        return []
    base = _finite(equity.iloc[0], 1.0) or 1.0
    bench_level = 1.0
    bench_levels: list[float] = []
    for value in benchmark_returns.fillna(0.0):
        bench_level *= 1.0 + _finite(value)
        bench_levels.append(bench_level)
    step = max(1, len(equity) // limit)
    points: list[dict[str, Any]] = []
    for index in range(0, len(equity), step):
        stamp = equity.index[index]
        label = stamp.strftime("%Y-%m-%d") if hasattr(stamp, "strftime") else str(stamp)
        points.append(
            {
                "date": label,
                "equity": round(100.0 * _finite(equity.iloc[index]) / base, 2),
                "benchmark": round(100.0 * bench_returns_at(bench_levels, index), 2),
            }
        )
    if points and points[-1]["date"] != str(equity.index[-1])[:10]:
        stamp = equity.index[-1]
        label = stamp.strftime("%Y-%m-%d") if hasattr(stamp, "strftime") else str(stamp)
        points.append(
            {
                "date": label,
                "equity": round(100.0 * _finite(equity.iloc[-1]) / base, 2),
                "benchmark": round(100.0 * bench_returns_at(bench_levels, len(equity) - 1), 2),
            }
        )
    return points


def bench_returns_at(levels: list[float], index: int) -> float:
    if not levels:
        return 1.0
    return levels[min(index, len(levels) - 1)]


def _gate_rows(report: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for gate in report.gates:
        status = getattr(gate, "status", None) or ("pass" if gate.passed else "fail")
        rows.append(
            {
                "label": gate.name,
                "status": status,
                "detail": f"{gate.value} vs {gate.threshold}" if gate.value is not None else gate.threshold,
            }
        )
    return rows


def _persist(
    db: Session,
    org_id: str,
    run: dict[str, Any],
    dataset: Any,
    result: Any,
    report: Any,
    spec: Any,
    trials: int,
    universe_label: str,
) -> str:
    base_name = f"{spec.name} · {universe_label}"
    existing = db.execute(
        select(func.count(Strategy.id)).where(Strategy.org_id == org_id, Strategy.name == base_name)
    ).scalar_one()
    strategy = Strategy(
        org_id=org_id,
        name=base_name,
        version=int(existing) + 1,
        status=run["status"],
        spec=spec.model_dump(mode="json"),
        code=spec.signal_code,
        code_hash=spec.code_hash(),
        created_by="chat-desk",
        tags=[run["template"], "chat-backtest"],
        metrics={**run["metrics"], **run["extra_metrics"]},
        trial_index=trials,
    )
    db.add(strategy)
    db.commit()
    db.refresh(strategy)
    db.add(
        Experiment(
            org_id=org_id,
            strategy_id=strategy.id,
            strategy_version=strategy.version,
            kind="backtest",
            engine=result.engine,
            params={**(result.params or {}), "template": run["template"]},
            metrics=_clean(result.metrics),
            equity_curve=[_finite(v) for v in result.equity.fillna(1.0)][-1000:],
            returns=[_finite(v) for v in result.returns.fillna(0.0)][-1000:],
            dates=result.dates[-1000:],
            data_hash=dataset.content_hash,
            code_hash=strategy.code_hash,
            seed=result.seed,
            env={"engine": result.engine, "source": "chat", "provider": dataset.provider},
            trial_index=trials,
            split={"role": "visible", "sealed": run["sealed"]},
        )
    )
    db.add(
        ValidationReport(
            org_id=org_id,
            strategy_id=strategy.id,
            passed=bool(report.passed),
            score=_finite(report.score),
            recommendation=report.recommendation,
            payload=report.to_dict(),
        )
    )
    db.commit()
    return strategy.id


def run_nasdaq_backtest(db: Session, org_id: str, prompt: str, settings: Settings) -> BacktestRun:
    """Run a real, pre-registered backtest for a chat request and store the result."""
    symbols, universe_label = parse_universe(prompt)
    start, end = parse_window(prompt)
    template_keys = parse_templates(prompt)
    adapter = get_adapter("native")
    counter = DBTrialCounter(db, org_id)

    dataset = load_universe(
        symbols,
        start=start,
        end=end,
        lake=DataLake(settings.lake_root),
        allow_network=settings.allow_network,
        cache_ttl_hours=float(settings.data_cache_ttl_hours),
        max_workers=settings.max_workers or None,
    )
    total_rows = len(dataset.data.close)
    if total_rows < 260:
        raise ValueError(
            f"only {total_rows} aligned bars for {', '.join(symbols)}; need at least 260 to backtest"
        )
    split = sealed_holdout_split(total_rows, 0.2)
    visible_end = int(split["visible"][1])
    visible = dataset.data.slice(0, visible_end)
    sealed = [
        str(dataset.data.index[visible_end].date()),
        str(dataset.data.index[-1].date()),
    ]

    candidates: list[Candidate] = []
    best: tuple[Candidate, Any, Any, Any] | None = None
    for key in template_keys:
        template = get_template(key)
        candidate = Candidate(key=key, name=template.name)
        try:
            scan_code(template.signal_code)
            spec = _spec_from_template(template, {}, symbols, 1)
            grid = adapter.run_grid(
                spec,
                template.grid,
                visible,
                objective="sharpe",
                trial_counter=counter,
                min_trades=3,
                max_trials=MAX_GRID_TRIALS,
                max_workers=settings.max_workers or None,
            )
            if grid.best is None:
                candidate.error = "no configuration met the minimum trade count"
                candidates.append(candidate)
                continue
            report = validate_strategy(
                spec,
                visible,
                adapter=adapter,
                grid=template.grid,
                trial_counter=counter,
                folds=3,
                cpcv_groups=4,
                cpcv_k=2,
                embargo=5,
                mc_sims=MC_SIMS,
                seed=7,
                max_workers=settings.max_workers or None,
            )
            candidate.params = dict(grid.best_params or {})
            candidate.metrics = _clean(grid.best.metrics)
            candidate.passed = bool(report.passed)
            candidate.score = _finite(report.score)
            candidate.gates_total = len(report.gates)
            candidate.gates_passed = sum(1 for gate in report.gates if gate.passed)
            candidate.failed = [gate.name for gate in report.gates if not gate.passed and not gate.skipped]
            candidate.skipped = [gate.name for gate in report.gates if gate.skipped]
            candidates.append(candidate)
            if best is None or (candidate.passed, candidate.score, candidate.metrics.get("sharpe", 0.0)) > (
                best[0].passed,
                best[0].score,
                best[0].metrics.get("sharpe", 0.0),
            ):
                best = (candidate, spec, grid.best, report)
        except Exception as exc:  # one bad template must not lose the whole run
            candidate.error = f"{type(exc).__name__}: {exc}"
            candidates.append(candidate)

    if best is None:
        raise ValueError(
            "every candidate failed before validation: "
            + "; ".join(f"{c.key}: {c.error}" for c in candidates if c.error)
        )

    candidate, spec, result, report = best
    metrics = _clean(result.metrics)
    status = (
        StrategyStatus.VALIDATED.value
        if candidate.passed
        else StrategyStatus.BACKTESTED.value
        if candidate.score >= 0.6
        else StrategyStatus.REJECTED.value
    )
    trial_total = counter.value
    run_payload = {
        "template": candidate.key,
        "status": status,
        "metrics": metrics,
        "sealed": sealed,
        "extra_metrics": {
            "template": candidate.key,
            "validation_score": candidate.score,
            "gates_passed": candidate.gates_passed,
            "gates_total": candidate.gates_total,
            "validation": "passed" if candidate.passed else "failed",
            "universe_label": universe_label,
            "provider": dataset.provider,
            "synthetic": dataset.synthetic,
        },
    }
    strategy_id = _persist(db, org_id, run_payload, dataset, result, report, spec, trial_total, universe_label)
    return BacktestRun(
        strategy_id=strategy_id,
        name=spec.name,
        template=candidate.key,
        symbols=symbols,
        start=str(visible.close.index[0].date()),
        visible_end=str(visible.close.index[-1].date()),
        sealed=sealed,
        provider=dataset.provider,
        synthetic=bool(dataset.synthetic),
        metrics=metrics,
        params=candidate.params,
        gates=_gate_rows(report),
        passed=candidate.passed,
        score=candidate.score,
        status=status,
        candidates=candidates,
        chart=_normalise_chart(result.equity, result.benchmark_returns),
        trials=trial_total,
    )


def _pct(value: float, digits: int = 1) -> str:
    return f"{value * 100:.{digits}f}%"


def result_blocks(run: BacktestRun) -> list[dict[str, Any]]:
    """Generative-UI payload for the chat: measured numbers only."""
    metrics = run.metrics
    blocks: list[dict[str, Any]] = [
        {
            "type": "metrics",
            "title": f"{run.name} · net of costs",
            "items": [
                {"label": "Sharpe", "value": f"{metrics.get('sharpe', 0.0):.2f}", "hint": "visible sample"},
                {"label": "CAGR", "value": _pct(metrics.get("cagr", 0.0)), "hint": "compounded"},
                {"label": "Max drawdown", "value": _pct(metrics.get("max_drawdown", 0.0)), "hint": "peak to trough"},
                {"label": "Trades", "value": f"{metrics.get('n_trades', 0):.0f}", "hint": "fills"},
                {
                    "label": "Volatility",
                    "value": _pct(metrics.get("volatility", 0.0)),
                    "hint": "annualised",
                },
                {
                    "label": "Benchmark Sharpe",
                    "value": f"{metrics.get('benchmark_sharpe', 0.0):.2f}",
                    "hint": run.symbols[0],
                },
            ],
        },
        {
            "type": "chart",
            "title": "Equity index vs benchmark",
            "series": [],
        },
        {
            "type": "gates",
            "title": f"Validation — {sum(1 for g in run.gates if g['status'] == 'pass')}/{len(run.gates)} passed",
            "items": run.gates,
        },
        {
            "type": "table",
            "title": "Candidates tested",
            "columns": ["Family", "Sharpe", "CAGR", "Max DD", "Gates", "Verdict"],
            "rows": [
                [
                    item.key,
                    f"{item.metrics.get('sharpe', 0.0):.2f}" if not item.error else "—",
                    _pct(item.metrics.get("cagr", 0.0)) if not item.error else "—",
                    _pct(item.metrics.get("max_drawdown", 0.0)) if not item.error else "—",
                    f"{item.gates_passed}/{item.gates_total}" if item.gates_total else "n/a",
                    "validated" if item.passed else (item.error or "rejected"),
                ]
                for item in run.candidates
            ],
        },
        {
            "type": "callout",
            "tone": "info" if run.passed else "warn",
            "title": "Verdict",
            "body": (
                f"{run.name} cleared every critical gate on the visible sample. It still needs "
                "human review and paper evidence before any capital is discussed."
                if run.passed
                else f"Best of {len(run.candidates)} candidates scored {run.score:.2f} and failed "
                + ", ".join(
                    gate["label"]
                    for gate in run.gates
                    if gate["status"] == "fail"
                )[:220]
                + ". The honest result is no promotion."
            ),
            "link": f"/strategies/{run.strategy_id}" if run.strategy_id else None,
        },
    ]
    # Chart series live in the block's `series` shape used by the client renderer.
    blocks[1]["series"] = [
        {
            "label": "strategy",
            "points": [[point["date"], point["equity"]] for point in run.chart],
        },
        {
            "label": "benchmark",
            "points": [[point["date"], point["benchmark"]] for point in run.chart],
        },
    ]
    return blocks


def message_content(run: BacktestRun) -> str:
    metrics = run.metrics
    failed = [gate["label"] for gate in run.gates if gate["status"] == "fail"]
    verdict = (
        "VALIDATED on the visible sample"
        if run.passed
        else f"not promoted: {len(failed)} gate(s) failed ({', '.join(failed[:5]) or 'n/a'})"
    )
    summary = [
        f"**Backtest finished — {run.name}**",
        f"Universe: {len(run.symbols)} symbols ({', '.join(run.symbols[:6])}{'…' if len(run.symbols) > 6 else ''}) "
        f"from {run.provider}{' (SYNTHETIC fallback)' if run.synthetic else ''}.",
        f"Visible sample: {run.start} → {run.visible_end}. Sealed holdout {run.sealed[0]} → {run.sealed[1]} "
        "was not used for selection.",
        f"Parameters: {json.dumps(run.params, default=str)}.",
        f"Measured: Sharpe {metrics.get('sharpe', 0.0):.2f}, CAGR {_pct(metrics.get('cagr', 0.0))}, "
        f"max drawdown {_pct(metrics.get('max_drawdown', 0.0))}, {metrics.get('n_trades', 0):.0f} trades, "
        f"{run.trials} org-wide trials counted.",
        f"Verdict: {verdict}.",
        f"Full report: /strategies/{run.strategy_id}",
    ]
    blocks = json.dumps(result_blocks(run), default=str)
    return "\n\n".join(summary) + "\n\n```ui\n" + blocks + "\n```\n\n" + run.disclaimer


def summary_text(run: BacktestRun) -> str:
    """Compact measurement block handed to the LLM so its prose can only quote real numbers."""
    metrics = run.metrics
    gates = "; ".join(
        f"{gate['label']}={gate['status']}" + (f" ({gate['detail']})" if gate.get("detail") else "")
        for gate in run.gates
    )
    candidates = "; ".join(
        f"{item.key}: sharpe {item.metrics.get('sharpe', 0.0):.2f}, gates {item.gates_passed}/{item.gates_total}"
        if not item.error
        else f"{item.key}: {item.error}"
        for item in run.candidates
    )
    return (
        f"The desk just ran a real backtest in this conversation and stored it as strategy "
        f"{run.strategy_id} ({run.name}).\n"
        f"Universe: {', '.join(run.symbols)}. Visible {run.start} → {run.visible_end}; "
        f"sealed {run.sealed[0]} → {run.sealed[1]}.\n"
        f"Parameters: {json.dumps(run.params, default=str)}.\n"
        f"Measured net: Sharpe {metrics.get('sharpe', 0.0):.2f}, CAGR "
        f"{metrics.get('cagr', 0.0):.4f}, max drawdown {metrics.get('max_drawdown', 0.0):.4f}, "
        f"volatility {metrics.get('volatility', 0.0):.4f}, trades {metrics.get('n_trades', 0.0):.0f}, "
        f"turnover_annual {metrics.get('turnover_annual', 0.0):.1f}.\n"
        f"Verdict: {'PASSED' if run.passed else 'FAILED'} (score {run.score:.2f}). Gates: {gates}.\n"
        f"Other candidates tested: {candidates}.\n"
        "Quote only these numbers. You did not invent them and you may not add new ones."
    )
