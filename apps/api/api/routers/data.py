"""Data explorer: providers, licences, universes, lake assets, previews and safe SQL."""

from __future__ import annotations

from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.config import settings
from api.db import get_db
from api.deps import get_principal
from api.models import DataAsset
from api.schemas import QueryIn
from api.security import Principal, require_admin
from data.lake import DataLake
from data.providers import get_provider, provider_catalog
from data.universe import UNIVERSES, universe_notice

router = APIRouter(prefix="/data", tags=["data"])


@router.get("/providers")
def providers(principal: Principal = Depends(get_principal)) -> dict[str, Any]:
    catalog = provider_catalog()
    for entry in catalog:
        try:
            provider = get_provider(entry["name"])
            entry["available"] = bool(provider.available())
        except Exception:
            entry["available"] = False
    return {
        "providers": catalog,
        "policy": "free tiers only; check each licence before commercial use",
        "warning": "yfinance/Yahoo endpoints are personal-use only. Do not redistribute.",
    }


@router.get("/universes")
def universes(principal: Principal = Depends(get_principal)) -> list[dict[str, Any]]:
    return [universe_notice(name) for name in UNIVERSES]


@router.get("/assets")
def assets(
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> list[dict[str, Any]]:
    rows = db.execute(
        select(DataAsset)
        .where(DataAsset.org_id == principal.org_id)
        .order_by(DataAsset.fetched_at.desc())
        .limit(limit)
    ).scalars()
    return [
        {
            "id": row.id,
            "provider": row.provider,
            "symbol": row.symbol,
            "interval": row.interval,
            "start": row.start,
            "end": row.end,
            "rows": row.rows,
            "content_hash": row.content_hash,
            "synthetic": row.synthetic,
            "quality": row.quality,
            "fetched_at": row.fetched_at.isoformat() if row.fetched_at else None,
        }
        for row in rows
    ]


@router.get("/preview")
def preview(
    symbol: str = Query(default="SPY", max_length=40),
    provider: str = Query(default="yahoo", max_length=40),
    limit: int = Query(default=120, ge=10, le=2000),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    lake = DataLake(settings.lake_root)
    cached = lake.read_bars(provider, symbol.upper())
    if cached is None:
        raise HTTPException(
            status_code=404,
            detail=f"no cached {provider} bars for {symbol}. Run a sprint or seed the lake first.",
        )
    frame, meta = cached
    tail = frame.tail(limit)
    return {
        "symbol": symbol.upper(),
        "provider": provider,
        "meta": meta,
        "columns": list(tail.columns),
        "rows": [
            {
                "date": str(pd.Timestamp(str(index)).date()),
                **{k: (None if pd.isna(value) else float(value)) for k, value in row.items()},
            }
            for index, row in tail.drop(columns=["available_at"], errors="ignore").iterrows()
        ],
        "note": "Cached free data; check /data/providers for licence constraints.",
    }


@router.post("/query")
def query(
    payload: QueryIn,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    require_admin(principal)
    lake = DataLake(settings.lake_root, read_only=True)
    try:
        frame = lake.query(payload.sql, limit=payload.limit)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"query rejected: {exc}") from exc
    return {
        "columns": list(frame.columns),
        "rows": frame.replace({float("nan"): None}).to_dict(orient="records"),
        "row_count": len(frame),
        "policy": "SELECT-only over the local Parquet lake; no writes are possible",
    }
