# Architecture

```mermaid
flowchart LR
  UI[Next.js 15 app] -->|/api proxy, JWT| API[FastAPI]
  API --> DB[(Postgres)]
  API -->|enqueue| Q[[Redis + Arq]]
  Q --> W[Worker process]
  W --> AG[agents/]
  W --> EN[engine/]
  W --> DA[data/]
  W --> SB[sandbox/]
  AG -->|messages, trials| DB
  EN --> LAKE[(Parquet lake)]
  DA --> LAKE
  API -->|read| DB
  UI -->|SSE / polling| API
```

## Trust boundaries

1. **Browser** is untrusted. It holds a JWT (or API key) and never receives provider secrets.
2. **API process** handles auth, CRUD, serialization and SSE. It never executes agent-generated code.
   `JOB_RUNNER=sync` exists for tests only; production uses Arq or a detached subprocess.
3. **Worker** runs agents and the engine. Long jobs are bounded by budget, wall clock, iteration caps
   and a kill switch.
4. **Sandbox** executes strategy code with no network, read-only mounts, resource limits and an AST
   method allowlist. Docker is selected whenever available; production fails closed if it is not.
   With `ENGINE_SANDBOX=true`, the native backtest itself runs through the adapter, not just the
   static scan. The local runner is development-only, not a production boundary.
5. **Data lake** is a local Parquet store queried read-only through DuckDB (SELECT-only surface).
6. **Job ledger** records each worker attempt and heartbeat. A reaper requeues only stale workers;
   a deterministic failure is never retried silently.

## Request flow: a sprint

1. `POST /sprints` validates the objective/universe and creates a `Sprint` row (`queued`).
2. The API enqueues the job and returns immediately (or runs synchronously in tests).
3. The orchestrator plans, asks the ideator for hypotheses, and hands each to the quant.
4. The orchestrator and ideator query the tenant knowledge base (Voyage embeddings, local
   fallback) and optional Tavily/Exa/Firecrawl deep research before choosing ideas. Sources
   are advisory evidence; the same validation gates still decide.
5. The quant writes `signal(features, params, close)`; the sandbox scans and executes it.
6. Before code runs, the hypothesis is frozen with a falsifier, critique and trial plan in the
   pre-registration funnel; duplicates and over-budget trials are refused.
7. The backtester runs a parameter grid in an ordered, hardware-aware pool; every configuration
   still increments the org-wide `TrialCounter` exactly once.
8. The skeptic runs the validation gauntlet. The sealed holdout is hashed before the sprint and is not
   readable by agents.
9. Surviving strategies become `candidate`; failures become `rejected` and still receive a tearsheet.
10. A performance claim additionally requires the one-time human holdout record and the paper-days
    gate; until then `GET /strategies/{id}/claim` withholds headline metrics.
11. The auditor writes messages to `agent_message`; the UI replays them via `/messages` or SSE.

## Data model (core)

`organization → user/api_key → sprint → agent_message`
`sprint → job_attempt` (durable worker heartbeat/retry ledger)
`idea → strategy → experiment → validation_report`
`strategy → approval → paper_run`
`strategy → holdout_evaluation` (one row per version, unique constraint is the seal)
`trial_counter` is org-scoped and monotonic; `portfolio` stores weights + metrics.

All tenant tables carry `org_id`; every query in the routers filters by the authenticated principal's
org. IDs are opaque strings generated server-side.

## Engine

- `engine/strategy.py` — `StrategySpec` (universe, signal code, features, params, costs, risk).
- `engine/native.py` — reference backtester: signals at `t-1`, fills at next bar, costs, stops,
  rebalance schedules (daily/weekly/monthly), vol targeting, trade ledger.
- `engine/base.py` — `MarketData`, `EngineAdapter`, grid/WFO orchestration, adapter registry.
- `engine/validation.py` — CPCV/PBO, DSR, PSR, Monte Carlo, regime and cost stress, gate assembly.
- `engine/portfolio.py` — HRP, risk parity, inverse vol, mean variance, Kelly sizing.

Optional `vectorbt` and `backtesting.py` adapters raise `EngineUnavailable` with an install hint when
the extra is missing; the native engine is always available.

Grid trials are independent. Engines that can overlap safely (the subprocess sandbox) use the
ordered pool in `engine/performance.py`; the native pandas engine stays sequential because it is
GIL-bound, and instead reuses its feature panel and uses a NumPy trade ledger. The pool caps
concurrency from the cgroup/CPU budget and preserves input order. The DB-backed trial counter is
updated once per batch, never touched from a worker thread. Data downloads use the same helper.

## Frontend

Next.js App Router. `/api/*` is rewritten server-side to the API origin, so the browser only talks to
its own origin (no CORS, no exposed internal host). Pages are client components using a small
`useApi` hook. With no token, the hook returns deterministic demo data and every screen labels it
"demo data". Charts use Recharts and the brand palette; metrics use JetBrains Mono.

## Failure modes

- Data provider unavailable → fallback provider, then clearly labelled synthetic data.
- Engine exception inside a strategy → strategy rejected, sprint continues.
- Budget/wall clock exhausted → sprint stops with `budget_exhausted` and partial results preserved.
- Redis unavailable → API can fall back to a detached subprocess (`JOB_RUNNER=subprocess`).
- API restart mid-sprint → messages and trials are already persisted; the UI resumes with `?after=`.
