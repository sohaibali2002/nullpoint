"""Pydantic request/response schemas. All external input is validated here."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SYMBOL = re.compile(r"^[A-Z0-9^=./-]{1,24}$")


class RegisterIn(BaseModel):
    org_name: str = Field(min_length=2, max_length=120)
    email: str = Field(min_length=5, max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def _lower(cls, value: str) -> str:
        return value.lower().strip()


class LoginIn(BaseModel):
    email: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    org_id: str
    user_id: str
    role: str


class ApiKeyIn(BaseModel):
    name: str = Field(default="default", max_length=80)


class ApiKeyOut(BaseModel):
    id: str
    name: str
    prefix: str
    created_at: str | None = None
    last_used_at: str | None = None


class ApiKeyCreated(ApiKeyOut):
    key: str


class SprintIn(BaseModel):
    objective: str = Field(min_length=8, max_length=2000)
    universe: list[str] = Field(default_factory=lambda: ["SPY", "QQQ", "XLK", "XLF", "XLE", "XLV"], max_length=40)
    budget_usd: float | None = Field(default=None, gt=0, le=1000)
    start: str | None = None
    end: str | None = None
    max_strategies: int = Field(default=4, ge=1, le=12)

    @field_validator("universe")
    @classmethod
    def _symbols(cls, values: list[str]) -> list[str]:
        cleaned = [v.strip().upper() for v in values if v.strip()]
        if not cleaned:
            raise ValueError("universe must contain at least one symbol")
        bad = [v for v in cleaned if not SYMBOL.match(v)]
        if bad:
            raise ValueError(f"invalid symbols: {bad}")
        return list(dict.fromkeys(cleaned))


class ChatSprintIn(BaseModel):
    message: str = Field(min_length=4, max_length=2000)


class IdeaIn(BaseModel):
    title: str = Field(min_length=4, max_length=200)
    hypothesis: str = Field(min_length=10, max_length=4000)
    family: str = Field(default="cross_sectional", max_length=60)
    tags: list[str] = Field(default_factory=list, max_length=12)


class ApproveIn(BaseModel):
    note: str = Field(default="", max_length=1000)
    target: Literal["paper", "live_ready"] = "paper"


class PortfolioIn(BaseModel):
    strategy_ids: list[str] = Field(min_length=2, max_length=20)
    method: Literal["hrp", "risk_parity", "inverse_vol", "mean_variance"] = "hrp"
    max_weight: float = Field(default=0.4, gt=0, le=1)


class PaperIn(BaseModel):
    strategy_id: str
    initial_equity: float = Field(default=100_000, gt=1000, le=100_000_000)


class QueryIn(BaseModel):
    sql: str = Field(min_length=6, max_length=4000)
    limit: int = Field(default=200, ge=1, le=2000)


class SettingsPatch(BaseModel):
    monthly_budget_usd: float | None = Field(default=None, gt=0, le=10_000)
    sprint_budget_usd: float | None = Field(default=None, gt=0, le=1000)
    model_orchestrator: str | None = Field(default=None, max_length=80)
    model_worker: str | None = Field(default=None, max_length=80)
    model_skeptic: str | None = Field(default=None, max_length=80)
    model_cheap: str | None = Field(default=None, max_length=80)
    nightly_enabled: bool | None = None
    alert_slack: bool | None = None
    alert_email: bool | None = None


class AlertIn(BaseModel):
    test: bool = True


class ORMBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class MessageOut(ORMBase):
    seq: int
    agent: str
    role: str
    content: str
    tool_name: str | None = None
    tool_payload: dict[str, Any] = Field(default_factory=dict)
    model: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    created_at: str | None = None
