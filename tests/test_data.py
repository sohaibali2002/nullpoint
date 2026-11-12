from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from data.lake import DataLake
from data.quality import check_ohlcv, clean_ohlcv
from data.universe import universe_notice


def _bars(n: int = 120, start: str = "2020-01-02") -> pd.DataFrame:
    index = pd.bdate_range(start=start, periods=n)
    rng = np.random.default_rng(1)
    close = 100 * np.cumprod(1 + rng.normal(0.0003, 0.01, n))
    return pd.DataFrame(
        {
            "open": close * 0.999,
            "high": close * 1.005,
            "low": close * 0.995,
            "close": close,
            "volume": rng.integers(1_000_000, 2_000_000, n),
        },
        index=index,
    )


def test_valid_dataset_passes():
    report = check_ohlcv(_bars(), "SPY", "yahoo")
    assert report.passed
    assert report.status in {"ok", "warn"}
    assert report.rows == 120


def test_duplicates_and_non_positive_prices_are_errors():
    frame = _bars()
    frame = pd.concat([frame, frame.iloc[[-1]]])
    frame.iloc[3, frame.columns.get_loc("close")] = -5
    frame.iloc[3, frame.columns.get_loc("open")] = -5
    report = check_ohlcv(frame, "BAD", "test")
    assert not report.passed
    assert any("duplicate" in error for error in report.errors)
    assert any("non-positive" in error for error in report.errors)


def test_gaps_and_stale_prices_are_warnings():
    frame = _bars(80)
    frame = frame.drop(frame.index[30:40])
    stale = frame.copy()
    frozen = float(stale["close"].iloc[54])
    for column in ("open", "high", "low", "close"):
        stale.iloc[55:62, stale.columns.get_loc(column)] = frozen
    report = check_ohlcv(stale, "GAPPY", "test", max_gap_days=5, stale_threshold=5)
    assert report.passed
    assert report.warnings
    assert report.checks["stale_runs"] >= 1


def test_ohlc_violation_is_an_error():
    frame = _bars()
    frame.iloc[5, frame.columns.get_loc("high")] = frame["close"].iloc[5] * 0.5
    report = check_ohlcv(frame, "BAD", "test")
    assert not report.passed
    assert any("OHLC" in error for error in report.errors)


def test_split_and_outlier_flags():
    frame = _bars(100)
    frame.iloc[50:, :4] = frame.iloc[50:, :4] / 2.0
    report = check_ohlcv(frame, "SPLIT", "test")
    assert any("split" in warning for warning in report.warnings)
    assert report.checks["outlier_count"] >= 1


def test_clean_ohlcv_drops_invalid_rows_without_filling():
    frame = _bars(50)
    frame.iloc[5, frame.columns.get_loc("close")] = np.nan
    frame.iloc[6, frame.columns.get_loc("close")] = -1
    cleaned = clean_ohlcv(frame)
    assert len(cleaned) == 48
    assert cleaned["close"].notna().all()
    assert (cleaned["close"] > 0).all()


def test_lake_roundtrip_and_duckdb(tmp_path):
    lake = DataLake(tmp_path)
    frame = _bars()
    frame.attrs["provider"] = "test"
    asset = lake.write_bars(frame, "test", "SPY", extra_meta={"license": "test-only"})
    assert asset.rows == len(frame)
    assert asset.content_hash
    loaded, meta = lake.read_bars("test", "SPY")
    pd.testing.assert_frame_equal(loaded, frame, check_freq=False, check_names=False)
    assert meta["provider"] == "test"
    manifests = lake.manifests()
    assert len(manifests) == 1
    assert lake.read_bars("test", "MISSING") is None

    result = lake.query("select count(*) as n from bars")
    assert int(result["n"].iloc[0]) == len(frame)
    with pytest.raises(ValueError):
        lake.query("delete from bars")
    assert lake.delete("test", "SPY") is True


def test_lake_cache_hits_and_expires(tmp_path):
    lake = DataLake(tmp_path)
    calls = {"n": 0}

    def fetcher() -> pd.DataFrame:
        calls["n"] += 1
        return _bars()

    lake.cached("test", "QQQ", "1d", fetcher, ttl_hours=24)
    frame, meta, hit = lake.cached("test", "QQQ", "1d", fetcher, ttl_hours=24)
    assert hit is True
    assert calls["n"] == 1
    assert len(frame) == 120
    _, _, forced = lake.cached("test", "QQQ", "1d", fetcher, ttl_hours=24, force=True)
    assert forced is False
    assert calls["n"] == 2


def test_lake_read_only_refuses_writes(tmp_path):
    lake = DataLake(tmp_path, read_only=True)
    with pytest.raises(PermissionError):
        lake.write_bars(_bars(), "test", "SPY")


def test_universe_notice_warns_about_survivorship():
    notice = universe_notice("us_sector_etfs")
    assert notice["survivorship_bias"] is True
    assert notice["warning"].startswith("SURVIVORSHIP BIAS")
    assert universe_notice("custom")["known"] is False
