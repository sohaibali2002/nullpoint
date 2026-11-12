"""Render standalone HTML tearsheets from stored strategies, experiments and validation reports.

Tearsheets are normally produced during the sprint. This script is for re-rendering after a data
restore or for reviewing the registry in bulk. It never changes a strategy's status.

Usage: python scripts/make_tearsheet.py [--org-slug skeptical-capital]
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.db import SessionLocal, init_db
from api.models import Experiment, Organization, Strategy, ValidationReport
from sqlalchemy import select

OUT = Path("artifacts/tearsheets")

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{name} · Nullpoint tearsheet</title>
<style>
:root{{--ink:#07080a;--panel:#101218;--line:#252a36;--text:#e9ecf3;--muted:#99a1b3;--signal:#5fe3c0;--danger:#ff6b6b;--warn:#f2b34b}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--ink);color:var(--text);font:14px/1.55 Inter,system-ui,sans-serif}}
main{{max-width:900px;margin:0 auto;padding:48px 28px}}h1{{font-size:28px;letter-spacing:-.03em;margin:0 0 6px}}
.mono{{font-family:"JetBrains Mono",ui-monospace,monospace;font-variant-numeric:tabular-nums}}
.brand{{display:flex;align-items:center;gap:10px;margin-bottom:36px;font-weight:600;letter-spacing:-.02em}}
.dot{{width:10px;height:10px;border-radius:99px;background:var(--signal)}}.ring{{width:20px;height:20px;border:3px solid var(--text);border-radius:99px;position:relative}}
.ring:after{{content:"";position:absolute;right:-7px;top:-5px;width:7px;height:7px;border-radius:99px;background:var(--signal)}}
.sub{{color:var(--muted)}}.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--line);border:1px solid var(--line);border-radius:10px;overflow:hidden;margin:24px 0}}
.cell{{background:var(--panel);padding:14px}}.cell b{{display:block;font-size:18px;margin-top:6px}}
table{{width:100%;border-collapse:collapse;margin:16px 0 28px}}th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);font-size:12px}}
th{{color:var(--muted);font-weight:500;text-transform:uppercase;letter-spacing:.1em;font-size:10px}}
.pass{{color:var(--signal)}}.fail{{color:var(--danger)}}.warnbox{{border:1px solid #4a3b1c;background:#1b1710;color:#f2d9a4;padding:12px 14px;border-radius:8px;font-size:12px;margin:18px 0}}
footer{{border-top:1px solid var(--line);margin-top:36px;padding-top:14px;color:var(--muted);font-size:11px}}
</style></head><body><main>
<div class="brand"><span class="ring"></span> nullpoint</div>
<div class="sub mono">{status} · trial #{trial} · {strategy_id}</div>
<h1>{name}</h1>
<p class="sub">{hypothesis}</p>
<div class="grid">{cells}</div>
<div class="warnbox">Research only. Not investment advice. Backtests are hypotheses, not forecasts.
This tearsheet is generated from stored results; it is not a live performance report.</div>
<h3>Validation gates</h3>
<table><thead><tr><th>Gate</th><th>Result</th><th>Value</th><th>Threshold</th></tr></thead><tbody>{gates}</tbody></table>
<h3>Experiment ledger</h3>
<table><thead><tr><th>Kind</th><th>Engine</th><th>Trial</th><th>Data hash</th><th>Created</th></tr></thead><tbody>{experiments}</tbody></table>
<footer>Nullpoint · nothing ships until it beats the null · {generated}</footer>
</main></body></html>"""


def render(strategy: Strategy, experiments: list[Experiment], report: ValidationReport | None) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    metrics = strategy.metrics or {}
    keys = ["sharpe", "sortino", "cagr", "max_drawdown"]
    cells = "".join(
        f"<div class='cell'><span class='sub mono'>{html.escape(key)}</span><b class='mono'>"
        f"{_fmt(metrics.get(key), key)}</b></div>"
        for key in keys
    )
    gates = ""
    if report and report.payload.get("gates"):
        for gate in report.payload["gates"]:
            cls = "pass" if gate.get("passed") else "fail"
            gates += (
                f"<tr><td>{html.escape(str(gate.get('name')))}</td>"
                f"<td class='{cls}'>{(gate.get('passed') and 'PASS') or 'FAIL'}</td>"
                f"<td class='mono'>{_fmt(gate.get('value'), '')}</td>"
                f"<td class='sub'>{html.escape(str(gate.get('threshold', '')))}</td></tr>"
            )
    else:
        gates = "<tr><td colspan='4' class='sub'>No validation report stored.</td></tr>"
    rows = "".join(
        f"<tr><td>{html.escape(row.kind)}</td><td>{html.escape(row.engine)}</td>"
        f"<td class='mono'>#{row.trial_index}</td><td class='mono'>{(row.data_hash or '')[:12]}</td>"
        f"<td class='sub'>{row.created_at.isoformat()[:16] if row.created_at else ''}</td></tr>"
        for row in experiments
    ) or "<tr><td colspan='5' class='sub'>No experiments stored.</td></tr>"
    page = PAGE.format(
        name=html.escape(strategy.name),
        status=html.escape(strategy.status),
        trial=strategy.trial_index,
        strategy_id=strategy.id,
        hypothesis=html.escape(str((strategy.spec or {}).get("hypothesis") or "")),
        cells=cells,
        gates=gates,
        experiments=rows,
        generated=(strategy.updated_at or strategy.created_at).isoformat()[:19]
        if (strategy.updated_at or strategy.created_at)
        else "",
    )
    path = OUT / f"{strategy.id}.html"
    path.write_text(page, encoding="utf-8")
    return path


def _fmt(value: Any, key: str) -> str:
    if not isinstance(value, (int, float)):
        return "—"
    if key in {"cagr", "max_drawdown"}:
        return f"{value * 100:.1f}%"
    return f"{value:.2f}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Re-render HTML tearsheets.")
    parser.add_argument("--org-slug")
    args = parser.parse_args()
    init_db()
    session = SessionLocal()
    try:
        query = select(Strategy)
        if args.org_slug:
            org = session.execute(
                select(Organization).where(Organization.slug == args.org_slug)
            ).scalar_one_or_none()
            if org is None:
                print(json.dumps({"error": f"org {args.org_slug} not found"}))
                return 1
            query = query.where(Strategy.org_id == org.id)
        strategies = session.execute(query.order_by(Strategy.created_at.desc())).scalars().all()
        rendered = []
        for strategy in strategies:
            experiments = list(
                session.execute(
                    select(Experiment)
                    .where(Experiment.strategy_id == strategy.id)
                    .order_by(Experiment.created_at.desc())
                    .limit(12)
                ).scalars()
            )
            report = session.execute(
                select(ValidationReport)
                .where(ValidationReport.strategy_id == strategy.id)
                .order_by(ValidationReport.created_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            path = render(strategy, experiments, report)
            metrics = dict(strategy.metrics or {})
            metrics["tearsheet_path"] = str(path)
            strategy.metrics = metrics
            rendered.append({"strategy_id": strategy.id, "path": str(path)})
        session.commit()
        print(json.dumps({"rendered": rendered, "output": str(OUT)}, indent=2))
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
