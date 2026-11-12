"""FastAPI dependencies: database session and tenant-scoped principal."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.db import get_db
from api.models import ApiKey, User
from api.security import Principal, bearer_token, decode_access_token, hash_api_key


def get_principal(
    request: Request,
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> Principal:
    token = bearer_token(authorization)
    if token:
        payload = decode_access_token(token)
        user = db.get(User, str(payload.get("sub")))
        if user is None or not user.is_active:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user not found")
        return Principal(
            user_id=user.id,
            org_id=user.org_id,
            email=user.email,
            role=user.role,
            via="jwt",
        )

    if x_api_key:
        hashed = hash_api_key(x_api_key.strip())
        record = db.execute(select(ApiKey).where(ApiKey.hashed_key == hashed)).scalar_one_or_none()
        if record is None or record.revoked:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid API key")
        user = db.get(User, record.user_id)
        if user is None or not user.is_active:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user not found")
        record.last_used_at = datetime.now(UTC)
        db.commit()
        return Principal(
            user_id=user.id,
            org_id=user.org_id,
            email=user.email,
            role=user.role,
            via="api_key",
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="authentication required: Bearer token or X-API-Key",
        headers={"WWW-Authenticate": "Bearer"},
    )


def can_write(principal: Principal) -> bool:
    return principal.role in {"owner", "admin", "analyst"}


__all__ = ["can_write", "get_db", "get_principal"]
