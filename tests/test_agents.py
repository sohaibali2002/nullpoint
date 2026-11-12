from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from api.config import Settings
from api.models import AgentMessage, Idea, Organization, Sprint, Strategy, TrialCounter, User
from sqlalchemy import select

from agents import DBTrialCounter, SprintRunner, roster_payload
from agents.bus import BudgetExceeded, BudgetGuard, MessageBus
from agents.embeddings import cosine, embed, is_duplicate
from agents.templates import TEMPLATES
from engine.base import MarketData
from engine.strategy import StrategySpec
from engine.validation import future_canary
from sandbox.scanner import scan_code
from tests.conftest import make_prices


def _seed_org(session) -> tuple[Organization, Sprint]:
    org = Organization(name="Test Desk", slug="test-desk")
    session.add(org)
    session.flush()
    user = User(org_id=org.id, email="research@test.dev", hashed_password="x")
    session.add(user)
    sprint = Sprint(
        org_id=org.id,
        objective="Research cross-sectional momentum on sector ETFs",
        universe=["AAA", "BBB", "CCC", "DDD"],
        budget_usd=2.0,
        config={"max_strategies": 2, "start": "2019-01-01", "end": "2021-12-31"},
    )
    session.add(sprint)
    session.commit()
    return org, sprint


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        app_env="test",
        database_url="sqlite://",
        lake_root=tmp_path / "lake",
        llm_provider="mock",
        max_agent_iterations=1,
        max_strategies_per_sprint=2,
        sprint_wall_clock_minutes=10,
        sprint_budget_usd=2.0,
    )


def test_roster_has_ten_agents_with_tool_whitelists():
    roster = roster_payload()
    assert len(roster) == 10
    keys = {agent["key"] for agent in roster}
    assert {
        "orchestrator", "ideator", "data_engineer", "strategy_coder", "backtester",
        "skeptic", "risk_analyst", "portfolio_constructor", "reporter", "librarian",
    } == keys
    assert all(agent["tools"] for agent in roster)


def test_trial_counter_is_org_scoped_and_persistent(db_session):
    org, _ = _seed_org(db_session)
    other = Organization(name="Other", slug="other")
    db_session.add(other)
    db_session.commit()
    counter = DBTrialCounter(db_session, org.id)
    assert counter.value == 0
    counter.increment(3)
    assert counter.value == 3
    assert DBTrialCounter(db_session, other.id).value == 0
    row = db_session.execute(select(TrialCounter).where(TrialCounter.org_id == org.id)).scalar_one()
    assert row.count == 3


def test_budget_guard_enforces_dollars_clock_and_kill():
    guard = BudgetGuard(1.0, wall_clock_minutes=10, max_iterations=2, kill_check=lambda: False)
    guard.spend(0.4, 100)
    assert guard.snapshot()["remaining_usd"] == pytest.approx(0.6)
    with pytest.raises(BudgetExceeded):
        guard.spend(0.7, 10)
    killed = BudgetGuard(10, 10, 2, kill_check=lambda: True)
    with pytest.raises(BudgetExceeded, match="kill"):
        killed.check()


def test_message_bus_persists_and_replays(db_session):
    org, sprint = _seed_org(db_session)
    bus = MessageBus(db_session, org.id, sprint.id)
    bus.emit("orchestrator", "planning", role="system")
    bus.emit("skeptic", "rejecting", tool_name="validate", tool_payload={"score": 0.2})
    messages = bus.messages()
    assert [m["seq"] for m in messages] == [1, 2]
    replay = MessageBus.replay(db_session, sprint.id)
    assert replay[0]["content"] == "planning"
    assert len(replay) == 2


def test_idea_embeddings_catch_near_duplicates():
    a = embed("cross sectional momentum in sector ETFs with volatility scaling")
    b = embed("sector ETF cross-sectional momentum scaled by volatility")
    c = embed("overnight reversal in FX futures around month end")
    assert cosine(a, b) > 0.6
    assert cosine(a, c) < 0.3
    duplicate, _, score = is_duplicate(
        "sector ETF cross-sectional momentum scaled by volatility",
        [("first", a)],
        threshold=0.5,
    )
    assert duplicate is True
    assert score > 0.5


def test_every_template_is_scanned_and_point_in_time():
    close = make_prices(n=400, symbols=("AAA", "BBB", "CCC", "DDD"), seed=4)
    for key, template in TEMPLATES.items():
        scan_code(template.signal_code)
        spec = StrategySpec(
            name=template.name,
            hypothesis=template.hypothesis,
            universe=["AAA", "BBB", "CCC", "DDD"],
            features=template.features,
            signal_code=template.signal_code,
            params=template.params,
            benchmark="AAA",
        )
        from engine.native import NativeAdapter

        result = NativeAdapter().run_backtest(spec, MarketData(close=close))
        assert len(result.returns) == len(close), key
        canary = future_canary(lambda frame, s=spec: _positions(s, frame), close)
        assert canary["passed"], f"{key} uses future data: {canary}"


def _positions(spec: StrategySpec, close: pd.DataFrame) -> pd.DataFrame:
    from engine.native import NativeAdapter

    return NativeAdapter().run_backtest(spec, MarketData(close=close)).positions


def test_sprint_runs_end_to_end_and_never_promotes_to_paper(db_session, tmp_path):
    org, sprint = _seed_org(db_session)
    runner = SprintRunner(
        db_session,
        sprint.id,
        settings=_settings(tmp_path),
        allow_network=False,
    )
    result = runner.run()
    assert result["status"] == "completed"
    assert result["sealed_holdout"]["sealed"][0] < result["sealed_holdout"]["sealed"][1]
    assert result["dataset"]["synthetic"] is True
    assert result["trials"] > 0
    assert result["budget"]["spent_usd"] <= result["budget"]["budget_usd"]

    messages = db_session.execute(
        select(AgentMessage).where(AgentMessage.sprint_id == sprint.id).order_by(AgentMessage.seq)
    ).scalars().all()
    assert len(messages) >= 10
    assert [m.seq for m in messages] == sorted(m.seq for m in messages)
    assert {m.agent for m in messages} >= {"orchestrator", "ideator", "data_engineer", "skeptic"}

    ideas = db_session.execute(select(Idea).where(Idea.org_id == org.id)).scalars().all()
    assert len(ideas) >= 1
    strategies = db_session.execute(select(Strategy).where(Strategy.org_id == org.id)).scalars().all()
    assert len(strategies) >= 1
    assert all(s.status not in {"paper", "live_ready"} for s in strategies)
    assert any(s.status in {"candidate", "rejected"} for s in strategies)

    # Every completed run leaves at least one tearsheet, including negative results.
    sheets = list(Path("artifacts/tearsheets").glob("*.html"))
    assert sheets, "expected a tearsheet artifact"


def test_sprint_respects_kill_switch(db_session, tmp_path):
    _org, sprint = _seed_org(db_session)
    sprint.kill_requested = True
    db_session.commit()
    runner = SprintRunner(db_session, sprint.id, settings=_settings(tmp_path), allow_network=False)
    result = runner.run()
    assert result["status"] == "killed"
    assert "kill" in str(result["error"]).lower()


def test_sprint_is_idempotent_once_completed(db_session, tmp_path):
    _org, sprint = _seed_org(db_session)
    sprint.status = "completed"
    sprint.result = {"status": "completed", "marker": 1}
    db_session.commit()
    runner = SprintRunner(db_session, sprint.id, settings=_settings(tmp_path), allow_network=False)
    assert runner.run()["marker"] == 1
