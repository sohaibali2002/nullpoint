"""Settings, cost dashboard, alerts, observability and safety surface."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from agents.llm import PRICING
from api.config import settings
from api.db import get_db
from api.deps import get_principal
from api.models import (
    AlertEvent,
    LLMUsage,
    Organization,
    Setting,
    Strategy,
    TrialCounter,
)
from api.schemas import SettingsPatch
from api.security import Principal, require_admin, safe_secret
from api.services.alerts import dispatch
from data.providers import provider_catalog
from engine.base import list_adapters

router = APIRouter(tags=["system"])
log = structlog.get_logger("nullpoint.api")

REQUESTS = Counter("nullpoint_requests_total", "HTTP requests", ["method", "path", "status"])


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict[str, Any]:
    checks = {"database": "ok", "redis": "unknown", "sandbox": os.environ.get("NULLPOINT_SANDBOX_BACKEND", "local")}
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        checks["database"] = "error"
    try:
        import redis

        client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=0.4, socket_timeout=0.4)
        checks["redis"] = "ok" if client.ping() else "error"
    except Exception:
        checks["redis"] = "unavailable (dev fallback: subprocess jobs)"
    healthy = checks["database"] == "ok"
    return {
        "status": "ok" if healthy else "degraded",
        "service": settings.app_name,
        "version": "0.1.0",
        "env": settings.app_env,
        "checks": checks,
        "safety": {
            "live_trading_enabled": False,
            "kill_switch": settings.kill_switch,
        },
        "time": datetime.now(UTC).isoformat(),
    }


@router.get("/ready")
def ready(db: Session = Depends(get_db)) -> Response:
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"database not ready: {exc}") from exc
    return Response(status_code=200, content="ready")


@router.get("/metrics")
def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.get("/models")
def models(principal: Principal = Depends(get_principal)) -> dict[str, Any]:
    return {
        "provider": settings.llm_provider,
        "configured": {
            "orchestrator": settings.model_orchestrator,
            "worker": settings.model_worker,
            "skeptic": settings.model_skeptic,
            "cheap": settings.model_cheap,
        },
        "pricing_usd_per_million": {
            model: {"input": price[0], "output": price[1]} for model, price in PRICING.items()
        },
        "note": "Prices are budget estimates, not billing records. Override models with env variables.",
    }


@router.get("/settings")
def get_settings(
    db: Session = Depends(get_db), principal: Principal = Depends(get_principal)
) -> dict[str, Any]:
    org = db.get(Organization, principal.org_id)
    setting = db.execute(select(Setting).where(Setting.org_id == principal.org_id)).scalar_one_or_none()
    return {
        "org": {
            "id": principal.org_id,
            "name": org.name if org else None,
            "plan": org.plan if org else None,
            "monthly_budget_usd": org.monthly_budget_usd if org else None,
        },
        "runtime": {
            "sprint_budget_usd": settings.sprint_budget_usd,
            "sprint_wall_clock_minutes": settings.sprint_wall_clock_minutes,
            "max_agent_iterations": settings.max_agent_iterations,
            "llm_provider": settings.llm_provider,
            "job_runner": settings.job_runner,
            "allow_network": settings.allow_network,
            "sandbox_backend": os.environ.get("NULLPOINT_SANDBOX_BACKEND", "local"),
            "kill_switch": settings.kill_switch,
            "live_trading_enabled": False,
        },
        "models": {
            "orchestrator": settings.model_orchestrator,
            "worker": settings.model_worker,
            "skeptic": settings.model_skeptic,
            "cheap": settings.model_cheap,
        },
        "preferences": setting.payload if setting else {},
        "keys": {
            "anthropic": safe_secret(settings.anthropic_api_key),
            "fred": safe_secret(settings.fred_api_key),
            "alpha_vantage": safe_secret(settings.alphavantage_api_key),
            "tiingo": safe_secret(settings.tiingo_api_key),
            "polygon": safe_secret(settings.polygon_api_key),
            "alpaca": safe_secret(settings.alpaca_api_key),
            "slack": safe_secret(settings.slack_webhook_url),
            "discord": safe_secret(settings.discord_webhook_url),
            "sentry": safe_secret(settings.sentry_dsn),
        },
        "adapters": list_adapters(),
        "ignored_secrets": "Keys are never returned. Set them in the server environment, never in the UI.",
    }


@router.patch("/settings")
def patch_settings(
    payload: SettingsPatch,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    require_admin(principal)
    org = db.get(Organization, principal.org_id)
    if org is None:
        raise HTTPException(status_code=400, detail="organization missing")
    if payload.monthly_budget_usd is not None:
        org.monthly_budget_usd = payload.monthly_budget_usd
    if payload.sprint_budget_usd is not None:
        settings.sprint_budget_usd = payload.sprint_budget_usd
    for field in ("model_orchestrator", "model_worker", "model_skeptic", "model_cheap"):
        value = getattr(payload, field)
        if value:
            setattr(settings, field, value)
    setting = db.execute(select(Setting).where(Setting.org_id == principal.org_id)).scalar_one_or_none()
    preferences = dict(setting.payload if setting else {})
    for field in ("nightly_enabled", "alert_slack", "alert_email"):
        value = getattr(payload, field)
        if value is not None:
            preferences[field] = value
    if setting is None:
        setting = Setting(org_id=principal.org_id, payload=preferences)
        db.add(setting)
    else:
        setting.payload = preferences
    db.commit()
    return {
        "updated": True,
        "preferences": preferences,
        "note": "Model overrides apply to new jobs; the worker process may need a restart.",
    }


@router.get("/usage")
def usage(
    db: Session = Depends(get_db), principal: Principal = Depends(get_principal)
) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(days=30)
    rows = db.execute(
        select(LLMUsage.model, func.sum(LLMUsage.cost_usd), func.sum(LLMUsage.tokens_in + LLMUsage.tokens_out))
        .where(LLMUsage.org_id == principal.org_id, LLMUsage.created_at >= since)
        .group_by(LLMUsage.model)
    ).all()
    total = float(sum(row[1] or 0.0 for row in rows))
    candidates = db.execute(
        select(func.count(Strategy.id)).where(
            Strategy.org_id == principal.org_id,
            Strategy.status.in_(["validated", "candidate", "paper"]),
        )
    ).scalar_one()
    trials = db.execute(
        select(func.coalesce(func.sum(TrialCounter.count), 0)).where(TrialCounter.org_id == principal.org_id)
    ).scalar_one()
    org = db.get(Organization, principal.org_id)
    return {
        "window_days": 30,
        "by_model": [
            {"model": row[0], "cost_usd": round(float(row[1] or 0.0), 6), "tokens": int(row[2] or 0)}
            for row in rows
        ],
        "total_llm_spend_usd": round(total, 6),
        "monthly_budget_usd": org.monthly_budget_usd if org else None,
        "budget_used_pct": round(total / org.monthly_budget_usd * 100, 2)
        if org and org.monthly_budget_usd
        else None,
        "trials": int(trials or 0),
        "validated_strategies": int(candidates or 0),
        "cost_per_validated_strategy_usd": round(total / int(candidates), 4) if candidates else None,
        "note": "Free-tier providers cost $0; LLM spend is the only metered cost in v0.",
    }


@router.get("/alerts")
def alerts(
    limit: int = 50, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)
) -> list[dict[str, Any]]:
    rows = db.execute(
        select(AlertEvent)
        .where(AlertEvent.org_id == principal.org_id)
        .order_by(AlertEvent.created_at.desc())
        .limit(limit)
    ).scalars()
    return [
        {
            "id": row.id,
            "channel": row.channel,
            "event": row.event,
            "subject": row.subject,
            "status": row.status,
            "error": row.error,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]


@router.post("/alerts/test")
def send_test_alert(
    db: Session = Depends(get_db), principal: Principal = Depends(get_principal)
) -> dict[str, Any]:
    require_admin(principal)
    results = dispatch(
        db,
        principal.org_id,
        "test",
        "Nullpoint alert test",
        "If you can read this, alert delivery is configured. Research only; not investment advice.",
    )
    return {"results": results, "note": "Unconfigured channels are recorded as skipped, not silently dropped."}


@router.get("/safety")
def safety() -> dict[str, Any]:
    return {
        "live_trading_enabled": False,
        "statement": "Nullpoint cannot place live orders. Strategies stop at CANDIDATE until a human approves paper trading.",
        "kill_switch": settings.kill_switch,
        "sealed_holdout": "agents never see the final 20% of the sample",
        "sandbox": {
            "backend": os.environ.get("NULLPOINT_SANDBOX_BACKEND", "local"),
            "network": "none (docker backend)",
            "limits": "CPU, memory, pids, wall clock, read-only mounts",
        },
        "providers": [p["name"] for p in provider_catalog()],
        "disclaimer": "Research only. Not investment advice. Backtests are not forecasts.",
    }
