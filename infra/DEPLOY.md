# Deployment checklist

Nullpoint does not deploy itself. These are manual steps; review them before running anything that
creates cloud resources. Use managed Postgres and Redis, TLS everywhere, and a private network between
API, worker and database.

## 0. Preconditions

- [ ] `AUTH_SECRET` is a long random value (not the example).
- [ ] `DATABASE_URL` points at managed Postgres with backups.
- [ ] `REDIS_URL` points at managed Redis (Arq queue + scheduler).
- [ ] `LLM_PROVIDER` and the matching key are set (or `mock` for a demo deployment).
- [ ] `CORS_ORIGINS` contains only the real web origin.
- [ ] `SANDBOX_BACKEND=docker` on the worker host, with Docker available.
- [ ] `AUTO_CREATE_SCHEMA=false`; the release command or manual step runs `alembic upgrade head`.
- [ ] `ENGINE_SANDBOX=true` and the worker can reach a Docker daemon.
- [ ] `nullpoint-sandbox:latest` is built from `sandbox/Dockerfile` on the worker host (or pulled
      from a registry); the backtest fails closed if the image is missing.
- [ ] Object storage or a persistent volume is mounted at `LAKE_ROOT`; the lake is not ephemeral.
- [ ] Secrets live in the platform secret store, never in the image or repository.
- [ ] `KILL_SWITCH` behaviour and on-call ownership are documented.

## 1. Fly.io

API + worker (`infra/fly.toml`), one image, two process groups. Staging uses
`infra/fly.staging.toml` with its own app, database, Redis and lake volume:

```bash
fly launch --no-deploy --config infra/fly.toml --name nullpoint-api
fly postgres create --name nullpoint-db
fly postgres attach nullpoint-db --app nullpoint-api
fly redis create
fly secrets set --app nullpoint-api AUTH_SECRET=... ANTHROPIC_API_KEY=... CORS_ORIGINS=https://app.example.com
fly volumes create lake --size 10 --app nullpoint-api
fly deploy --config infra/fly.toml
fly scale count api=1 worker=1 --app nullpoint-api
```

Web (`apps/web/fly.toml`):

```bash
cd apps/web
fly launch --no-deploy --config fly.toml --name nullpoint-web
fly secrets set --app nullpoint-web API_INTERNAL_URL=https://nullpoint-api.fly.dev
fly deploy
```

Set `NEXT_PUBLIC_API_URL` only if the browser must call the API directly; the default `/api` rewrite is
same-origin and preferred.

> **Sandbox host requirement.** Standard Fly Machines do not expose a Docker daemon. Run the worker
> on a host/VM with Docker access, or provide a verified Docker-in-Docker sidecar, before enabling
> `ENGINE_SANDBOX=true`. The worker fails closed if it cannot reach Docker.

## 2. Railway

- Create Postgres and Redis plugins.
- API service: Dockerfile `apps/api/Dockerfile`, start command
  `uvicorn api.main:app --host 0.0.0.0 --port $PORT`.
- Worker service: same image, start command `arq api.jobs.WorkerSettings`, no public domain.
- Web service: root `apps/web`, Dockerfile `Dockerfile`, set `API_INTERNAL_URL` to the API's private URL.
- Add a persistent volume for `/lake`.
- Reference: `infra/railway.api.json` (adapt service names to your project).

## 3. Database migrations

Production and staging are migration-owned: `AUTO_CREATE_SCHEMA=false`, and the Fly release command
runs `alembic upgrade head` before the new machines serve traffic. Apply migrations manually only when
the platform has no release command:

```bash
alembic upgrade head
# fail the deploy if the models have drifted from the migrations
alembic check
```

Create a migration after a model change with `make migrate-create m="describe change"`; review the
generated file before committing it. Never edit an already-applied revision.

A development database created before Alembic can be adopted without losing data:

```bash
alembic stamp 9697d791d47d   # mark the pre-Alembic baseline
alembic upgrade head         # apply the bridge revision (idempotent)
alembic check                # verify no drift
```

The bridge only adds missing objects; it never drops a table or column.

## 4. Post-deploy verification

- [ ] `GET /health` returns `status: ok`, database ok, `live_trading_enabled: false`.
- [ ] `GET /ready` returns 200.
- [ ] Register a user, launch a sprint, read messages, open a tearsheet.
- [ ] Second account cannot read the first account's sprint (404) or strategies (empty list).
- [ ] `POST /strategies/{id}/approve` with `live_ready` returns 400.
- [ ] Web loads over TLS, `/api/health` proxies, no console errors, demo badge absent when signed in.
- [ ] Prometheus scrapes `/metrics`; logs include request ids.
- [ ] Nightly scheduler and drift jobs appear in worker logs.
- [ ] Restore drill: database backup restores, lake volume reattaches, strategy pages still render.

## 5. Rollback

Deploy the previous image tag, keep the database backward compatible, and do not delete the lake volume.
If a sprint is misbehaving, set `KILL_SWITCH=true` and restart the worker; persisted messages remain.
