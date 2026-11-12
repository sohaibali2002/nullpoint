"""Postgres/SQLite-compatible schema for the whole platform.

Design notes
------------
* Every tenant-scoped row carries ``org_id``; queries always filter on it.
* Payloads that agents produce (specs, metrics, validation reports, audit messages) are stored as
  JSON so the ledger stays replayable even as Pydantic models evolve.
* ``TrialCounter`` is the single source of truth for the multiple-testing denominator N.
* No table stores derived secrets: API keys are SHA-256 hashed, passwords PBKDF2.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db import Base

JSONType = JSON().with_variant(JSONB(), "postgresql")


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(UTC)


class StrategyStatus(enum.StrEnum):
    """The only permitted status ladder. Live trading is intentionally absent."""

    IDEA = "idea"
    BACKTESTED = "backtested"
    VALIDATED = "validated"
    CANDIDATE = "candidate"
    PAPER = "paper"
    LIVE_READY = "live_ready"
    REJECTED = "rejected"
    ARCHIVED = "archived"

    @property
    def order(self) -> int:
        return _STATUS_ORDER[self]


_STATUS_ORDER: dict[StrategyStatus, int] = {
    StrategyStatus.IDEA: 0,
    StrategyStatus.BACKTESTED: 1,
    StrategyStatus.VALIDATED: 2,
    StrategyStatus.CANDIDATE: 3,
    StrategyStatus.PAPER: 4,
    StrategyStatus.LIVE_READY: 5,
    StrategyStatus.REJECTED: -1,
    StrategyStatus.ARCHIVED: -2,
}

# Forward-only transitions. Rejection/archival allowed from anywhere before LIVE_READY.
ALLOWED_TRANSITIONS: dict[StrategyStatus, set[StrategyStatus]] = {
    StrategyStatus.IDEA: {StrategyStatus.BACKTESTED, StrategyStatus.REJECTED, StrategyStatus.ARCHIVED},
    StrategyStatus.BACKTESTED: {StrategyStatus.VALIDATED, StrategyStatus.REJECTED, StrategyStatus.ARCHIVED},
    StrategyStatus.VALIDATED: {StrategyStatus.CANDIDATE, StrategyStatus.REJECTED, StrategyStatus.ARCHIVED},
    StrategyStatus.CANDIDATE: {StrategyStatus.PAPER, StrategyStatus.REJECTED, StrategyStatus.ARCHIVED},
    StrategyStatus.PAPER: {StrategyStatus.VALIDATED, StrategyStatus.REJECTED, StrategyStatus.ARCHIVED},
    StrategyStatus.LIVE_READY: {StrategyStatus.ARCHIVED},
    StrategyStatus.REJECTED: {StrategyStatus.ARCHIVED},
    StrategyStatus.ARCHIVED: set(),
}


class Organization(Base):
    __tablename__ = "organization"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    plan: Mapped[str] = mapped_column(String(32), default="research")
    monthly_budget_usd: Mapped[float] = mapped_column(Float, default=250.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    users: Mapped[list[User]] = relationship(back_populates="org", cascade="all, delete-orphan")


class User(Base):
    __tablename__ = "app_user"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organization.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(24), default="owner")  # owner|admin|analyst|viewer
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    org: Mapped[Organization] = relationship(back_populates="users")


class ApiKey(Base):
    __tablename__ = "api_key"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organization.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(80), default="default")
    prefix: Mapped[str] = mapped_column(String(16), index=True)
    hashed_key: Mapped[str] = mapped_column(String(128), unique=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Idea(Base):
    __tablename__ = "idea"
    __table_args__ = (UniqueConstraint("org_id", "fingerprint", name="uq_idea_org_fingerprint"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organization.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    hypothesis: Mapped[str] = mapped_column(Text)
    family: Mapped[str] = mapped_column(String(60), default="cross_sectional")
    tags: Mapped[list[str]] = mapped_column(JSONType, default=list)
    # Sentence-embedding stand-in (hashed bag of n-grams) used for de-duplication.
    embedding: Mapped[list[float]] = mapped_column(JSONType, default=list)
    fingerprint: Mapped[str] = mapped_column(String(64))
    dedupe_of: Mapped[str | None] = mapped_column(String(32))
    dedupe_score: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(24), default="new")
    origin: Mapped[str] = mapped_column(String(24), default="agent")  # agent|human
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Strategy(Base):
    __tablename__ = "strategy"
    __table_args__ = (
        UniqueConstraint("org_id", "name", "version", name="uq_strategy_name_version"),
        Index("ix_strategy_org_status", "org_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organization.id", ondelete="CASCADE"), index=True)
    idea_id: Mapped[str | None] = mapped_column(ForeignKey("idea.id", ondelete="SET NULL"))
    sprint_id: Mapped[str | None] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(160))
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(24), default=StrategyStatus.IDEA.value)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONType)
    code: Mapped[str] = mapped_column(Text, default="")
    code_hash: Mapped[str] = mapped_column(String(64), default="")
    parent_id: Mapped[str | None] = mapped_column(String(32))
    created_by: Mapped[str] = mapped_column(String(32), default="agent")
    tags: Mapped[list[str]] = mapped_column(JSONType, default=list)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    trial_index: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    experiments: Mapped[list[Experiment]] = relationship(back_populates="strategy")


class Sprint(Base):
    __tablename__ = "sprint"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organization.id", ondelete="CASCADE"), index=True)
    objective: Mapped[str] = mapped_column(Text)
    universe: Mapped[list[str]] = mapped_column(JSONType, default=list)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    config: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    budget_usd: Mapped[float] = mapped_column(Float, default=8.0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    trials_used: Mapped[int] = mapped_column(Integer, default=0)
    strategies_found: Mapped[int] = mapped_column(Integer, default=0)
    kill_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(32), default="human")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Experiment(Base):
    __tablename__ = "experiment"
    __table_args__ = (Index("ix_experiment_org_kind", "org_id", "kind"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organization.id", ondelete="CASCADE"), index=True)
    sprint_id: Mapped[str | None] = mapped_column(String(32), index=True)
    strategy_id: Mapped[str | None] = mapped_column(ForeignKey("strategy.id", ondelete="CASCADE"))
    strategy_version: Mapped[int] = mapped_column(Integer, default=1)
    kind: Mapped[str] = mapped_column(String(32))  # backtest|grid|wfo|cpcv|skeptic|holdout
    engine: Mapped[str] = mapped_column(String(32), default="native")
    params: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    equity_curve: Mapped[list[float]] = mapped_column(JSONType, default=list)
    returns: Mapped[list[float]] = mapped_column(JSONType, default=list)
    dates: Mapped[list[str]] = mapped_column(JSONType, default=list)
    data_hash: Mapped[str] = mapped_column(String(64), default="")
    code_hash: Mapped[str] = mapped_column(String(64), default="")
    seed: Mapped[int] = mapped_column(Integer, default=7)
    env: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    trial_index: Mapped[int] = mapped_column(Integer, default=0)
    is_oos: Mapped[bool] = mapped_column(Boolean, default=False)
    split: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    strategy: Mapped[Strategy | None] = relationship(back_populates="experiments")


class ValidationReport(Base):
    __tablename__ = "validation_report"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organization.id", ondelete="CASCADE"), index=True)
    sprint_id: Mapped[str | None] = mapped_column(String(32), index=True)
    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategy.id", ondelete="CASCADE"), index=True)
    experiment_id: Mapped[str | None] = mapped_column(String(32))
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    recommendation: Mapped[str] = mapped_column(String(24), default="reject")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentMessage(Base):
    """Append-only audit trail. `seq` is monotonic per sprint and enables replay + SSE resume."""

    __tablename__ = "agent_message"
    __table_args__ = (UniqueConstraint("sprint_id", "seq", name="uq_agent_message_seq"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organization.id", ondelete="CASCADE"), index=True)
    sprint_id: Mapped[str] = mapped_column(String(32), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    agent: Mapped[str] = mapped_column(String(40))
    role: Mapped[str] = mapped_column(String(24), default="assistant")  # system|assistant|tool|human
    content: Mapped[str] = mapped_column(Text, default="")
    tool_name: Mapped[str | None] = mapped_column(String(64))
    tool_payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    model: Mapped[str | None] = mapped_column(String(60))
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TrialCounter(Base):
    __tablename__ = "trial_counter"
    __table_args__ = (UniqueConstraint("org_id", "scope", name="uq_trial_org_scope"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organization.id", ondelete="CASCADE"))
    scope: Mapped[str] = mapped_column(String(80), default="global")
    count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class LLMUsage(Base):
    __tablename__ = "llm_usage"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organization.id", ondelete="CASCADE"), index=True)
    sprint_id: Mapped[str | None] = mapped_column(String(32), index=True)
    model: Mapped[str] = mapped_column(String(60))
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Portfolio(Base):
    __tablename__ = "portfolio"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organization.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    method: Mapped[str] = mapped_column(String(32), default="hrp")
    weights: Mapped[dict[str, float]] = mapped_column(JSONType, default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PaperRun(Base):
    __tablename__ = "paper_run"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organization.id", ondelete="CASCADE"), index=True)
    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategy.id", ondelete="CASCADE"))
    broker: Mapped[str] = mapped_column(String(32), default="simulator")
    status: Mapped[str] = mapped_column(String(24), default="running")
    initial_equity: Mapped[float] = mapped_column(Float, default=100_000.0)
    equity_curve: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)
    fills: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)
    drift: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Approval(Base):
    """Human gate. Required before CANDIDATE -> PAPER and before LIVE_READY (which has no executor)."""

    __tablename__ = "approval"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organization.id", ondelete="CASCADE"), index=True)
    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategy.id", ondelete="CASCADE"))
    requested_status: Mapped[str] = mapped_column(String(24))
    approved_by: Mapped[str] = mapped_column(String(32))
    note: Mapped[str] = mapped_column(Text, default="")
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AlertEvent(Base):
    __tablename__ = "alert_event"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organization.id", ondelete="CASCADE"), index=True)
    channel: Mapped[str] = mapped_column(String(24), default="log")
    event: Mapped[str] = mapped_column(String(48))
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), default="queued")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DataAsset(Base):
    """A cached dataset with its quality report and content hash (data lineage)."""

    __tablename__ = "data_asset"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organization.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    symbol: Mapped[str] = mapped_column(String(40))
    interval: Mapped[str] = mapped_column(String(16), default="1d")
    start: Mapped[str] = mapped_column(String(20), default="")
    end: Mapped[str] = mapped_column(String(20), default="")
    rows: Mapped[int] = mapped_column(Integer, default=0)
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    path: Mapped[str] = mapped_column(String(500), default="")
    quality: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Memory(Base):
    """Librarian memory: what worked, what failed, and why (embedded for recall)."""

    __tablename__ = "memory"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organization.id", ondelete="CASCADE"), index=True)
    sprint_id: Mapped[str | None] = mapped_column(String(32), index=True)
    strategy_id: Mapped[str | None] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(24), default="lesson")  # lesson|result|note
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(JSONType, default=list)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Setting(Base):
    __tablename__ = "setting"
    __table_args__ = (UniqueConstraint("org_id", name="uq_setting_org"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organization.id", ondelete="CASCADE"))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
