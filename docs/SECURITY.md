# Security

## Authentication

- Email + password registration/login, bcrypt password hashes (`api/security.py`).
- JWT bearer tokens (HS256) with expiry. Production refuses to boot with the default development
  `SECRET_KEY`; set a unique 32+ character value.
- API keys are generated as `np_…`, shown once, stored as SHA-256 hashes with a short prefix for
  lookup. Revocation is a soft delete.
- Roles: `owner`, `admin`, `analyst`, `viewer`. Write operations require a non-viewer; settings and
  raw SQL require admin.

## Tenant isolation

Every tenant table has `org_id`. Helpers in `api/deps.py` resolve the principal from a token or key and
all router queries filter on `principal.org_id`. Object lookups by id always re-check ownership and
return 404 rather than leaking existence. There is no admin cross-tenant read endpoint.

## Agent code execution

Agent-written strategy code is untrusted.

- AST scan uses a method allowlist: imports outside the allowlist, `pd.read_*`, `np.fromfile`,
  `pd.io.*`/`np.lib.*` reach-through, dunder access, filesystem/network primitives and process
  spawning are rejected. Unknown methods fail closed.
- Execution happens in `sandbox/`, never in the API process. `SANDBOX_BACKEND=auto` selects Docker
  when it is available; production refuses to start with the local backend and fails closed if Docker
  is unavailable.
- With `ENGINE_SANDBOX=true`, agent-authored signals run through `engine/sandbox_adapter.py`, which
  sends the actual native backtest to the sandbox. Production refuses to start with it disabled.
- Docker backend: no network, read-only mounts, tmpfs workdir, CPU/memory/PID limits, wall-clock
  timeout, dropped capabilities.
- The local subprocess backend is a development fallback with reduced isolation; do not use it for
  multi-tenant production.
- The worker subprocess receives an allowlisted environment; API/LLM/broker secrets are not inherited.
- Outputs are parsed as JSON with size limits.

## Secrets

- Provider/LLM keys are read from the environment (pydantic settings, secret types) and never returned
  by the API. `/settings` exposes presence only (`safe_secret`).
- `.env` is gitignored; `.env.example` contains placeholders.
- Logs redact authorization headers and do not log message bodies containing keys.
- Frontend never receives provider credentials. It only holds the user's JWT in `localStorage`.

## API hardening

- CORS restricted to `CORS_ORIGINS`.
- Security headers: `X-Content-Type-Options`, `X-Frame-Options: DENY`, referrer policy, permissions
  policy, COOP.
- Per-key/IP token-bucket rate limit (`RATE_LIMITER`), 429 with `Retry-After`.
- Pydantic validation on every request body; symbol and SQL allowlists; read-only DuckDB queries.
- Generic 500 responses with a request id; stack traces stay server-side. Sentry optional.

## Known limitations

- No email verification, password reset, SSO or MFA yet.
- Rate limiting is in-process; use a Redis-backed limiter for multi-instance deployments.
- No CSRF tokens because auth is header-based (no cookies); do not add cookie auth without CSRF.
- The API is not a hardened internet-facing trading system. Put it behind TLS, a WAF and private
  networking, and review this document before exposing it publicly.
- No live-trading credentials are accepted by design.

## Reporting

This is a research project. If you deploy it, add a security contact and a disclosure policy appropriate
to your organisation.
