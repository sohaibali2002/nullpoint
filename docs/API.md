# API

Base URL `/`. Interactive schema at `/docs`, OpenAPI at `/openapi.json`.

Auth: `Authorization: Bearer <jwt>` or `X-API-Key: np_…`. Tenant is derived from the credential; there
is no org parameter. Errors use `{"detail": "..."}` with conventional status codes (`401`, `403`,
`404`, `409`, `422`, `429`, `500`). 500 responses include a `request_id`; stack traces stay in logs.

## Auth

| Method | Path | Notes |
|---|---|---|
| POST | `/auth/register` | `{org_name,email,password}` → token + ids; creates owner |
| POST | `/auth/login` | `{email,password}` → token |
| GET | `/auth/me` | Current user/org/role |
| POST | `/auth/api-keys` | `{name}` → raw key once |
| GET | `/auth/api-keys` | Prefix and metadata only |
| DELETE | `/auth/api-keys/{id}` | Revoke |

## Research

| Method | Path | Notes |
|---|---|---|
| POST | `/sprints` | `{objective, universe[], max_strategies, start?, end?, budget_usd?}` → 202 |
| GET | `/sprints` | Recent runs |
| GET | `/sprints/{id}` | Sprint + its strategies |
| POST | `/sprints/{id}/kill` | Sets kill flag; worker stops at next checkpoint |
| GET | `/sprints/{id}/messages?after=` | Persisted audit trail |
| GET | `/sprints/{id}/stream?after=` | SSE (`message`, `done`, `timeout`) |
| GET | `/sprints/{id}/replay` | Full ordered messages |
| POST | `/sprints/from-chat` | `{message}` → parsed universe + sprint |
| GET | `/dashboard` | Counts, recent sprints, leaderboard, roster, safety flags |
| GET | `/agents` | Roster + model slots |
| GET | `/ideas` · POST `/ideas` | List / record with embedding dedupe warning |

## Strategies & experiments

| Method | Path | Notes |
|---|---|---|
| GET | `/strategies?status=&sort=&limit=` | Registry |
| GET | `/leaderboard` | Promoted only, ranked by visible Sharpe |
| GET | `/strategies/{id}` | Spec, validation, experiments, lineage, approvals |
| GET | `/strategies/{id}/validation` | Latest report with every gate |
| GET | `/strategies/{id}/experiments` | Ledger entries |
| GET | `/strategies/{id}/tearsheet` | HTML (404 if not generated) |
| GET | `/strategies/{id}/export?format=python|json|pine` | Honest, approximate Pine |
| GET | `/strategies/{id}/diff/{other}` | Spec differences |
| POST | `/strategies/{id}/approve` | `{target:"paper"|"live_ready", note}`; live_ready is a label and is refused 400 |
| GET | `/experiments?strategy_id=` | Ledger |
| GET | `/experiments/{id}?include_series=true` | Returns/equity/dates |

## Portfolio & paper

| Method | Path | Notes |
|---|---|---|
| GET/POST | `/portfolios` | POST `{strategy_ids[2..20], method, max_weight}` |
| GET | `/paper` | Paper runs summary + drift |
| POST | `/paper` | `{strategy_id, initial_equity}`; requires `paper` status |
| GET | `/paper/{run_id}` | Curve, fills, drift |
| POST | `/paper/{run_id}/mark?steps=` | Advance simulator (max 60) |
| POST | `/paper/{run_id}/stop` | Stop run |
| GET | `/paper/alpaca/account` | Read-only paper account, if configured |

## Data

| Method | Path | Notes |
|---|---|---|
| GET | `/data/providers` | Licence, classes, rate limit, availability |
| GET | `/data/universes` | Symbols + survivorship warnings |
| GET | `/data/assets` | Cached assets with hashes and quality |
| GET | `/data/preview?symbol=&provider=` | Cached bars only |
| POST | `/data/query` | Admin, SELECT-only DuckDB over the lake |

## System

| Method | Path | Notes |
|---|---|---|
| GET | `/health` · `/ready` · `/metrics` | Liveness, readiness, Prometheus |
| GET/PATCH | `/settings` | PATCH: budgets, model slots, alert prefs (admin) |
| GET | `/usage` | 30-day spend by model, trials, cost/candidate |
| GET | `/alerts` · POST `/alerts/test` | Delivery log; unconfigured channels are recorded skipped |
| GET | `/safety` | Capabilities and limits |
| GET | `/models` | Configured slots and budget prices |
| GET | `/` | Brand, version, endpoint map, disclaimer |

## Examples

```bash
TOKEN=$(curl -s localhost:8000/auth/register -H 'content-type: application/json' \
  -d '{"org_name":"Desk","email":"a@b.co","password":"correct-horse"}' | jq -r .access_token)

curl -s localhost:8000/sprints -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"objective":"Test momentum after 3x costs","universe":["SPY","QQQ"],"max_strategies":1}' | jq

curl -sN "localhost:8000/sprints/<id>/stream?after=0" -H "authorization: Bearer $TOKEN"
```

The API never returns provider secrets, agent stack traces or another tenant's data. Rate limits apply
per credential (or IP) and return `429` with `Retry-After`.
