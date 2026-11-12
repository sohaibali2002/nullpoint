# --- Milestones, decisions, and trade-offs ---------------------------------
# Append-only. Newest at the bottom. Each entry: context -> decision -> why.
# "Not built" entries are deliberate, not forgotten.

## M1 — Foundations

### D1. Brand: "Nullpoint" (see /brand/NAMES.md)
Chosen because it names the differentiator (falsification against a null) rather than the
category. Tagline: *Nothing ships until it beats the null.* The four rejected candidates and the
trademark/domain clearance checklist are documented for the owner.

### D2. One Python package, four importable domains
`engine`, `data`, `agents`, `sandbox` live at the repo root as required; the FastAPI app lives in
`apps/api/api` and is mapped into the wheel via `[tool.setuptools.package-dir]`. Rationale: a `uv`
workspace with 5 members adds configuration surface without buying isolation at this stage.
Trade-off: one lockfile/venv for everything; acceptable at this size. Revisit if we split workers.

### D3. Sync SQLAlchemy 2.0 + Pydantic v2, Postgres in prod, SQLite in tests
Async ORM buys nothing for work that is CPU-bound backtesting; FastAPI runs sync deps in a
threadpool. Tests use a temp SQLite file, so no external service is needed for CI. JSONB-heavy
payloads use `JSON` with a Postgres JSONB variant so both dialects work.

### D4. Deterministic offline mode is a first-class path
`LLM_PROVIDER=mock` yields a scripted but genuine research loop: the orchestrator still calls
tools, runs real backtests and real validation. This makes demos reproducible, tests hermetic, and
the noise gate meaningful without spending tokens. Production sets `LLM_PROVIDER=anthropic`.

### D5. Auth is a deliberate stub, structured for replacement
Local users (argon2-grade PBKDF2-SHA256, 600k iterations), JWT access tokens, hashed API keys,
org scoping, in-memory token-bucket rate limits. Clerk/Auth0 is a config swap (`AUTH_MODE`), not a
rewrite: all identity resolution funnels through `api.security`. Multi-tenant isolation is enforced
by `org_id` on every table and every query (tested).

### D6. No live-trading code path exists
Paper trading only. `StrategyStatus.LIVE_READY` is reachable only through a human approval record and
has no execution adapter. This is a safety property, not a phase-2 TODO.

### D7. Engine strategy: adapters + native reference implementation
`vectorbt` is the primary production engine, but it is an optional extra (heavy numba dependency).
A NumPy/pandas `NativeAdapter` implements the same semantics (next-bar fills, costs, stops, sizing)
and is the adapter the test suite and CI noise gate run against. A third adapter (backtesting.py)
proves pluggability. All adapters share the same `StrategySpec` and `BacktestResult`, so validation
code never knows which engine produced a result.

### D8. Global trial counter lives in the database, not in a process
DSR/PBO are meaningless if N is undercounted. Every grid point, WFO fold candidate, skeptic rerun and
agent-requested variant increments one org-scoped counter row (`trial_counter`) inside the same
transaction that records the experiment. A process-local fallback exists for pure-library use and is
explicitly reported in the validation payload as `trial_source: "memory"`.

### D9. Research-only product
Every API response that carries a strategy includes `disclaimer`; the UI shows it in the footer and
on each tearsheet. No performance claim is phrased as a forecast anywhere in the copy.
