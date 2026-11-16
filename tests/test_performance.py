from __future__ import annotations

from engine import MarketData, MemoryTrialCounter, make_spec
from engine.native import NativeAdapter
from engine.performance import parallel_map, worker_count
from tests.conftest import make_prices

WEIGHTED_SIGNAL = """
def signal(features, params, close):
    return close * 0 + float(params.get("weight", 1.0))
"""


def test_worker_count_respects_override(monkeypatch):
    monkeypatch.setenv("NULLPOINT_MAX_WORKERS", "2")
    assert worker_count() == 2
    assert worker_count(5) == 2
    monkeypatch.delenv("NULLPOINT_MAX_WORKERS")
    assert worker_count(5) == 5
    assert worker_count(1) == 1


def test_parallel_map_preserves_input_order():
    assert parallel_map(lambda value: value * 2, [1, 2, 3, 4], max_workers=3) == [2, 4, 6, 8]


def test_feature_cache_is_scoped_to_the_universe():
    data = MarketData(close=make_prices(n=400, seed=21))
    full = make_spec(
        "full universe",
        "synthetic test",
        ["SPY", "QQQ", "XLK"],
        signal_code=WEIGHTED_SIGNAL,
        features={"volatility": {"type": "volatility", "window": 20}},
    )
    narrow = make_spec(
        "narrow universe",
        "synthetic test",
        ["SPY", "QQQ"],
        signal_code=WEIGHTED_SIGNAL,
        features={"volatility": {"type": "volatility", "window": 20}},
    )
    adapter = NativeAdapter()
    adapter.run_backtest(full, data)
    cached = adapter.run_backtest(narrow, data)
    fresh = adapter.run_backtest(narrow, MarketData(close=data.close.copy()))
    assert list(cached.positions.columns) == ["SPY", "QQQ"]
    assert cached.returns.equals(fresh.returns)


def test_grid_parallel_and_sequential_agree_on_results_and_trial_count():
    data = MarketData(close=make_prices(n=300, seed=12))
    spec = make_spec(
        "parallel grid",
        "synthetic test",
        ["SPY", "QQQ", "XLK"],
        signal_code=WEIGHTED_SIGNAL,
        features={},
    )
    grid = {"weight": [0.2, 0.5, 0.8, 1.0], "variant": [0, 1]}

    sequential_counter = MemoryTrialCounter()
    sequential = NativeAdapter().run_grid(
        spec,
        grid,
        data,
        trial_counter=sequential_counter,
        min_trades=1,
        parallel=False,
    )
    # Native is GIL-bound and deliberately stays sequential; force the pool through a
    # test adapter that advertises parallel support, which is how the sandbox adapter works.
    class ParallelNative(NativeAdapter):
        supports_parallel = True

    parallel_counter = MemoryTrialCounter()
    parallel = ParallelNative().run_grid(
        spec,
        grid,
        data,
        trial_counter=parallel_counter,
        min_trades=1,
        parallel=True,
        max_workers=2,
    )

    assert parallel_counter.value == sequential_counter.value == 8
    assert parallel.best_params == sequential.best_params
    assert len(parallel.results) == len(sequential.results) == 8
    assert parallel.metadata["parallel"] is True
    assert parallel.metadata["workers"] == 2
    assert parallel.trial_index == 8
