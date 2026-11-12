"""Password hashing, JWT access tokens, API keys and rate limiting.

Identity resolution is isolated here so swapping the local stub for Clerk/Auth0 (``AUTH_MODE``)
touches one module. Passwords use PBKDF2-HMAC-SHA256 with a per-user salt; API keys are stored as
SHA-256 hashes and shown once.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from fastapi import HTTPException, status

from api.config import settings

PBKDF2_ITERATIONS = 260_000
API_KEY_PREFIX = "np_"


def hash_password(password: str) -> str:
    if len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt, digest = stored.split("$")
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations))
    return hmac.compare_digest(candidate.hex(), digest)


def create_access_token(
    *, user_id: str, org_id: str, role: str, ttl_minutes: int | None = None
) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "org": org_id,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=ttl_minutes or settings.access_token_ttl_minutes)).timestamp()),
        "iss": "nullpoint",
    }
    return jwt.encode(payload, settings.secret_key.get_secret_value(), algorithm="HS256")


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(
            token,
            settings.secret_key.get_secret_value(),
            algorithms=["HS256"],
            issuer="nullpoint",
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token") from exc


def generate_api_key() -> tuple[str, str, str]:
    """Return (full_key, prefix, sha256_hash). The full key is shown exactly once."""
    raw = secrets.token_urlsafe(32)
    full = f"{API_KEY_PREFIX}{raw}"
    return full, full[:12], hash_api_key(full)


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


@dataclass
class Principal:
    user_id: str
    org_id: str
    email: str
    role: str
    via: str  # jwt | api_key

    @property
    def is_admin(self) -> bool:
        return self.role in {"owner", "admin"}


class TokenBucket:
    def __init__(self, per_minute: int = 60, burst: int | None = None) -> None:
        self.rate = max(1, per_minute) / 60.0
        self.capacity = float(burst or max(1, per_minute))
        self._state: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, cost: float = 1.0) -> bool:
        with self._lock:
            now = time.monotonic()
            tokens, updated = self._state.get(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - updated) * self.rate)
            allowed = tokens >= cost
            self._state[key] = (tokens - cost if allowed else tokens, now)
            if len(self._state) > 10_000:  # bound memory on hostile traffic
                self._state = dict(list(self._state.items())[-5_000:])
            return allowed


RATE_LIMITER = TokenBucket(per_minute=120, burst=60)
AUTH_LIMITER = TokenBucket(per_minute=20, burst=10)


def require_admin(principal: Principal) -> None:
    if not principal.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin role required")


def safe_secret(value: Any) -> str | None:
    """Never expose a secret: report only whether it is configured."""
    if value is None:
        return None
    try:
        raw = value.get_secret_value() if hasattr(value, "get_secret_value") else str(value)
    except Exception:
        return "configured"
    return "configured" if raw else None
