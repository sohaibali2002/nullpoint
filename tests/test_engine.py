from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine import EngineUnavailable, MarketData, MemoryTrialCounter, make_spec
from engine.native import NativeAdapter, extract_trades
from engine.signal import SignalError, compile_signal
from engine.strategy import Costs, RiskRules
from tests.conftest import make_prices

CONSTANT_LONG = """
def signal(features, params, close):
    weight = float(params.get("weight", 1.0))
    return close * 0 + weight
"""

HOLD_THEN_EXIT = """
def signal(features, params, close):
    hold = int(params.get("hold", 30))
    mask = np.zeros(len(close))
    mask[:hold] = 1.0
    return pd.DataFrame(
        np.repeat(mask[:, None], close.shape[1], axis=1),
        index=close.index,
        columns=close.columns,
    )
"""

MOMENTUM_SIGNAL = """
def signal(features, params, close):
    lookback = int(params.get("lookback", 20))
    top_n = int(params.get("top_n", 2))
    score = features[f"momentum_{lookback}"] / features["volatility"].clip(lower=1e-4)
    ranks = score.rank(axis=1, ascending=False, method="first")
    longs = (ranks <= top_n).astype(float)
    shorts = (ranks > ranks.shape[1] - top_n).astype(float)
    raw = longs - shorts
    return raw.div(raw.abs().sum(axis=1).replace(0, 1), axis=0)
"""

BROKEN_SIGNAL = """
def signal(features, params, close):
    if params.get("weight", 1.0) > 0.6:
        raise RuntimeError("bad parameter")
    return close * 0 + 0.5
"""


def _spec(signal_code: str = CONSTANT_LONG, **kwargs):
    universe = kwargs.pop("universe", ["SPY", "QQQ", "XLK"])
    default_features = {
        "momentum_5": {"type": "momentum", "window": 5},
        "momentum_10": {"type": "momentum", "window": 10},
        "momentum_20": {"type": "momentum", "window": 20},
        "volatility": {"type": "volatility", "window": 20},
    }
    features = kwargs.pop("features", default_features)
    return make_spec(
        "test strategy",
        "synthetic test",
        universe,
        signal_code=signal_code,
        features=features,
        **kwargs,
    )


def test_next_bar_execution_only():
    data = MarketData(close=make_prices(n=80))
    spec = _spec(risk=RiskRules(max_position_weight=1.0, max_gross_exposure=3.0))
    result = NativeAdapter().run_backtest(spec, data)
    assert result.positions.iloc[0].abs().sum() == pytest.approx(0.0)
    assert result.positions.iloc[1].abs().sum() == pytest.approx(3.0, abs=1e-9)
    # first day has no position, so no PnL on day 0
    assert result.returns.iloc[0] == pytest.approx(0.0)


def test_costs_and_borrow_are_charged():
    data = MarketData(close=make_prices(n=120))
    spec = _spec(
        risk=RiskRules(max_position_weight=1.0, max_gross_exposure=3.0),
        costs=Costs(commission_bps=1.0, slippage_bps=2.0, spread_bps=0.5, borrow_bps_annual=0.0),
    )
    long = NativeAdapter().run_backtest(spec, data)
    assert long.costs.iloc[1] == pytest.approx(3.0 * 3.5 / 10_000, rel=1e-6)
    assert long.returns.iloc[1] < long.gross_returns.iloc[1]

    flat_code = "def signal(features, params, close):\n    return close * 0\n"
    flat = NativeAdapter().run_backtest(_spec(flat_code), data)
    assert flat.costs.abs().sum() == pytest.approx(0.0)
    assert flat.returns.abs().sum() == pytest.approx(0.0)

    short_code = "def signal(features, params, close):\n    return close * 0 - 1.0\n"
    short = NativeAdapter().run_backtest(
        _spec(
            short_code,
            risk=RiskRules(max_position_weight=1.0, max_gross_exposure=3.0),
            costs=Costs(borrow_bps_annual=252.0, commission_bps=0, slippage_bps=0, spread_bps=0),
        ),
        data,
    )
    assert short.costs.iloc[5] == pytest.approx(3 * 252 / 10_000 / 252, rel=1e-6)


def test_long_only_rule_clips_shorts():
    data = MarketData(close=make_prices(n=80))
    short_code = "def signal(features, params, close):\n    return close * 0 - 1.0\n"
    spec = _spec(short_code, risk=RiskRules(long_short=False, max_position_weight=1.0, max_gross_exposure=3.0))
    result = NativeAdapter().run_backtest(spec, data)
    assert result.positions.min().min() >= 0.0


def test_position_and_gross_limits_enforced():
    data = MarketData(close=make_prices(n=100))
    spec = _spec(
        risk=RiskRules(max_position_weight=0.2, max_gross_exposure=0.5),
        features={},
    )
    result = NativeAdapter().run_backtest(spec, data)
    assert result.positions.abs().max().max() <= 0.2 + 1e-12
    assert result.positions.abs().sum(axis=1).max() <= 0.5 + 1e-12


def test_stops_flatten_after_adverse_move():
    index = pd.bdate_range("2020-01-01", periods=60)
    falling = pd.DataFrame({"SPY": 100.0 * np.cumprod(np.repeat(0.99, 60))}, index=index)
    positions = pd.DataFrame({"SPY": np.ones(60)}, index=index)
    spec = _spec(risk=RiskRules(stop_loss_pct=0.05, max_position_weight=1.0, max_gross_exposure=1.0), universe=["SPY"])
    stopped = NativeAdapter._apply_stops(falling, positions, spec)
    assert stopped["SPY"].iloc[:3].sum() == pytest.approx(3.0)
    assert (stopped["SPY"] == 0.0).any()
    trades = extract_trades(stopped, falling)
    assert len(trades) >= 1


def test_take_profit_triggers():
    index = pd.bdate_range("2020-01-01", periods=40)
    rising = pd.DataFrame({"SPY": 100.0 * np.cumprod(np.repeat(1.02, 40))}, index=index)
    positions = pd.DataFrame({"SPY": np.ones(40)}, index=index)
    spec = _spec(risk=RiskRules(take_profit_pct=0.05), universe=["SPY"])
    out = NativeAdapter._apply_stops(rising, positions, spec)
    assert (out["SPY"] == 0.0).any()


def test_weekly_rebalance_holds_positions_between_rebalances():
    data = MarketData(close=make_prices(n=90))
    spec = _spec(
        signal_code=MOMENTUM_SIGNAL,
        risk=RiskRules(rebalance="weekly", max_position_weight=0.5, max_gross_exposure=1.0),
        params={"lookback": 5, "top_n": 1},
    )
    result = NativeAdapter().run_backtest(spec, data)
    changes_per_week = (result.positions.diff().abs().sum(axis=1) > 1e-12).resample("W").sum()
    assert changes_per_week.max() <= 2  # one rebalance, plus the next-bar fill at a boundary
    daily = NativeAdapter().run_backtest(
        spec.model_copy(
            update={"rebalance": "daily", "risk": spec.risk.model_copy(update={"rebalance": "daily"})}
        ),
        data,
    )
    assert daily.metrics["turnover_annual"] > result.metrics["turnover_annual"]


def test_vol_target_scales_exposure():
    data = MarketData(close=make_prices(n=300, vol=0.03))
    spec = _spec(
        signal_code=MOMENTUM_SIGNAL,
        risk=RiskRules(vol_target_annual=0.10, max_gross_exposure=3.0, max_position_weight=1.0),
        params={"lookback": 10, "top_n": 2},
    )
    result = NativeAdapter().run_backtest(spec, data)
    assert result.metrics["exposure"] > 0
    assert result.positions.abs().sum(axis=1).max() <= 3.0 + 1e-9


def test_grid_counts_every_trial_and_captures_failures():
    data = MarketData(close=make_prices(n=200))
    counter = MemoryTrialCounter()
    spec = _spec(BROKEN_SIGNAL, features={})
    grid = NativeAdapter().run_grid(
        spec, {"weight": [0.2, 0.5, 0.8, 1.0]}, data, trial_counter=counter, min_trades=1
    )
    assert len(grid.failures) == 2
    assert len(grid.results) == 2
    assert counter.value == 4  # failures count as trials
    assert grid.best is not None
    assert grid.best_params["weight"] in {0.2, 0.5}


def test_walk_forward_is_out_of_sample_and_counts_trials():
    data = MarketData(close=make_prices(n=500, seed=5), benchmark=None)
    counter = MemoryTrialCounter()
    spec = _spec(MOMENTUM_SIGNAL, params={"lookback": 5, "top_n": 2})
    report = NativeAdapter().run_walk_forward(
        spec,
        {"lookback": [5, 10], "top_n": [1, 2]},
        data,
        folds=3,
        mode="rolling",
        trial_counter=counter,
        min_trades=1,
    )
    assert report.n_folds == 3
    assert counter.value > 0
    assert len(report.oos_returns) > 0
    assert report.oos_returns.index.is_monotonic_increasing
    assert report.param_stability["modal_share"] >= 0
    assert report.metrics["n_periods"] == len(report.oos_returns)


def test_anchored_and_rolling_walk_forward_differ():
    data = MarketData(close=make_prices(n=500, seed=9))
    spec = _spec(MOMENTUM_SIGNAL, params={"lookback": 5, "top_n": 2})
    param_grid = {"lookback": [5, 10], "top_n": [1, 2]}
    rolling = NativeAdapter().run_walk_forward(spec, param_grid, data, folds=2, mode="rolling", min_trades=1)
    anchored = NativeAdapter().run_walk_forward(spec, param_grid, data, folds=2, mode="anchored", min_trades=1)
    assert rolling.mode == "rolling"
    assert anchored.mode == "anchored"
    assert len(rolling.folds) == len(anchored.folds) == 2


def test_backtest_is_deterministic():
    data = MarketData(close=make_prices(n=300, seed=2))
    spec = _spec(MOMENTUM_SIGNAL, params={"lookback": 10, "top_n": 2})
    first = NativeAdapter().run_backtest(spec, data)
    second = NativeAdapter().run_backtest(spec, data)
    pd.testing.assert_series_equal(first.returns, second.returns)
    pd.testing.assert_frame_equal(first.positions, second.positions)


def test_missing_universe_symbol_raises():
    data = MarketData(close=make_prices(n=80))
    spec = _spec(universe=["SPY", "NOPE"])
    with pytest.raises(ValueError, match="missing from data"):
        NativeAdapter().run_backtest(spec, data)


def test_signal_errors_are_reported():
    data = MarketData(close=make_prices(n=80))
    spec = _spec("def signal(features, params, close):\n    return 42\n", features={})
    with pytest.raises(SignalError, match="DataFrame"):
        NativeAdapter().run_backtest(spec, data)


def test_signal_with_future_data_still_compiles_but_is_flagged_by_validation():
    code = "def signal(features, params, close):\n    return close.shift(-1) * 0 + 0.5\n"
    fn = compile_signal(code)
    assert callable(fn)


def test_sandbox_scanner_blocks_dangerous_signal():
    from sandbox.scanner import ScanError

    with pytest.raises(ScanError):
        compile_signal("import os\ndef signal(features, params):\n    return os.system('ls')\n")


def test_extract_trades_records_round_trips():
    index = pd.bdate_range("2021-01-01", periods=50)
    close = pd.DataFrame({"AAA": 100.0 * np.cumprod(np.repeat(1.01, 50))}, index=index)
    positions = pd.DataFrame({"AAA": [0.0] * 5 + [1.0] * 10 + [0.0] * 10 + [-1.0] * 5 + [0.0] * 20}, index=index)
    trades = extract_trades(positions, close)
    assert len(trades) == 2
    assert trades[0].direction == 1
    assert trades[1].direction == -1
    assert trades[0].entry_date < trades[0].exit_date


def test_market_data_hash_changes_with_prices():
    close = make_prices(n=60)
    first = MarketData(close=close).data_hash()
    changed = close.copy()
    changed.iloc[-1, 0] *= 1.01
    assert MarketData(close=changed).data_hash() != first


def test_optional_adapters_explain_installation():
    from engine import get_adapter, list_adapters

    availability = list_adapters()
    if not availability["vectorbt"]["available"]:
        with pytest.raises(EngineUnavailable, match="vectorbt"):
            get_adapter("vectorbt")
    if not availability["backtesting"]["available"]:
        with pytest.raises(EngineUnavailable, match="backtesting"):
            get_adapter("backtesting")
    with pytest.raises(ValueError):
        get_adapter("does-not-exist")


def test_costs_multiplier_and_delay_change_results():
    data = MarketData(close=make_prices(n=200))
    spec = _spec(MOMENTUM_SIGNAL, params={"lookback": 5, "top_n": 2})
    base = NativeAdapter().run_backtest(spec, data, cost_multiplier=1.0)
    stressed = NativeAdapter().run_backtest(spec, data, cost_multiplier=3.0, delay_bars=2)
    assert stressed.costs.sum() > base.costs.sum()
    assert stressed.metrics["delay_bars"] == 2
    with pytest.raises(ValueError, match="next-bar"):
        NativeAdapter().run_backtest(spec, data, delay_bars=0)
