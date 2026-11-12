"""Typed message bus / blackboard with a persistent, replayable audit trail.

Every agent thought, tool call and tool result is written to ``agent_message`` with a monotonically
increasing sequence number per sprint. SSE clients resume from ``?after=<seq>``; the replay endpoint
reconstructs the sprint from the same rows.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api.models import AgentMessage, LLMUsage


class BudgetExceeded(RuntimeError):
    """Raised when tokens, dollars, wall-clock or iteration caps are hit."""


@dataclass
class BudgetSnapshot:
    budget_usd: float
    spent_usd: float
    tokens: int
    trials: int
    elapsed_seconds: float
    max_iterations: int

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.budget_usd - self.spent_usd)

    def as_dict(self) -> dict[str, Any]:
        return {
            "budget_usd": round(self.budget_usd, 4),
            "spent_usd": round(self.spent_usd, 4),
            "remaining_usd": round(self.remaining_usd, 4),
            "tokens": self.tokens,
            "trials": self.trials,
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "max_iterations": self.max_iterations,
        }


class BudgetGuard:
    def __init__(
        self,
        budget_usd: float,
        wall_clock_minutes: float,
        max_iterations: int,
        *,
        spent_usd: float = 0.0,
        tokens: int = 0,
        trials: int = 0,
        kill_check: Callable[[], bool] | None = None,
    ) -> None:
        self.budget_usd = float(budget_usd)
        self.wall_clock_seconds = float(wall_clock_minutes) * 60.0
        self.max_iterations = int(max_iterations)
        self._spent = float(spent_usd)
        self._tokens = int(tokens)
        self._trials = int(trials)
        self._kill_check = kill_check
        self._started = time.monotonic()

    def spend(self, cost_usd: float = 0.0, tokens: int = 0, trials: int = 0) -> None:
        self._spent += max(0.0, float(cost_usd))
        self._tokens += max(0, int(tokens))
        self._trials += max(0, int(trials))
        self.check()

    def set_trials(self, trials: int) -> None:
        self._trials = max(self._trials, int(trials))

    def check(self) -> None:
        if self._kill_check and self._kill_check():
            raise BudgetExceeded("kill switch engaged")
        if self._spent > self.budget_usd:
            raise BudgetExceeded(f"budget exceeded: ${self._spent:.2f} > ${self.budget_usd:.2f}")
        if self.snapshot()["elapsed_seconds"] > self.wall_clock_seconds:
            raise BudgetExceeded("wall-clock limit exceeded")
        if self._trials > 100_000:
            raise BudgetExceeded("trial limit exceeded")

    def snapshot(self) -> dict[str, Any]:
        return BudgetSnapshot(
            budget_usd=self.budget_usd,
            spent_usd=self._spent,
            tokens=self._tokens,
            trials=self._trials,
            elapsed_seconds=time.monotonic() - self._started,
            max_iterations=self.max_iterations,
        ).as_dict()


@dataclass
class Emitted:
    seq: int
    agent: str
    role: str
    content: str
    created_at: str


class MessageBus:
    def __init__(
        self,
        session: Session,
        org_id: str,
        sprint_id: str,
        sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.session = session
        self.org_id = org_id
        self.sprint_id = sprint_id
        self.sink = sink

    def _next_seq(self) -> int:
        current = self.session.execute(
            select(func.coalesce(func.max(AgentMessage.seq), 0)).where(
                AgentMessage.sprint_id == self.sprint_id
            )
        ).scalar_one()
        return int(current) + 1

    def emit(
        self,
        agent: str,
        content: str,
        *,
        role: str = "assistant",
        tool_name: str | None = None,
        tool_payload: dict[str, Any] | None = None,
        model: str | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost_usd: float = 0.0,
        commit: bool = True,
    ) -> Emitted:
        payload_safe = _json_safe(tool_payload or {})
        message = AgentMessage(
            org_id=self.org_id,
            sprint_id=self.sprint_id,
            seq=self._next_seq(),
            agent=agent,
            role=role,
            content=content[:20_000],
            tool_name=tool_name,
            tool_payload=payload_safe,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
        )
        self.session.add(message)
        if cost_usd > 0 and model:
            self.session.add(
                LLMUsage(
                    org_id=self.org_id,
                    sprint_id=self.sprint_id,
                    model=model,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    cost_usd=cost_usd,
                )
            )
        if commit:
            self.session.commit()
        else:
            self.session.flush()
        payload = {
            "seq": message.seq,
            "sprint_id": self.sprint_id,
            "agent": agent,
            "role": role,
            "content": message.content,
            "tool_name": tool_name,
            "tool_payload": message.tool_payload,
            "model": model,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": cost_usd,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if self.sink:
            self.sink(payload)
        return Emitted(
            seq=message.seq,
            agent=agent,
            role=role,
            content=message.content,
            created_at=payload["created_at"],
        )

    def tool(self, agent: str, tool_name: str, payload: dict[str, Any], result: Any) -> Emitted:
        summary = _summarise(result)
        return self.emit(
            agent,
            f"{tool_name} → {summary}",
            role="tool",
            tool_name=tool_name,
            tool_payload={"input": payload, "result": _compact(result)},
        )

    def messages(self, after_seq: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        rows = self.session.execute(
            select(AgentMessage)
            .where(AgentMessage.sprint_id == self.sprint_id, AgentMessage.seq > after_seq)
            .order_by(AgentMessage.seq)
            .limit(limit)
        ).scalars()
        return [
            {
                "seq": row.seq,
                "agent": row.agent,
                "role": row.role,
                "content": row.content,
                "tool_name": row.tool_name,
                "tool_payload": row.tool_payload,
                "model": row.model,
                "tokens_in": row.tokens_in,
                "tokens_out": row.tokens_out,
                "cost_usd": row.cost_usd,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]

    @staticmethod
    def replay(session: Session, sprint_id: str, limit: int = 5000) -> list[dict[str, Any]]:
        rows = session.execute(
            select(AgentMessage)
            .where(AgentMessage.sprint_id == sprint_id)
            .order_by(AgentMessage.seq)
            .limit(limit)
        ).scalars()
        return [
            {
                "seq": row.seq,
                "agent": row.agent,
                "role": row.role,
                "content": row.content,
                "tool_name": row.tool_name,
                "tool_payload": row.tool_payload,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]


def _json_safe(value: Any, depth: int = 0) -> Any:
    """Coerce arbitrary tool output into a JSON-safe payload for the audit trail."""
    if depth > 5:
        return str(value)[:300]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:4000]
    if isinstance(value, dict):
        return {str(k): _json_safe(v, depth + 1) for k, v in list(value.items())[:40]}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v, depth + 1) for v in list(value)[:25]]
    if hasattr(value, "model_dump"):
        try:
            return _json_safe(value.model_dump(mode="json"), depth + 1)
        except Exception:  # noqa: BLE001
            pass
    metadata = getattr(value, "metadata", None)
    if isinstance(metadata, dict):
        return {"metadata": _json_safe(metadata, depth + 1)}
    metrics = getattr(value, "metrics", None)
    if isinstance(metrics, dict):
        return {
            "engine": str(getattr(value, "engine", "")),
            "params": _json_safe(getattr(value, "params", {}), depth + 1),
            "metrics": _json_safe(metrics, depth + 1),
        }
    return str(value)[:300]


def _compact(result: Any) -> Any:
    return _json_safe(result)


def _summarise(result: Any) -> str:
    if isinstance(result, dict):
        for key in ("summary", "message", "verdict", "recommendation", "status", "ok"):
            if key in result:
                return f"{key}={result[key]}"
        return f"{len(result)} fields"
    if isinstance(result, (list, tuple)):
        return f"{len(result)} items"
    return str(result)[:180]
