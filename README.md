# Nullpoint

**Nothing ships until it beats the null.**

Nullpoint is a multi-agent autonomous quant research platform. Ten specialised agents propose,
implement, backtest and then try to falsify systematic strategies on free data. A strategy reaches the
leaderboard only after it survives an explicit anti-overfitting gauntlet, and nothing can place a live
order — that code path does not exist.

Research software only. Not investment advice, not a signal service, not a broker.

![Landing page](files/nullpoint-hero.png)

## What it does

- **10 agents, one typed bus.** Orchestrator, ideator, quant, engineer, backtester, skeptic, risk
  officer, librarian, portfolio manager, auditor. Every message, tool call and token is persisted and
  replayable.
- **Falsification, not curve fitting.** Walk-forward (anchored/rolling), purged + embargoed K-fold,
  CPCV, PBO/CSCV, deflated Sharpe against a global trial counter, Monte Carlo trade/block bootstrap,
  random-entry and signal-shuffle nulls, cost 2×/3× stress, execution delay, regime coverage and a
  sealed holdout.
- **True optimization.** Exhaustive pre-registered grids for PBO/plateau, plus TRIBES (adaptive,
  parameter-free particle swarm) re-fitted on every walk-forward train window with warm-up history.
- **Cost-aware engine.** Native NumPy/pandas reference implementation with next-bar fills, commission,
  spread, slippage, borrow, stops, position caps and vol targeting. `vectorbt` and `backtesting.py`
  adapters are optional extras.
- **Free data, honest labels.** Yahoo, Stooq, FRED, SEC EDGAR, Fama-French, Binance/ccxt, plus optional
  Alpha Vantage/Tiingo/Polygon/Alpaca keys. Every provider carries its licence note; every universe
  carries its survivorship-bias warning; synthetic fallback data is labelled at every layer.
- **Registry and portfolio.** Strategy versioning, lineage, diff, embedding dedupe, experiment ledger
  with data/code hashes, HRP / risk parity / inverse-vol / mean-variance construction, paper trading
  with drift, alerts, cost dashboard.
- **Product surface.** Next.js 15 app (dark-first brand, demo mode), FastAPI with JWT + API keys,
  Arq worker, Postgres, Parquet/DuckDB lake, Prometheus metrics, Docker Compose, CI.

## Quickstart

### Docker (full stack)

```bash
cp .env.example .env
docker compose up --build
# web  → http://localhost:3000
# api  → http://localhost:8000/docs
```

### Local

```bash
uv sync --extra dev
npm --prefix apps/web install
cp .env.example .env

make api      # http://localhost:8000/docs
make worker   # Arq worker (sprint queue + nightly drift)
make web      # http://localhost:3000
```

No Anthropic key? Set `LLM_PROVIDER=mock` (the default in tests). The mock provider runs the real
tools, backtests and validation gates deterministically.

### One command demo

```bash
python scripts/demo_sprint.py --synthetic
```

This creates a demo org (`demo@nullpoint.local` / `nullpoint-demo`), runs a complete sprint offline,
and prints the sprint id, trials, spend, strategies and tearsheet paths. Without `--synthetic` it tries
Yahoo/Stooq first and labels the dataset accordingly.

```bash
make seed           # demo org + ideas + sprint + portfolio
make tearsheet      # re-render standalone HTML tearsheets into artifacts/
make demo           # sprint on SPY/QQQ + sector ETFs
```

### Web without the API

The UI runs in **demo mode** when no token is present: deterministic, labelled demo data, all pages and
charts usable. Sign in at `/login` after starting the API to use real sprints.

## Repository layout

```
apps/api/          FastAPI app (routers, services, jobs, models, security)
apps/web/          Next.js 15 app (brand, pages, charts, API client, demo data)
engine/            backtest engine, features, metrics, portfolio, validation gauntlet
agents/            roster, orchestrator, bus, tools, templates, tearsheets, embeddings
data/              providers, quality checks, Parquet/DuckDB lake, universes, pipeline
sandbox/           AST scanner + Docker/subprocess runners for untrusted agent code
brand/             NAMES.md, BRAND.md, palette, logo/wordmark/favicon/OG SVGs
docs/              ARCHITECTURE, API, SECURITY, DATA_LICENSES, SETUP, DEMO, DECISIONS
infra/             Fly.io / Railway deployment configs and checklist
scripts/           demo sprint, seed, tearsheet re-render
tests/             engine, validation, noise gate, data, agents, API
```

## Validation gates

Configured in `engine/validation.py` (`GATES`) and applied to every candidate:

<!-- gates:start -->
| Gate | Threshold | Critical | Source key |
|---|---|:---:|---|
| Walk-forward OOS Sharpe | >= 0.5 | yes | `oos_sharpe_min` |
| WFO efficiency (OOS / IS) | >= 0.4 |  | `wfo_efficiency_min` |
| CPCV median OOS Sharpe (train-only params) | >= 0.3 | yes | `cpcv_median_sharpe_min` |
| PBO / CSCV | <= 0.35 | yes | `pbo_max` |
| Deflated Sharpe (per-period units, effective trials) | >= 0.9 | yes | `dsr_min` |
| Block-bootstrap P(SR <= 0) | <= 0.1 | yes | `mc_prob_le_zero_max` |
| Random-entry null p-value | <= 0.1 |  | `random_entry_p_max` |
| Placebo shuffle p-value (same costs/turnover) | <= 0.1 | yes | `signal_shuffle_p_max` |
| Sharpe at 3x costs, +1 bar | >= 0.3 | yes | `cost_3x_sharpe_min` |
| Sharpe with +1 extra bar delay | >= 0.3 |  | `delay_sharpe_min` |
| Parameter plateau share | >= 0.4 |  | `plateau_share_min` |
| Minimum track record | >= 1 years and >= 20 trades | yes | `min_trades` |
| Worst regime Sharpe (60+ days, >= 4 regimes) | >= -0.25 | yes | `regime_min_sharpe` |
| Sealed holdout | one-time, human-only, logged before opening | yes | policy |

Generated from `engine/validation.py` by `scripts/render_gates.py`. Do not edit this table by hand.
<!-- gates:end -->

One failed gate blocks promotion. Rejections are stored with a tearsheet; deleting them would make the
trial count lie.

## Status ladder and safety

`idea → backtested → validated → candidate → paper → live_ready`

`live_ready` is a **label for human review**, not an execution state. There is no broker order code
anywhere in the repository. Paper trading requires a stored human approval record; the simulator
bootstraps the strategy's own out-of-sample returns and is labelled simulated. Agent code executes in
a sandbox (no network, read-only mounts, CPU/RAM/PID/wall-clock limits, AST method allowlist). The
Docker backend is used whenever available and production fails closed if it is not; the local
subprocess runner is development-only.

## Evidence gates after validation

A passing validation report is not a performance claim. `GET /strategies/{id}/claim` withholds all
headline metrics until three things exist:

1. the latest validation report passed;
2. a human opened the sealed holdout exactly once and its metrics were recorded
   (`holdout_evaluation` is uniquely constrained by strategy version);
3. a paper run has at least `MIN_PAPER_DAYS` distinct forward days and no active drift alert.

Simulator paper results are labelled synthetic. The gate returns explicit blockers instead of
pretending missing evidence is a pass.

## Operations

- **Migrations.** Alembic owns the schema in staging/production (`alembic upgrade head`);
  `alembic check` fails on model drift. `AUTO_CREATE_SCHEMA` is for dev/test only.
- **Sandboxed backtests.** With `ENGINE_SANDBOX=true`, the native engine runs in the sandbox
  (`engine/sandbox_adapter.py`) instead of compiling agent code in the worker. Production refuses
  to boot with it disabled. Build the image with `make sandbox-image`.
- **Durable jobs.** Every worker attempt is recorded in `job_attempt`. A sprint whose heartbeat is
  stale is requeued up to `JOB_MAX_ATTEMPTS`; deterministic failures are not retried automatically.
- **Typed research artifacts.** Proposals are frozen with a hypothesis, falsifier, critique and trial
  plan before code runs; the funnel prevents duplicate pre-registration and over-budget trials.
- **Staging.** `infra/fly.staging.toml` is a separate app/database/Redis/lake configuration.

## Quality gates

```bash
make check     # ruff + mypy + pytest with coverage
make cov       # engine/validation coverage must stay ≥ 80% (currently ~87%)
python -m pytest tests/test_noise_gate.py -q   # noise must never be promoted
```

CI runs lint, typecheck, full tests, the engine coverage gate, the noise gate and a production web
build.

## Configuration

See `.env.example`. Important variables:

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | sqlite in dev | Postgres in production (`postgresql+psycopg://…`) |
| `LLM_PROVIDER` | `mock` | `mock` or `anthropic` |
| `MODEL_ORCHESTRATOR/WORKER/SKEPTIC/CHEAP` | Claude models | model slots per role |
| `JOB_RUNNER` | `auto` | `arq`, `subprocess` or `sync` (tests only) |
| `ALLOW_NETWORK` | `true` | false forces the labelled synthetic path |
| `LAKE_ROOT` | `./artifacts/lake` | Parquet lake |
| `SANDBOX_BACKEND` | `auto` | `docker` when available; production refuses `local` |
| `ENGINE_SANDBOX` | `false` | run every backtest in the sandbox; production must be `true` |
| `AUTO_CREATE_SCHEMA` | `true` | dev/test only; staging/production use Alembic |
| `JOB_MAX_ATTEMPTS` | `3` | stale-worker requeue budget |
| `MIN_PAPER_DAYS` | `30` | distinct forward days required before a claim |
| `KILL_SWITCH` | `false` | stop new sprints immediately |
| `ANTHROPIC_API_KEY` | — | required only for `LLM_PROVIDER=anthropic` |

Secrets belong in the environment or your platform's secret store, never in the UI or repository.

## Deploy

`infra/DEPLOY.md` covers Fly.io (API + worker + web), Railway and managed Postgres/Redis. The API and
worker share one image; the web image uses Next.js standalone output. `docker-compose.yml` is the
reference topology. Deployment is intentionally manual: this repository does not deploy itself.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [API](docs/API.md)
- [Security](docs/SECURITY.md)
- [Data licences and bias](docs/DATA_LICENSES.md)
- [Setup](docs/SETUP.md)
- [Demo](docs/DEMO.md)
- [Decisions](docs/DECISIONS.md)

## Limitations

- Yahoo Finance via `yfinance` is personal, non-commercial data and is not survivorship-free or
  point-in-time. The lake records provider, fetch time and content hashes, and dataset metadata
  exposes `point_in_time` / `survivorship_free` flags with a bias note. The bias is flagged, not
  solved: there is no delisted-constituent reconstruction yet.
- The native engine is the reference; vectorbt is covered by a parity test and the SPY walk-forward,
  but not by the full engine coverage gate.
- Docker execution is implemented, but the container path is not exercised in the development
  environment (the daemon is permission-denied). The local subprocess backend has a native-parity
  test; build `nullpoint-sandbox:latest` and verify a staging sprint before relying on it.
- LLM cost figures are estimates from published token prices, not invoices.
- Paper trading is simulation-first. Alpaca integration is read-only and restricted to paper endpoints.
- Multi-tenant isolation, auth and rate limits are implemented, but this is not a hardened
  internet-facing trading system; review `docs/SECURITY.md` before exposing it publicly.

---

Nullpoint · research only · not investment advice · backtests are hypotheses, not forecasts.
