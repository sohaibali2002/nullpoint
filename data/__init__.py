"""Free-data layer: providers, quality checks, Parquet/DuckDB lake and universe registry."""

from data.base import (
    BarProvider,
    DataUnavailable,
    ProviderError,
    ProviderMeta,
    RateLimiter,
    normalize_bars,
)
from data.lake import DataLake, LakeAsset
from data.providers import PROVIDER_CLASSES, get_provider, provider_catalog
from data.quality import QualityReport, check_ohlcv, clean_ohlcv
from data.universe import UNIVERSES, universe_notice

__all__ = [
    "BarProvider",
    "DataLake",
    "DataUnavailable",
    "LakeAsset",
    "PROVIDER_CLASSES",
    "ProviderError",
    "ProviderMeta",
    "QualityReport",
    "RateLimiter",
    "UNIVERSES",
    "check_ohlcv",
    "clean_ohlcv",
    "get_provider",
    "normalize_bars",
    "provider_catalog",
    "universe_notice",
]
