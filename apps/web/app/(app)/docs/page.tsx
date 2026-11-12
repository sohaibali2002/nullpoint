import { BookOpen, Lock, ShieldCheck, Terminal } from "lucide-react";
import Link from "next/link";

import { Badge, Notice, PageHeader, Panel } from "@/components/ui";

const GATES = [
  ["Deflated Sharpe (DSR)", "≥ 0.90", "Sharpe deflated by the global trial count and higher moments"],
  ["PBO (CSCV)", "≤ 0.35", "Probability the selected config is overfit"],
  ["WFO OOS Sharpe", "≥ 0.50", "Anchored/rolling walk-forward, net of costs"],
  ["WFO efficiency", "≥ 0.50", "OOS Sharpe ÷ IS Sharpe across folds"],
  ["3× cost Sharpe", "≥ 0.30", "Commissions, spread, slippage and borrow at 3×"],
  ["1-bar delay Sharpe", "≥ 0.30", "Execution delayed one extra bar"],
  ["Monte Carlo p-value", "< 0.05", "Trade bootstrap vs zero-edge null"],
  ["Regime coverage", "≥ 3 regimes", "Positive expectancy across volatility regimes"],
  ["Track record", "≥ 8y & ≥ 100 trades", "Minimum evidence before promotion"],
  ["Sealed holdout", "untouched", "Opened once, only after the config is frozen"],
];

const ENDPOINTS = [
  ["POST", "/auth/register", "Create org + owner user, returns JWT"],
  ["POST", "/sprints", "Launch a bounded agent research run"],
  ["GET", "/sprints/{id}/messages", "Replay the persisted audit trail"],
  ["GET", "/sprints/{id}/stream", "SSE stream, resumable with ?after="],
  ["GET", "/strategies", "Registry with metrics, filters and sorting"],
  ["GET", "/strategies/{id}/validation", "Every gate, including failures"],
  ["POST", "/strategies/{id}/approve", "Human approval → paper (live_ready is a label only)"],
  ["GET", "/leaderboard", "Promoted strategies, ranked by visible Sharpe"],
  ["POST", "/portfolios", "HRP / risk parity / inverse vol / mean variance"],
  ["POST", "/paper", "Start a simulated paper run (approval required)"],
  ["GET", "/data/providers", "Providers with licence notes and availability"],
  ["GET", "/settings", "Runtime, model slots, key presence"],
  ["GET", "/usage", "LLM spend, trials and cost per candidate"],
  ["GET", "/health · /metrics", "Liveness and Prometheus metrics"],
];

export default function DocsPage() {
  return (
    <div>
      <PageHeader
        eyebrow="Documentation"
        title="The method, stated plainly."
        description="What Nullpoint measures, how it tries to falsify it, and what the software deliberately cannot do."
        actions={
          <Link
            href="/dashboard"
            className="focus-ring rounded-lg bg-signal px-3.5 py-2 text-xs font-medium text-ink hover:bg-signal/90"
          >
            Open the dashboard
          </Link>
        }
      />

      <div className="grid gap-3 xl:grid-cols-[1.5fr_1fr]">
        <div className="space-y-3">
          <Panel title="Quickstart" eyebrow="local, docker or single process">
            <div className="space-y-4 px-5 py-4">
              <div>
                <p className="label mb-2">1 · Full stack with Docker</p>
                <pre className="overflow-x-auto rounded-lg border border-line/70 bg-ink/60 px-3.5 py-3 font-mono text-[11px] leading-relaxed text-muted">
{`cp .env.example .env
docker compose up --build
# web  → http://localhost:3000
# api  → http://localhost:8000/docs`}
                </pre>
              </div>
              <div>
                <p className="label mb-2">2 · Local Python + Node</p>
                <pre className="overflow-x-auto rounded-lg border border-line/70 bg-ink/60 px-3.5 py-3 font-mono text-[11px] leading-relaxed text-muted">
{`uv sync --extra dev
make api        # uvicorn api.main:app --reload
make worker     # arq api.jobs.WorkerSettings
cd apps/web && npm install && npm run dev`}
                </pre>
              </div>
              <div>
                <p className="label mb-2">3 · Deterministic demo, no keys</p>
                <pre className="overflow-x-auto rounded-lg border border-line/70 bg-ink/60 px-3.5 py-3 font-mono text-[11px] leading-relaxed text-muted">
{`LLM_PROVIDER=mock JOB_RUNNER=sync make api
python scripts/demo_sprint.py --synthetic`}
                </pre>
              </div>
              <Notice tone="beam">
                <Terminal size={11} className="mr-1.5 inline" />
                Free data has limits. Run with <code className="mono">ALLOW_NETWORK=false</code> to force the
                clearly-labelled synthetic path when offline.
              </Notice>
            </div>
          </Panel>

          <Panel title="Architecture" eyebrow="one package, clear trust boundaries">
            <pre className="overflow-x-auto px-5 py-4 font-mono text-[10px] leading-[1.7] text-muted">
{`Next.js UI ── /api proxy ── FastAPI (auth, CRUD, SSE)
                               │
                     Arq queue / subprocess worker
                               │
        ┌──────────────────────┼──────────────────────┐
        │                      │                      │
   agents/               engine/                data/
   10 roles              native (tested)        yahoo · stooq · fred
   budget guard          vectorbt (optional)    sec · ccxt · …
   typed bus             validation gauntlet    Parquet + DuckDB lake
        │                      │                      │
        └──────────────┬───────┴──────────────────────┘
                       │
                 sandbox/ (no network, CPU/RAM/time caps)
                       │
            Postgres: sprints, messages, strategies,
            experiments, trials, approvals, paper runs`}
            </pre>
          </Panel>

          <Panel title="API surface" eyebrow="Bearer JWT or X-API-Key">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[620px] border-collapse">
                <thead>
                  <tr className="border-b border-line/60">
                    <th className="table-head">Method</th>
                    <th className="table-head">Path</th>
                    <th className="table-head">Purpose</th>
                  </tr>
                </thead>
                <tbody>
                  {ENDPOINTS.map(([method, path, purpose]) => (
                    <tr key={path}>
                      <td className="table-cell">
                        <Badge tone={method === "GET" ? "beam" : "signal"}>{method}</Badge>
                      </td>
                      <td className="table-cell mono text-[11px] text-text">{path}</td>
                      <td className="table-cell text-[11px] text-muted">{purpose}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="border-t border-line/60 px-4 py-3 text-[10px] text-muted">
              OpenAPI schema at <span className="mono">/openapi.json</span> · interactive docs at{" "}
              <span className="mono">/docs</span>. Rate limits are per key (or IP) and return 429.
            </p>
          </Panel>
        </div>

        <div className="space-y-3">
          <Panel title="Validation gates" eyebrow="hard constraints, not a score">
            <ul className="divide-y divide-line/60">
              {GATES.map(([gate, threshold, body]) => (
                <li key={gate} className="px-4 py-2.5">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[11px] text-text">{gate}</span>
                    <span className="mono shrink-0 text-[10px] text-signal">{threshold}</span>
                  </div>
                  <p className="mt-0.5 text-[10px] leading-relaxed text-muted">{body}</p>
                </li>
              ))}
            </ul>
          </Panel>

          <Panel title="Safety" eyebrow="by construction">
            <div className="space-y-3 px-4 py-4 text-[11px] leading-relaxed text-muted">
              <p className="flex gap-2">
                <Lock size={13} className="mt-0.5 shrink-0 text-signal" />
                <span>No live order routing exists. <span className="mono text-text">live_ready</span> is a
                label for human review, not an execution state.</span>
              </p>
              <p className="flex gap-2">
                <ShieldCheck size={13} className="mt-0.5 shrink-0 text-signal" />
                <span>Agent code never executes in the API process. Docker sandbox: no network, read-only
                mounts, CPU/RAM/PID/wall-clock limits and an AST blocklist.</span>
              </p>
              <p className="flex gap-2">
                <BookOpen size={13} className="mt-0.5 shrink-0 text-signal" />
                <span>Multi-tenant isolation on every query. API keys are stored hashed, shown once.</span>
              </p>
            </div>
          </Panel>

          <Panel title="Data honesty" eyebrow="licences and bias">
            <div className="space-y-2 px-4 py-4 text-[11px] leading-relaxed text-muted">
              <p>Yahoo endpoints are personal, non-commercial use. Do not redistribute.</p>
              <p>Stooq daily data is split-adjusted, not dividend-adjusted.</p>
              <p>Sector and mega-cap universes are current constituents: survivorship bias is recorded with
                every result.</p>
              <p>If market data is unavailable, the pipeline falls back to synthetic data — labelled in the
                API, the ledger, the tearsheet and this UI.</p>
            </div>
          </Panel>

          <Notice tone="warn">
            <strong>Research only.</strong> Nullpoint is not an investment adviser, broker or signal service.
            Backtests are hypotheses. Past performance does not predict future returns.
          </Notice>
        </div>
      </div>
    </div>
  );
}
