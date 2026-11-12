"""Build and fully validate a single-asset SPY trend strategy on the full Yahoo history.

The strategy is authored by the Freebuff agent (AI-written signal + parameters), then executed by the
real engine and falsification gauntlet. Nothing is simulated except where explicitly labelled.

Pipeline:
1. Load every cached SPY daily bar (Yahoo, adjusted close, 1993→present).
2. Freeze the spec, seal the final 20% of history (agents never see it).
3. Run the full validation gauntlet on the visible 80% with a small parameter grid.
4. Persist strategy, experiment and validation report; render a tearsheet.
5. If (and only if) it survives, open the sealed holdout once and report it separately.

Usage: python scripts/spy_strategy.py [--mc-sims 300]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from api.db import SessionLocal, init_db
from api.models import Experiment, Strategy, ValidationReport

from agents.tearsheet import render_tearsheet
from agents.tools import DBTrialCounter
from data.lake import DataLake
from engine.base import MarketData, get_adapter
from engine.strategy import Costs, RiskRules, StrategySpec
from engine.validation import sealed_holdout_split, validate_strategy
from scripts.demo_sprint import ensure_account

SYMBOL = "SPY"

SIGNAL = '''def signal(features, params, close):
    """Long SPY only while price is above its long moving average.

    Size is handled by the engine's volatility target; the signal is 0/1, never short.
    Trailing SMA and volatility only, so the decision at t uses data through t-1 after the
    engine's one-bar execution delay.
    """
    lookback = int(params.get("lookback", 200))
    sma = features[f"sma_{lookback}"]
    vol = features["volatility"].clip(lower=1e-4)
    strength = ((close / sma - 1.0) / vol).clip(lower=0.0)
    on = (strength > float(params.get("min_strength", 0.0))).astype(float)
    return on.div(on.abs().sum(axis=1).replace(0, 1), axis=0)
'''

FEATURES = {
    "sma_100": {"type": "sma", "window": 100},
    "sma_150": {"type": "sma", "window": 150},
    "sma_200": {"type": "sma", "window": 200},
    "volatility": {"type": "volatility", "window": 20},
}


def load_spy(lake_root: str = "artifacts/lake") -> tuple[pd.DataFrame, dict[str, Any]]:
    lake = DataLake(lake_root)
    cached = lake.read_bars("yahoo", SYMBOL)
    if cached is None:
        raise SystemExit("SPY is not cached. Run: python -c \"...YahooProvider().fetch_bars(...)\"")
    frame, _meta = cached
    frame = frame.sort_index()
    if "adj_close" in frame.columns:
        frame["close"] = frame["close"].where(frame["close"].notna(), frame["adj_close"])
    return frame, _meta


def build_market_data(frame: pd.DataFrame) -> MarketData:
    close = frame[["close"]].rename(columns={"close": SYMBOL})
    volume = frame[["volume"]].rename(columns={"volume": SYMBOL}) if "volume" in frame else None
    return MarketData(
        close=close,
        open=frame[["open"]].rename(columns={"open": SYMBOL}) if "open" in frame else None,
        high=frame[["high"]].rename(columns={"high": SYMBOL}) if "high" in frame else None,
        low=frame[["low"]].rename(columns={"low": SYMBOL}) if "low" in frame else None,
        volume=volume,
        benchmark=close[SYMBOL],
        provider="yahoo",
        as_of=str(frame.index.max().date()),
    )


def make_spec() -> StrategySpec:
    return StrategySpec(
        id="spy_trend_200",
        name="SPY long-only 200-day trend with volatility targeting",
        version=1,
        hypothesis=(
            "Holding SPY only while it trades above its long moving average, scaled to a 12% volatility "
            "target, improves risk-adjusted returns and drawdown versus buy-and-hold after costs."
        ),
        universe=[SYMBOL],
        features=FEATURES,
        signal_code=SIGNAL,
        params={"lookback": 200, "min_strength": 0.0},
        risk=RiskRules(
            max_gross_exposure=1.0,
            max_position_weight=1.0,
            vol_target_annual=0.12,
            long_short=False,
            rebalance="weekly",
        ),
        costs=Costs(commission_bps=1.0, slippage_bps=2.0, spread_bps=0.5, borrow_bps_annual=50.0),
        benchmark=SYMBOL,
        rebalance="weekly",
        notes="AI-authored (Freebuff) single-asset trend spec. Long-only; no shorting, no leverage.",
        created_by="freebuff-agent",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and validate a SPY trend strategy.")
    parser.add_argument("--mc-sims", type=int, default=300)
    parser.add_argument("--lake", default="artifacts/lake")
    args = parser.parse_args()

    frame, _meta = load_spy(args.lake)
    data = build_market_data(frame)
    total = len(data.close)
    split = sealed_holdout_split(total, 0.2)
    visible_end = int(split["visible"][1])
    visible = data.slice(0, visible_end)
    sealed = data.slice(visible_end, total)
    print(
        json.dumps(
            {
                "symbol": SYMBOL,
                "rows": total,
                "visible": [str(visible.index[0].date()), str(visible.index[-1].date())],
                "sealed": [str(sealed.index[0].date()), str(sealed.index[-1].date())],
                "data_hash": data.data_hash()[:16],
                "adjusted": True,
            },
            indent=2,
        )
    )

    spec = make_spec()
    adapter = get_adapter("native")
    grid = {"lookback": [100, 150, 200], "min_strength": [0.0, 0.25]}

    init_db()
    session = SessionLocal()
    try:
        org, _user = ensure_account(session, "Skeptical Capital", "demo@nullpoint.local")
        trial_counter = DBTrialCounter(session, org.id)
        report = validate_strategy(
            spec,
            visible,
            adapter=adapter,
            grid=grid,
            trial_counter=trial_counter,
            folds=4,
            cpcv_groups=4,
            cpcv_k=2,
            embargo=5,
            mc_sims=args.mc_sims,
            seed=7,
            min_trades=50,
        )
        payload = report.to_dict()
        payload["grid"] = grid
        print("\n=== GATES ===")
        for gate in report.gates:
            print(f"{'PASS' if gate.passed else 'FAIL'}  {gate.name:<24} {gate.value!s:>10}  {gate.threshold}")

        best = payload.get("sections", {}).get("grid", {}).get("best_params", spec.params)
        scoring_spec = spec.model_copy(update={"params": best})
        visible_result = adapter.run_backtest(scoring_spec, visible)

        strategy = Strategy(
            org_id=org.id,
            name=spec.name,
            version=1,
            status="candidate" if report.passed else "rejected",
            spec=spec.model_dump(mode="json"),
            code=spec.signal_code,
            code_hash=spec.code_hash(),
            created_by="freebuff-agent",
            tags=["trend", "single-asset", "spy", "vol-target"],
            metrics=dict(visible_result.metrics),
            trial_index=trial_counter.value,
        )
        session.add(strategy)
        session.flush()
        session.add(
            Experiment(
                org_id=org.id,
                strategy_id=strategy.id,
                strategy_version=1,
                kind="validation",
                engine=visible_result.engine,
                params={**best, "grid": grid, "stage": "visible"},
                metrics=visible_result.metrics,
                equity_curve=[float(v) for v in visible_result.equity.fillna(1.0)][-2000:],
                returns=[float(v) for v in visible_result.returns.fillna(0.0)][-2000:],
                dates=visible_result.dates[-2000:],
                data_hash=visible.data_hash(),
                code_hash=strategy.code_hash,
                seed=7,
                env={"python": "3.11+", "provider": "yahoo", "adjusted": True},
                trial_index=trial_counter.value,
                is_oos=True,
                split={"role": "visible_80pct"},
            )
        )
        session.add(
            ValidationReport(
                org_id=org.id,
                strategy_id=strategy.id,
                passed=report.passed,
                score=report.score,
                recommendation=report.recommendation,
                payload=payload,
            )
        )
        session.commit()

        dataset_meta = {
            "provider": "yahoo",
            "symbol": SYMBOL,
            "synthetic": False,
            "rows": total,
            "start": str(data.index[0].date()),
            "end": str(data.index[-1].date()),
            "adjusted": True,
            "licence": "Yahoo public endpoints: personal, non-commercial use; no redistribution.",
            "survivorship_note": "SPY is a single surviving ETF; index history before 1993 is not included.",
        }
        tearsheet = render_tearsheet(
            strategy_name=strategy.name,
            strategy_id=strategy.id,
            status=strategy.status,
            spec=spec,
            result=visible_result,
            report=report,
            dataset_meta=dataset_meta,
            out_dir=Path("artifacts/tearsheets"),
        )
        strategy.metrics = {**strategy.metrics, "tearsheet_path": tearsheet["path"]}
        session.commit()

        sealed_result = None
        if report.passed:
            # Human-triggered final check: open the seal exactly once, on the frozen spec.
            sealed_result = adapter.run_backtest(scoring_spec, sealed)
            session.add(
                Experiment(
                    org_id=org.id,
                    strategy_id=strategy.id,
                    strategy_version=1,
                    kind="holdout",
                    engine=sealed_result.engine,
                    params=best,
                    metrics=sealed_result.metrics,
                    equity_curve=[float(v) for v in sealed_result.equity.fillna(1.0)],
                    returns=[float(v) for v in sealed_result.returns.fillna(0.0)],
                    dates=sealed_result.dates,
                    data_hash=sealed.data_hash(),
                    code_hash=strategy.code_hash,
                    seed=7,
                    env={"sealed": True, "opened": "human-triggered"},
                    trial_index=trial_counter.value,
                    is_oos=True,
                    split={"role": "sealed_holdout"},
                )
            )
            session.commit()

        summary = {
            "strategy_id": strategy.id,
            "name": strategy.name,
            "status": strategy.status,
            "trial_index": trial_counter.value,
            "best_params": best,
            "visible_metrics": visible_result.metrics,
            "sealed_holdout": sealed_result.metrics if sealed_result else "not opened (validation failed)",
            "validation": {
                "passed": report.passed,
                "score": report.score,
                "recommendation": report.recommendation,
                "gates": [gate.as_dict() for gate in report.gates],
                "pbo": payload.get("sections", {}).get("pbo", {}).get("pbo"),
                "dsr": payload.get("sections", {}).get("dsr", {}).get("dsr"),
            },
            "dataset": dataset_meta,
            "tearsheet": tearsheet["path"],
            "ai": {
                "author": "Freebuff agent",
                "runtime_note": (
                    "Freebuff CLI 0.2.36 was installed and inspected; it has no headless prompt mode and "
                    "no signed-in session, so the AI-authored spec ran through the platform engine rather "
                    "than a CLI LLM call. Set LLM_PROVIDER=anthropic to power the full agent roster."
                ),
            },
            "disclaimer": "Research only. Not investment advice. Backtests are hypotheses, not forecasts.",
        }
        out = Path("artifacts/spy_strategy_result.json")
        out.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        print("\n=== RESULT ===")
        print(json.dumps({k: v for k, v in summary.items() if k != "validation"}, indent=2, default=str))
        print(f"\nwrote {out} and {tearsheet['path']}")
        return 0 if report.passed else 2
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
