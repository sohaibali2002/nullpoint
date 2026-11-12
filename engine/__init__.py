"""Nullpoint backtest engine: declarative strategies, adapters, metrics, validation."""

from engine.base import (
    BacktestResult,
    EngineAdapter,
    EngineUnavailable,
    GridResult,
    MarketData,
    MemoryTrialCounter,
    WalkForwardReport,
    get_adapter,
    list_adapters,
)
from engine.metrics import Trade, compute_metrics
from engine.native import NativeAdapter
from engine.strategy import Costs, RiskRules, StrategySpec, make_spec

__all__ = [
    "BacktestResult",
    "Costs",
    "EngineAdapter",
    "EngineUnavailable",
    "GridResult",
    "MarketData",
    "MemoryTrialCounter",
    "NativeAdapter",
    "RiskRules",
    "StrategySpec",
    "Trade",
    "WalkForwardReport",
    "compute_metrics",
    "get_adapter",
    "list_adapters",
    "make_spec",
]
