"""FastAPI application entry point."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from api.config import settings
from api.db import engine, init_db
from api.routers import auth, data, portfolio, research, strategies, system
from api.routers.system import REQUESTS

log = structlog.get_logger("nullpoint.api")

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(0),
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    init_db()
    if settings.sentry_dsn:
        try:
            import sentry_sdk

            sentry_sdk.init(dsn=settings.sentry_dsn.get_secret_value(), traces_sample_rate=0.1)
        except Exception:
            log.warning("sentry_init_failed")
    log.info("api_started", env=settings.app_env, llm=settings.llm_provider)
    yield
    engine.dispose()
    log.info("api_stopped")


app = FastAPI(
    title="Nullpoint API",
    version="0.1.0",
    description=(
        "Multi-agent autonomous quant research. Nothing ships until it beats the null. "
        "Research only — not investment advice, and no live-trading endpoints exist."
    ),
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Request-ID"],
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("X-Request-ID", getattr(request.state, "request_id", ""))
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        path = request.url.path
        if path in {"/health", "/ready", "/metrics", "/docs", "/openapi.json"}:
            return await call_next(request)
        from api.security import RATE_LIMITER

        key = (
            request.headers.get("x-api-key")
            or request.headers.get("authorization")
            or (request.client.host if request.client else "anonymous")
        )
        if not RATE_LIMITER.allow(str(key)[:120]):
            return JSONResponse(
                status_code=429,
                content={"detail": "rate limit exceeded; slow down or use your API key"},
                headers={"Retry-After": "60"},
            )
        return await call_next(request)


class TelemetryMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000
        route = request.scope.get("route")
        path = getattr(route, "path", request.url.path)
        REQUESTS.labels(method=request.method, path=path, status=str(response.status_code)).inc()
        log.info(
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=round(elapsed_ms, 1),
            request_id=request.state.request_id,
        )
        return response


app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(TelemetryMiddleware)

app.include_router(system.router)
app.include_router(auth.router)
app.include_router(research.router)
app.include_router(strategies.router)
app.include_router(data.router)
app.include_router(portfolio.router)


@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    log.error(
        "unhandled_exception",
        path=request.url.path,
        error=f"{type(exc).__name__}: {exc}",
        request_id=getattr(request.state, "request_id", None),
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": "internal error; the request was logged with an id",
            "request_id": getattr(request.state, "request_id", None),
        },
    )


@app.get("/")
def root() -> dict:
    return {
        "brand": "Nullpoint",
        "tagline": "Nothing ships until it beats the null.",
        "version": "0.1.0",
        "docs": "/docs",
        "health": "/health",
        "disclaimer": "Research only. Not investment advice. No live-trading endpoints exist.",
        "endpoints": {
            "sprints": "/sprints",
            "stream": "/sprints/{id}/stream",
            "leaderboard": "/leaderboard",
            "dashboard": "/dashboard",
            "agents": "/agents",
            "data": "/data/providers",
            "settings": "/settings",
        },
    }
