from __future__ import annotations

from collections.abc import Iterator, Sequence

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from engine.base import MarketData


def make_prices(
    n: int = 600,
    symbols: Sequence[str] = ("SPY", "QQQ", "XLK"),
    *,
    seed: int = 7,
    drift: float = 0.0,
    vol: float = 0.012,
    start: str = "2018-01-02",
    common: float = 0.6,
) -> pd.DataFrame:
    """Correlated GBM panel with business-day index. drift=0 gives a pure-noise null."""
    rng = np.random.default_rng(seed)
    index = pd.bdate_range(start=start, periods=n)
    market = rng.normal(drift, vol, size=n)
    data = {}
    for i, symbol in enumerate(symbols):
        idio = rng.normal(drift * 0.2, vol * (1 - common) * (1 + 0.1 * i), size=n)
        data[symbol] = 100.0 * np.cumprod(1.0 + common * market + idio)
    return pd.DataFrame(data, index=index)


def make_trending_prices(
    n: int = 800,
    symbols: Sequence[str] = ("AAA", "BBB", "CCC", "DDD"),
    *,
    seed: int = 11,
) -> pd.DataFrame:
    """Alternating persistent trends: a cross-sectional momentum signal should find structure."""
    rng = np.random.default_rng(seed)
    index = pd.bdate_range(start="2016-01-04", periods=n)
    frame = {}
    block = 60
    for i, symbol in enumerate(symbols):
        drift = np.zeros(n)
        for start in range(0, n, block):
            sign = 1.0 if ((start // block) + i) % 2 == 0 else -1.0
            drift[start : start + block] = sign * 0.0018
        noise = rng.normal(0.0, 0.004, size=n)
        frame[symbol] = 50.0 * np.cumprod(1.0 + drift + noise)
    return pd.DataFrame(frame, index=index)


def make_market_data(
    n: int = 600,
    symbols: Sequence[str] = ("SPY", "QQQ", "XLK"),
    *,
    seed: int = 7,
    drift: float = 0.0,
    vol: float = 0.012,
) -> MarketData:
    close = make_prices(n=n, symbols=symbols, seed=seed, drift=drift, vol=vol)
    return MarketData(close=close, benchmark=close[symbols[0]], provider="synthetic")


@pytest.fixture()
def db_session(tmp_path):
    from api import models  # noqa: F401  (register metadata)
    from api.db import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def noise_data() -> MarketData:
    return make_market_data(n=700, seed=3, drift=0.0)


@pytest.fixture()
def trending_data() -> MarketData:
    close = make_trending_prices()
    return MarketData(close=close, benchmark=None, provider="synthetic")
