"""Data-quality checks run on every fetch before data reaches the engine.

Findings are split into ``errors`` (block the dataset) and ``warnings`` (visible in the UI and in
the experiment ledger). Checks are deterministic and cheap enough to run on ingest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

COMMON_SPLIT_RATIOS = (2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0, 15.0, 20.0)


@dataclass
class QualityReport:
    symbol: str
    provider: str
    rows: int
    start: str | None
    end: str | None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return not self.errors

    @property
    def status(self) -> str:
        if self.errors:
            return "failed"
        return "warn" if self.warnings else "ok"

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "provider": self.provider,
            "rows": self.rows,
            "start": self.start,
            "end": self.end,
            "status": self.status,
            "passed": self.passed,
            "errors": self.errors,
            "warnings": self.warnings,
            "checks": self.checks,
        }


def _business_day_gaps(index: pd.DatetimeIndex, threshold: int) -> list[str]:
    if len(index) < 3:
        return []
    expected = pd.bdate_range(index[0], index[-1])
    missing = expected.difference(index)
    gaps: list[str] = []
    if missing.empty:
        return gaps
    runs = _runs(missing)
    for run in runs:
        if len(run) >= threshold:
            gaps.append(f"{len(run)} consecutive business days missing from {run[0].date()} to {run[-1].date()}")
    return gaps


def _runs(index: pd.DatetimeIndex) -> list[list[pd.Timestamp]]:
    runs: list[list[pd.Timestamp]] = []
    current: list[pd.Timestamp] = []
    for ts in index:
        if current and (ts - current[-1]).days > 4:
            runs.append(current)
            current = []
        current.append(ts)
    if current:
        runs.append(current)
    return runs


def _stale_runs(close: pd.Series, threshold: int) -> list[str]:
    same = close.diff().fillna(1.0) == 0
    runs: list[str] = []
    start: Any = None
    length = 0
    for ts, flag in same.items():
        if flag:
            if start is None:
                start = ts
            length += 1
        else:
            if length >= threshold and start is not None:
                runs.append(f"price unchanged for {length} bars starting {start.date()}")
            start, length = None, 0
    if length >= threshold and start is not None:
        runs.append(f"price unchanged for {length} bars starting {start.date()}")
    return runs


def _split_candidates(close: pd.Series) -> list[str]:
    rets = close.pct_change(fill_method=None).dropna()
    hits: list[str] = []
    for ts, ret in rets.items():
        if abs(ret) < 0.25:
            continue
        ratio = 1.0 + ret
        for split in COMMON_SPLIT_RATIOS:
            if abs(ratio - split) / split < 0.03 or abs(ratio - 1.0 / split) * split < 0.03:
                hits.append(f"possible unadjusted {split:g}:1 split on {ts.date()} (return {ret:+.1%})")
                break
    return hits[:5]


def check_ohlcv(
    df: pd.DataFrame,
    symbol: str = "",
    provider: str = "",
    *,
    max_gap_days: int = 5,
    stale_threshold: int = 5,
    outlier_sigma: float = 8.0,
    expect_adjusted: bool = True,
) -> QualityReport:
    report = QualityReport(
        symbol=symbol or str(df.attrs.get("symbol", "")),
        provider=provider or str(df.attrs.get("provider", "")),
        rows=len(df),
        start=str(df.index[0].date()) if len(df) else None,
        end=str(df.index[-1].date()) if len(df) else None,
    )
    if df.empty:
        report.errors.append("dataset is empty")
        return report

    required = {"open", "high", "low", "close", "volume"}
    missing = sorted(required - set(df.columns))
    if missing:
        report.errors.append(f"missing columns: {missing}")
        return report

    if not isinstance(df.index, pd.DatetimeIndex):
        report.errors.append("index is not a DatetimeIndex")
        return report
    report.checks["rows"] = len(df)
    report.checks["duplicates"] = int(df.index.duplicated().sum())
    if df.index.duplicated().any():
        report.errors.append(f"{int(df.index.duplicated().sum())} duplicate timestamps")
    if not df.index.is_monotonic_increasing:
        report.errors.append("index is not sorted ascending")

    non_positive = int((df[["open", "high", "low", "close"]] <= 0).sum().sum())
    if non_positive:
        report.errors.append(f"{non_positive} non-positive OHLC values")

    nan_counts = df[["open", "high", "low", "close"]].isna().sum()
    report.checks["nan_by_column"] = {k: int(v) for k, v in nan_counts.items()}
    if int(nan_counts.sum()) > 0:
        report.errors.append(f"{int(nan_counts.sum())} NaN values in OHLC columns")
    if df["volume"].isna().mean() > 0.5:
        report.warnings.append("volume is mostly missing; turnover/capacity estimates are unreliable")

    valid = df.dropna(subset=["open", "high", "low", "close"])
    if len(valid):
        bad_high = int((valid["high"] < valid[["open", "close"]].max(axis=1) - 1e-9).sum())
        bad_low = int((valid["low"] > valid[["open", "close"]].min(axis=1) + 1e-9).sum())
        report.checks["ohlc_violations"] = bad_high + bad_low
        if bad_high or bad_low:
            report.errors.append(f"OHLC relationship violated on {bad_high + bad_low} bars")

    gaps = _business_day_gaps(df.index, max_gap_days)
    if gaps:
        report.warnings.extend(gaps)
    report.checks["gap_count"] = len(gaps)

    stale = _stale_runs(df["close"], stale_threshold)
    if stale:
        report.warnings.extend(stale)
    report.checks["stale_runs"] = len(stale)

    rets = df["close"].pct_change(fill_method=None).dropna()
    if len(rets) > 30:
        sigma = float(rets.std(ddof=1))
        if sigma > 0:
            outliers = rets[abs((rets - rets.mean()) / sigma) > outlier_sigma]
            report.checks["outlier_count"] = int(len(outliers))
            report.checks["max_abs_return"] = float(rets.abs().max())
            if len(outliers):
                dates = ", ".join(str(d.date()) for d in outliers.index[:4])
                report.warnings.append(f"{len(outliers)} returns beyond {outlier_sigma:g} sigma ({dates})")
        splits = _split_candidates(df["close"])
        if splits and expect_adjusted:
            report.warnings.extend(splits)
        elif splits:
            report.warnings.extend(splits)

    if "adj_close" in df.columns:
        ratio = (df["adj_close"] / df["close"]).replace([np.inf, -np.inf], np.nan).dropna()
        if len(ratio) and ratio.std() > 0:
            report.checks["adjustment_ratio_range"] = [float(ratio.min()), float(ratio.max())]
            if ratio.max() / max(ratio.min(), 1e-12) > 1.5:
                report.warnings.append("adjusted/unadjusted ratio varies >50%; verify corporate actions")

    years = (df.index[-1] - df.index[0]).days / 365.25 if len(df) > 1 else 0.0
    report.checks["years"] = round(float(years), 2)
    if years < 1.0:
        report.warnings.append("less than one year of history: results are fragile")

    return report


def clean_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Minimal, non-fabricating cleanup: sort, de-duplicate, drop invalid rows."""
    out = df.copy()
    out = out[~out.index.duplicated(keep="last")].sort_index()
    for column in ("open", "high", "low", "close"):
        if column in out:
            out[column] = pd.to_numeric(out[column], errors="coerce")
    out = out.dropna(subset=[c for c in ("open", "high", "low", "close") if c in out.columns])
    out = out[(out[["open", "high", "low", "close"]] > 0).all(axis=1)]
    out.attrs.update(df.attrs)
    return out
