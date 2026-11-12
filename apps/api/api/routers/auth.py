"""Authentication and API-key management (local stub, replaceable by Clerk/Auth0)."""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.config import settings
from api.db import get_db
from api.deps import get_principal
from api.models import ApiKey, Organization, User
from api.schemas import (
    ApiKeyCreated,
    ApiKeyIn,
    ApiKeyOut,
    LoginIn,
    RegisterIn,
    TokenOut,
)
from api.security import (
    AUTH_LIMITER,
    Principal,
    create_access_token,
    generate_api_key,
    hash_password,
    require_admin,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _rate_limit(request: Request) -> None:
    key = request.client.host if request.client else "unknown"
    if not AUTH_LIMITER.allow(key):
        raise HTTPException(status_code=429, detail="too many authentication attempts")


def _slug(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:48] or "org"
    return f"{base}-{secrets.token_hex(3)}"


@router.post("/register", response_model=TokenOut, status_code=201)
def register(payload: RegisterIn, request: Request, db: Session = Depends(get_db)) -> TokenOut:
    _rate_limit(request)
    existing = db.execute(select(User).where(User.email == payload.email)).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="email already registered")
    org = Organization(name=payload.org_name.strip(), slug=_slug(payload.org_name))
    db.add(org)
    db.flush()
    user = User(
        org_id=org.id,
        email=payload.email,
        hashed_password=hash_password(payload.password),
        role="owner",
    )
    db.add(user)
    db.commit()
    token = create_access_token(user_id=user.id, org_id=org.id, role=user.role)
    return TokenOut(
        access_token=token,
        expires_in=settings.access_token_ttl_minutes * 60,
        org_id=org.id,
        user_id=user.id,
        role=user.role,
    )


@router.post("/login", response_model=TokenOut)
def login(payload: LoginIn, request: Request, db: Session = Depends(get_db)) -> TokenOut:
    _rate_limit(request)
    user = db.execute(select(User).where(User.email == payload.email.lower().strip())).scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.hashed_password) or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
    token = create_access_token(user_id=user.id, org_id=user.org_id, role=user.role)
    return TokenOut(
        access_token=token,
        expires_in=settings.access_token_ttl_minutes * 60,
        org_id=user.org_id,
        user_id=user.id,
        role=user.role,
    )


@router.get("/me")
def me(principal: Principal = Depends(get_principal), db: Session = Depends(get_db)) -> dict:
    org = db.get(Organization, principal.org_id)
    user = db.get(User, principal.user_id)
    return {
        "user_id": principal.user_id,
        "email": principal.email,
        "role": principal.role,
        "via": principal.via,
        "org": {
            "id": principal.org_id,
            "name": org.name if org else None,
            "slug": org.slug if org else None,
            "plan": org.plan if org else None,
            "monthly_budget_usd": org.monthly_budget_usd if org else None,
        },
        "created_at": user.created_at.isoformat() if user and user.created_at else None,
    }


@router.post("/api-keys", response_model=ApiKeyCreated, status_code=201)
def create_api_key(
    payload: ApiKeyIn,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> ApiKeyCreated:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="admin role required")
    full, prefix, hashed = generate_api_key()
    record = ApiKey(
        org_id=principal.org_id,
        user_id=principal.user_id,
        name=payload.name,
        prefix=prefix,
        hashed_key=hashed,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return ApiKeyCreated(
        id=record.id,
        name=record.name,
        prefix=record.prefix,
        created_at=record.created_at.isoformat() if record.created_at else None,
        key=full,
    )


@router.get("/api-keys", response_model=list[ApiKeyOut])
def list_api_keys(
    principal: Principal = Depends(get_principal), db: Session = Depends(get_db)
) -> list[ApiKeyOut]:
    rows = db.execute(
        select(ApiKey).where(ApiKey.org_id == principal.org_id).order_by(ApiKey.created_at.desc())
    ).scalars()
    return [
        ApiKeyOut(
            id=row.id,
            name=row.name,
            prefix=row.prefix,
            created_at=row.created_at.isoformat() if row.created_at else None,
            last_used_at=row.last_used_at.isoformat() if row.last_used_at else None,
        )
        for row in rows
    ]


@router.delete("/api-keys/{key_id}", status_code=204)
def revoke_api_key(
    key_id: str,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> None:
    require_admin(principal)
    record = db.get(ApiKey, key_id)
    if record is None or record.org_id != principal.org_id:
        raise HTTPException(status_code=404, detail="API key not found")
    record.revoked = True
    record.last_used_at = datetime.now(UTC)
    db.commit()
