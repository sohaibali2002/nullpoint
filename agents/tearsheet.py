"""Branded, self-contained HTML tearsheets (no external assets, safe to email or print)."""

from __future__ import annotations

import html
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from engine.base import BacktestResult
from engine.strategy import StrategySpec
from engine.validation import ValidationReport

PALETTE = {
    "ink": "#07080A",
    "graphite": "#101218",
    "line": "#252A36",
    "text": "#E9ECF3",
    "muted": "#99A1B3",
    "signal": "#5FE3C0",
    "beam": "#7AA2FF",
    "warn": "#F2B34B",
    "danger": "#FF6B6B",
}

MONO = "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace"
SANS = "Inter, -apple-system, 'Segoe UI', sans-serif"

HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{name} — Nullpoint tearsheet</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:{ink}; color:{text}; font-family:{sans}; }}
  .wrap {{ max-width: 1000px; margin: 0 auto; padding: 48px 32px 80px; }}
  header {{ display:flex; align-items:center; justify-content:space-between; border-bottom:1px solid {line}; padding-bottom:20px; }}
  .brand {{ display:flex; gap:12px; align-items:center; font-weight:600; letter-spacing:-.02em; }}
  .mark {{ width:28px; height:28px; }}
  .status {{ font-family:{mono}; font-size:12px; text-transform:uppercase; letter-spacing:.14em; color:{signal}; border:1px solid {line}; padding:6px 10px; border-radius:999px; }}
  h1 {{ font-size:34px; letter-spacing:-.03em; margin:32px 0 6px; font-weight:600; }}
  .hyp {{ color:{muted}; max-width:70ch; line-height:1.55; margin:0 0 28px; }}
  .grid {{ display:grid; grid-template-columns:repeat(4,1fr); gap:1px; background:{line}; border:1px solid {line}; }}
  .cell {{ background:{graphite}; padding:16px; }}
  .label {{ font-family:{mono}; font-size:10px; letter-spacing:.12em; text-transform:uppercase; color:{muted}; }}
  .value {{ font-family:{mono}; font-size:22px; margin-top:6px; }}
  .sub {{ font-family:{mono}; font-size:11px; color:{muted}; margin-top:4px; }}
  section {{ margin-top:40px; }}
  h2 {{ font-size:12px; letter-spacing:.16em; text-transform:uppercase; color:{muted}; font-weight:500; margin-bottom:14px; }}
  table {{ width:100%; border-collapse:collapse; font-family:{mono}; font-size:13px; }}
  th {{ text-align:left; color:{muted}; font-weight:500; border-bottom:1px solid {line}; padding:8px 10px; }}
  td {{ border-bottom:1px solid {line}; padding:8px 10px; }}
  .pass {{ color:{signal}; }} .fail {{ color:{danger}; }} .warn {{ color:{warn}; }}
  .chart {{ background:{graphite}; border:1px solid {line}; padding:16px; }}
  pre {{ background:{graphite}; border:1px solid {line}; padding:16px; overflow:auto; font-size:12px; color:{muted}; }}
  footer {{ margin-top:56px; border-top:1px solid {line}; padding-top:20px; color:{muted}; font-size:12px; line-height:1.6; }}
  .disclaimer {{ color:{warn}; }}
</style></head>
<body><div class="wrap">
<header>
  <div class="brand">
    <svg class="mark" viewBox="0 0 64 64" aria-hidden="true">
      <circle cx="28" cy="36" r="15" fill="none" stroke="{text}" stroke-width="3.2"/>
      <circle cx="50" cy="14" r="4.6" fill="{signal}"/>
    </svg>
    nullpoint
  </div>
  <div class="status">{status}</div>
</header>
<h1>{name}</h1>
<p class="hyp">{hypothesis}</p>
<div class="grid">
  <div class="cell"><div class="label">Sharpe (net)</div><div class="value">{sharpe}</div><div class="sub">{periods} bars</div></div>
  <div class="cell"><div class="label">CAGR</div><div class="value">{cagr}</div><div class="sub">after costs</div></div>
  <div class="cell"><div class="label">Max drawdown</div><div class="value">{mdd}</div><div class="sub">{mdd_days} bars</div></div>
  <div class="cell"><div class="label">Deflated Sharpe</div><div class="value">{dsr}</div><div class="sub">N={n_trials} trials</div></div>
</div>

<section><h2>Equity curve (net of costs)</h2><div class="chart">{chart}</div></section>

<section><h2>Validation gates</h2>
<table><thead><tr><th>Gate</th><th>Result</th><th>Value</th><th>Requirement</th></tr></thead><tbody>
{gates}
</tbody></table></section>

<section><h2>Risk &amp; execution</h2>
<table><tbody>
<tr><td>Volatility (ann.)</td><td>{volatility}</td></tr>
<tr><td>Sortino</td><td>{sortino}</td></tr>
<tr><td>CVaR 95%</td><td>{cvar}</td></tr>
<tr><td>Turnover (ann.)</td><td>{turnover}</td></tr>
<tr><td>Exposure</td><td>{exposure}</td></tr>
<tr><td>Trades</td><td>{trades}</td></tr>
<tr><td>Costs (share model)</td><td>{cost_model}</td></tr>
</tbody></table></section>

<section><h2>Data provenance</h2>
<table><tbody>
<tr><td>Provider</td><td>{provider}</td></tr>
<tr><td>Synthetic</td><td>{synthetic}</td></tr>
<tr><td>Window</td><td>{window}</td></tr>
<tr><td>Data hash</td><td style="word-break:break-all">{data_hash}</td></tr>
<tr><td>Universe</td><td>{universe}</td></tr>
</tbody></table></section>

<section><h2>Signal source (scanned, sandboxed)</h2><pre>{code}</pre></section>

<footer>
  <p><strong>{name}</strong> was evaluated on {created_at}. Status: {status}. Trial count at report time: {n_trials}.</p>
  <p class="disclaimer">Research only. Not investment advice. Backtests are hypotheses, not forecasts.
  Costs, slippage and borrow are modelled, not observed. Past performance does not predict future results.
  Data may contain errors and, where marked synthetic, is not market data at all.</p>
  <p>Nullpoint · nothing ships until it beats the null.</p>
</footer>
</div></body></html>
"""


def equity_svg(equity: pd.Series, width: int = 880, height: int = 220) -> str:
    series = pd.Series(equity, dtype=float).replace([np.inf, -np.inf], np.nan).dropna()
    if len(series) < 2:
        return "<svg viewBox='0 0 880 220'></svg>"
    values = series.to_numpy()
    low, high = float(values.min()), float(values.max())
    span = max(high - low, 1e-9)
    pad = 12
    xs = np.linspace(pad, width - pad, len(values))
    ys = height - pad - (values - low) / span * (height - 2 * pad)
    points = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys, strict=True))
    baseline = height - pad - (1.0 - low) / span * (height - 2 * pad) if low <= 1.0 <= high else height - pad
    return (
        f"<svg viewBox='0 0 {width} {height}' width='100%' height='{height}' preserveAspectRatio='none'>"
        f"<line x1='0' y1='{baseline:.1f}' x2='{width}' y2='{baseline:.1f}' stroke='{PALETTE['line']}' stroke-width='1'/>"
        f"<polyline fill='none' stroke='{PALETTE['signal']}' stroke-width='2' points='{points}'/>"
        f"<text x='{width - 8}' y='18' text-anchor='end' fill='{PALETTE['muted']}' font-size='11' "
        f"font-family='JetBrains Mono, monospace'>{low:.2f} – {high:.2f}</text></svg>"
    )


def render_tearsheet(
    *,
    strategy_name: str,
    strategy_id: str,
    status: str,
    spec: StrategySpec,
    result: BacktestResult,
    report: ValidationReport,
    dataset_meta: dict[str, Any],
    out_dir: Path,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    metrics = result.metrics
    gates = "\n".join(
        "<tr><td>{name}</td><td class='{cls}'>{verdict}</td><td>{value}</td><td class='sub'>{threshold}</td></tr>".format(
            name=html.escape(gate.name),
            cls="pass" if gate.passed else "fail",
            verdict="PASS" if gate.passed else "FAIL",
            value=_fmt(gate.value),
            threshold=html.escape(gate.threshold),
        )
        for gate in report.gates
    )
    summary = _summary(metrics, report, dataset_meta)
    page = HTML.format(
        name=html.escape(strategy_name),
        status=html.escape(status),
        hypothesis=html.escape(spec.hypothesis),
        sharpe=_fmt(metrics.get("sharpe")),
        cagr=_pct(metrics.get("cagr")),
        mdd=_pct(metrics.get("max_drawdown")),
        mdd_days=_fmt(metrics.get("max_drawdown_duration"), 0),
        dsr=_fmt(report.sections.get("deflated_sharpe", {}).get("dsr")),
        n_trials=report.n_trials_used,
        periods=_fmt(metrics.get("n_periods"), 0),
        chart=equity_svg(result.equity),
        gates=gates,
        volatility=_pct(metrics.get("volatility")),
        sortino=_fmt(metrics.get("sortino")),
        cvar=_pct(metrics.get("cvar_95")),
        turnover=_fmt(metrics.get("turnover_annual")),
        exposure=_fmt(metrics.get("exposure")),
        trades=_fmt(metrics.get("n_trades"), 0),
        cost_model=f"{spec.costs.commission_bps:g}/{spec.costs.slippage_bps:g}/{spec.costs.spread_bps:g} bps "
        f"+ {spec.costs.borrow_bps_annual:g} bps borrow",
        provider=html.escape(str(dataset_meta.get("provider", "unknown"))),
        synthetic="YES — not market data" if dataset_meta.get("synthetic") else "no",
        window=f"{dataset_meta.get('start')} → {dataset_meta.get('end')}",
        data_hash=html.escape(str(dataset_meta.get("content_hash", ""))),
        universe=", ".join(spec.universe),
        code=html.escape(spec.signal_code),
        created_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        ink=PALETTE["ink"],
        graphite=PALETTE["graphite"],
        line=PALETTE["line"],
        text=PALETTE["text"],
        muted=PALETTE["muted"],
        signal=PALETTE["signal"],
        warn=PALETTE["warn"],
        danger=PALETTE["danger"],
        mono=MONO,
        sans=SANS,
    )
    path = out_dir / f"{strategy_id}.html"
    path.write_text(page, encoding="utf-8")
    return {
        "summary": summary,
        "path": str(path),
        "strategy_id": strategy_id,
        "gates_passed": sum(1 for g in report.gates if g.passed),
        "gates_total": len(report.gates),
        "verdict": report.recommendation,
    }


def _summary(metrics: dict[str, Any], report: ValidationReport, dataset_meta: dict[str, Any]) -> str:
    verdict = report.recommendation.upper()
    passed = sum(1 for gate in report.gates if gate.passed)
    data_note = (
        "Synthetic data was used, so this is a pipeline check rather than market evidence."
        if dataset_meta.get("synthetic")
        else f"Data came from {dataset_meta.get('provider')} and passed quality checks."
    )
    return (
        f"{verdict}: {passed}/{len(report.gates)} validation gates passed. "
        f"Net Sharpe {_fmt(metrics.get('sharpe'))} over {_fmt(metrics.get('n_periods'), 0)} bars, "
        f"max drawdown {_pct(metrics.get('max_drawdown'))}, {_fmt(metrics.get('n_trades'), 0)} trades. "
        f"Deflated Sharpe {_fmt(report.sections.get('deflated_sharpe', {}).get('dsr'))} after "
        f"{report.n_trials_used} trials; PBO {_fmt(report.sections.get('pbo', {}).get('pbo'))}. "
        f"{data_note} Research only, not investment advice."
    )


def _fmt(value: Any, digits: int = 2) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if not np.isfinite(number):
        return "—"
    return f"{number:,.{digits}f}"


def _pct(value: Any, digits: int = 1) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if not np.isfinite(number):
        return "—"
    return f"{number * 100:,.{digits}f}%"
