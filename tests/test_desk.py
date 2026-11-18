from __future__ import annotations

from datetime import UTC, datetime

from api.services import desk


def test_detects_a_strategy_request() -> None:
    assert desk.detect_backtest_request("Create me a robust nasdaq trading strategy")
    assert desk.detect_backtest_request("please backtest a momentum strategy on AAPL")
    assert desk.detect_backtest_request("build me a low vol portfolio system")


def test_does_not_launch_the_engine_for_discussion() -> None:
    assert not desk.detect_backtest_request("How would you falsify this idea?")
    assert not desk.detect_backtest_request("What makes a strategy worth testing?")
    assert not desk.detect_backtest_request("hello")
    assert not desk.detect_backtest_request("")


def test_nasdaq_request_selects_the_nasdaq_universe() -> None:
    symbols, label = desk.parse_universe("Create me a robust nasdaq trading strategy")
    assert "QQQ" in symbols
    assert "NVDA" in symbols
    assert label == "Nasdaq-100 majors"


def test_explicit_tickers_win() -> None:
    symbols, label = desk.parse_universe("Backtest a trend strategy on AAPL MSFT")
    assert symbols == ["AAPL", "MSFT"]
    assert label == "custom tickers"


def test_window_parsing() -> None:
    assert desk.parse_window("backtest 2015-2019") == ("2015-01-01", "2019-12-31")
    assert desk.parse_window("over the last 5 years")[0] == f"{datetime.now(UTC).year - 5}-01-01"
    assert desk.parse_window("")[0] == f"{datetime.now(UTC).year - 8}-01-01"


def test_named_family_is_placed_first() -> None:
    keys = desk.parse_templates("create a regime strategy for the nasdaq")
    assert keys[0] == "vol_regime_risk_on"
    assert len(keys) <= desk.MAX_CANDIDATES


def test_result_message_only_contains_measured_numbers() -> None:
    run = desk.BacktestRun(
        strategy_id="abc123",
        name="Cross-sectional momentum",
        template="xs_momentum",
        symbols=["QQQ", "AAPL"],
        start="2018-01-02",
        visible_end="2024-06-28",
        sealed=["2024-07-01", "2025-12-31"],
        provider="yahoo",
        synthetic=False,
        metrics={"sharpe": 0.42, "cagr": 0.06, "max_drawdown": -0.2, "n_trades": 120.0},
        params={"lookback": 20},
        gates=[{"label": "pbo", "status": "fail", "detail": "0.6 vs <= 0.35"}],
        passed=False,
        score=0.31,
        status="rejected",
        candidates=[
            desk.Candidate(
                key="xs_momentum",
                name="Cross-sectional momentum",
                metrics={"sharpe": 0.42, "cagr": 0.06, "max_drawdown": -0.2},
                gates_passed=10,
                gates_total=16,
                failed=["pbo"],
            )
        ],
        chart=[{"date": "2018-01-02", "equity": 100.0, "benchmark": 100.0}],
        trials=24,
    )
    content = desk.message_content(run)
    assert "Sharpe 0.42" in content
    assert "pbo" in content
    assert "/strategies/abc123" in content
    assert "```ui" in content

    blocks = desk.result_blocks(run)
    kinds = [block["type"] for block in blocks]
    assert kinds[:2] == ["metrics", "chart"]
    assert blocks[1]["series"][0]["points"] == [["2018-01-02", 100.0]]
    assert blocks[-1]["link"] == "/strategies/abc123"

    summary = desk.summary_text(run)
    assert "abc123" in summary
    assert "PASSED" not in summary
