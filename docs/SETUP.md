# Setup

## Requirements

- Python 3.11+ (3.12 used in images)
- [uv](https://docs.astral.sh/uv/) for Python dependencies
- Node 20+ and npm for the web app
- Docker (optional but recommended for Postgres/Redis and the sandbox)
- No paid data or LLM key required: `LLM_PROVIDER=mock` and free providers are first-class

## Local, step by step

```bash
cp .env.example .env
uv venv --python 3.11
uv pip install -e ".[dev]"
npm --prefix apps/web install
```

Start the API:

```bash
uv run uvicorn api.main:app --reload --port 8000
# schema is created idempotently on startup
```

The default `DATABASE_URL` is SQLite for development. For Postgres:

```bash
docker compose up -d postgres redis
export DATABASE_URL='postgresql+psycopg://nullpoint:nullpoint@localhost:5432/nullpoint'
```

Start the worker (production-like queue) or use `JOB_RUNNER=sync` for a single process:

```bash
uv run arq api.jobs.WorkerSettings
```

Start the web app:

```bash
npm --prefix apps/web run dev
# http://localhost:3000 — demo mode until you sign in
```

Register at `/login` (or `POST /auth/register`), then launch a sprint from **Agent sprints**.

## Sandbox

The Docker sandbox requires a running Docker daemon:

```bash
export SANDBOX_BACKEND=docker
```

Without Docker, `local` is used (development only). Never expose the local backend to untrusted users.

## Data

The lake is `LAKE_ROOT` (default `./artifacts/lake`). First sprint downloads bars through Yahoo/Stooq
and caches Parquet. To work offline:

```bash
export ALLOW_NETWORK=false
python scripts/demo_sprint.py --synthetic
```

Synthetic runs are labelled in the API, ledger, tearsheet and UI.

## Optional extras

```bash
uv pip install -e ".[vbt]"     # vectorbt engine adapter
uv pip install -e ".[bta]"     # backtesting.py adapter
uv pip install -e ".[llm]"     # Anthropic SDK (other LLM providers need no SDK)
uv pip install -e ".[pdf]"     # PDF tearsheets (optional path)
```

## Research providers

Every integration below is optional and degrades honestly when absent:

```bash
# Any OpenAI-compatible LLM (or use LLM_PROVIDER=anthropic)
export LLM_PROVIDER=openrouter
# or: openai | groq | deepseek | mistral | xai | together | fireworks | gemini | ollama
export LLM_API_KEY=...
# export LLM_BASE_URL=http://localhost:8000/v1   # for custom/vLLM/LM Studio/Azure

export VOYAGE_API_KEY=...       # Voyage knowledge-base embeddings; local fallback

export TAVILY_API_KEY=...       # web research providers; use any subset
export EXA_API_KEY=...
export FIRECRAWL_API_KEY=...

# cap grid/download concurrency; 0 = auto CPU/cgroup detection
export MAX_WORKERS=4
```

Run `POST /sprints` as usual. The Ideator now queries the knowledge base and deep-research
providers before proposing ideas; citations appear in the sprint audit trail. Without keys the
sprint still runs with the deterministic template library and local embeddings.

## Checks

```bash
make lint
make type
make test
make cov
python -m pytest tests/test_noise_gate.py -q
npm --prefix apps/web run build
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Sprint ends `failed` immediately | Read `/sprints/{id}` `error`, then `artifacts/jobs/*.log`. Usually data download or missing column. |
| Results say `synthetic: true` | Network disabled or providers failed. Expected offline behaviour, not market evidence. |
| `EngineUnavailable` | Optional adapter not installed; use the native engine or install the extra. |
| Worker does nothing | Redis unreachable. Check `REDIS_URL`; use `JOB_RUNNER=subprocess` as a fallback. |
| Web shows demo data after login | Token missing/expired, or API proxy cannot reach `API_INTERNAL_URL`. |
| Sandbox scanner rejects code | The AST allowlist rejected the code; inspect the strategy error. Do not disable the scanner. |
