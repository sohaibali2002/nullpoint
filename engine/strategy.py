"""The declarative Strategy: hypothesis + universe + features + signal + risk + costs.

A strategy is data, not code: the spec is versioned JSON, the signal is Python source that
must pass the AST scanner before it can run. Every field that affects results is hashed so
experiments are reproducible.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Costs(BaseModel):
    """All costs in basis points, applied per unit of turnover. Deliberately pessimistic."""

    model_config = ConfigDict(extra="forbid")

    commission_bps: float = Field(1.0, ge=0, le=100)
    slippage_bps: float = Field(2.0, ge=0, le=200)
    spread_bps: float = Field(0.5, ge=0, le=200)
    borrow_bps_annual: float = Field(50.0, ge=0, le=10_000)

    @property
    def per_turnover_bps(self) -> float:
        return self.commission_bps + self.slippage_bps + self.spread_bps


class RiskRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_gross_exposure: float = Field(1.0, gt=0, le=10)
    max_position_weight: float = Field(0.35, gt=0, le=1.0)
    stop_loss_pct: float | None = Field(None, gt=0, le=1.0)
    take_profit_pct: float | None = Field(None, gt=0, le=10.0)
    vol_target_annual: float | None = Field(None, gt=0, le=2.0)
    max_drawdown_stop: float | None = Field(None, gt=0, le=1.0)
    rebalance: Literal["daily", "weekly", "monthly"] = "daily"
    long_short: bool = True


class StrategySpec(BaseModel):
    """Versioned, hashable description of a systematic strategy."""

    model_config = ConfigDict(extra="forbid")

    id: str = "strategy"
    name: str
    version: int = 1
    hypothesis: str
    universe: list[str]
    features: dict[str, dict[str, Any]] = Field(default_factory=dict)
    signal_code: str
    params: dict[str, Any] = Field(default_factory=dict)
    risk: RiskRules = Field(default_factory=RiskRules)
    costs: Costs = Field(default_factory=Costs)
    benchmark: str = "SPY"
    rebalance: Literal["daily", "weekly", "monthly"] = "daily"
    notes: str = ""
    created_by: str = "agent"

    @field_validator("universe")
    @classmethod
    def _universe_nonempty(cls, value: list[str]) -> list[str]:
        cleaned = [s.strip().upper() for s in value if s.strip()]
        if not cleaned:
            raise ValueError("universe must contain at least one symbol")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("universe contains duplicates")
        return cleaned

    @model_validator(mode="after")
    def _align_rebalance(self) -> StrategySpec:
        # Risk rules are the single source of truth for the rebalance schedule.
        if self.risk.rebalance != self.rebalance:
            self.rebalance = self.risk.rebalance
        return self

    def canonical(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload.pop("id", None)
        payload.pop("notes", None)
        return payload

    def spec_hash(self) -> str:
        blob = json.dumps(self.canonical(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()

    def code_hash(self) -> str:
        return hashlib.sha256(self.signal_code.encode()).hexdigest()

    def with_params(self, params: dict[str, Any]) -> StrategySpec:
        return self.model_copy(update={"params": {**self.params, **params}})


DEFAULT_SIGNAL = '''
def signal(features, params):
    """Cross-sectional momentum with a volatility floor.

    Returns target weights in [-1, 1] per symbol. Positions are executed on the NEXT bar
    by the engine, so this function may only look at information available at time t.
    """
    mom = features["momentum"]
    vol = features["volatility"]
    lookback = int(params.get("lookback", 20))
    top_n = int(params.get("top_n", 3))
    mom = features[f"momentum_{lookback}"]
    score = mom / vol.clip(lower=1e-4)
    ranks = score.rank(axis=1, ascending=False, method="first")
    longs = (ranks <= top_n).astype(float)
    shorts = (ranks > ranks.shape[1] - top_n).astype(float) if params.get("long_short", True) else 0.0
    raw = longs - shorts
    return raw.div(raw.abs().sum(axis=1).replace(0, 1), axis=0)
'''


def make_spec(
    name: str,
    hypothesis: str,
    universe: list[str],
    *,
    lookback: int = 20,
    signal_code: str | None = None,
    features: dict[str, dict[str, Any]] | None = None,
    params: dict[str, Any] | None = None,
    costs: Costs | None = None,
    risk: RiskRules | None = None,
    benchmark: str = "SPY",
    version: int = 1,
) -> StrategySpec:
    """Convenience builder used by the ideator, the demo and the seed script."""
    feats = features or {f"momentum_{lookback}": {"type": "momentum", "window": lookback}}
    if "volatility" not in feats:
        feats = {**feats, "volatility": {"type": "volatility", "window": 20}}
    all_params: dict[str, Any] = {"lookback": lookback, "top_n": 3, "long_short": True}
    all_params.update(params or {})
    return StrategySpec(
        name=name,
        version=version,
        hypothesis=hypothesis,
        universe=universe,
        features=feats,
        signal_code=signal_code or DEFAULT_SIGNAL,
        params=all_params,
        costs=costs or Costs(),
        risk=risk or RiskRules(),
        benchmark=benchmark,
    )
