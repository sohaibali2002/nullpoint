from __future__ import annotations

import pytest
from api.config import settings
from api.db import Base, get_db
from api.main import app
from api.security import AUTH_LIMITER, RATE_LIMITER
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def api(tmp_path, monkeypatch):
    import api.models  # noqa: F401

    engine = create_engine(
        f"sqlite:///{tmp_path / 'api.db'}", connect_args={"check_same_thread": False}, future=True
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    # The limiters are process-global; each test gets a fresh bucket so auth-heavy
    # integration tests do not make later tests flaky.
    AUTH_LIMITER._state.clear()
    RATE_LIMITER._state.clear()
    monkeypatch.setattr(settings, "job_runner", "sync")
    monkeypatch.setattr(settings, "allow_network", False)
    monkeypatch.setattr(settings, "llm_provider", "mock")
    monkeypatch.setattr(settings, "lake_root", tmp_path / "lake")
    monkeypatch.setattr(settings, "max_agent_iterations", 1)
    monkeypatch.setattr(settings, "sprint_wall_clock_minutes", 5)
    monkeypatch.setattr(settings, "sprint_budget_usd", 2.0)
    with TestClient(app) as client:
        client.session_factory = factory  # type: ignore[attr-defined]
        yield client
    app.dependency_overrides.clear()


def _register(client, email="quant@example.com", org="Skeptical Capital") -> str:
    response = client.post(
        "/auth/register",
        json={"org_name": org, "email": email, "password": "correct-horse-battery"},
    )
    assert response.status_code == 201, response.text
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_health_root_and_security_headers(api):
    health = api.get("/health")
    assert health.status_code == 200
    assert health.json()["safety"]["live_trading_enabled"] is False
    assert health.headers["X-Content-Type-Options"] == "nosniff"
    assert health.headers["X-Frame-Options"] == "DENY"
    root = api.get("/")
    assert root.json()["tagline"] == "Nothing ships until it beats the null."
    assert "not investment advice" in root.json()["disclaimer"].lower()
    assert api.get("/ready").status_code == 200
    assert api.get("/metrics").status_code == 200


def test_register_login_me_and_api_keys(api):
    token = _register(api)
    me = api.get("/auth/me", headers=_auth(token))
    assert me.status_code == 200
    assert me.json()["role"] == "owner"
    login = api.post("/auth/login", json={"email": "quant@example.com", "password": "correct-horse-battery"})
    assert login.status_code == 200
    assert login.json()["token_type"] == "bearer"
    wrong = api.post("/auth/login", json={"email": "quant@example.com", "password": "nope"})
    assert wrong.status_code == 401

    key_response = api.post("/auth/api-keys", json={"name": "ci"}, headers=_auth(token))
    assert key_response.status_code == 201
    raw_key = key_response.json()["key"]
    assert raw_key.startswith("np_")
    listing = api.get("/auth/api-keys", headers=_auth(token))
    assert all("hashed_key" not in row and "key" not in row for row in listing.json())
    key_auth = api.get("/auth/me", headers={"X-API-Key": raw_key})
    assert key_auth.status_code == 200

    assert api.get("/sprints").status_code == 401


def test_agent_chat_persists_and_is_tenant_scoped(api):
    token = _register(api, "chat@example.com", "Chat Desk")
    created = api.post("/chats", headers=_auth(token), json={"agent": "skeptic"})
    assert created.status_code == 201, created.text
    thread = created.json()["thread"]
    assert thread["agent"] == "skeptic"
    assert thread["agent_name"] == "Skeptic / Red Team"
    assert created.json()["messages"] == []

    first = api.post(
        f"/chats/{thread['id']}/messages",
        headers=_auth(token),
        json={"content": "How would you falsify a momentum idea?"},
    )
    assert first.status_code == 200, first.text
    payload = first.json()
    assert [message["role"] for message in payload["messages"]] == ["user", "assistant"]
    assert payload["messages"][0]["content"] == "How would you falsify a momentum idea?"
    assert payload["messages"][1]["content"]
    assert payload["messages"][1]["model"]
    assert payload["thread"]["title"].startswith("How would you falsify")

    second = api.post(
        f"/chats/{thread['id']}/messages",
        headers=_auth(token),
        json={"content": "What data would you check next?"},
    )
    assert second.status_code == 200, second.text
    assert len(second.json()["messages"]) == 4

    listing = api.get("/chats", headers=_auth(token)).json()
    assert len(listing) == 1
    assert listing[0]["message_count"] == 4
    assert listing[0]["last_message"]

    other = _register(api, "other-chat@example.com", "Other Desk")
    assert api.get(f"/chats/{thread['id']}", headers=_auth(other)).status_code == 404
    assert api.post(
        f"/chats/{thread['id']}/messages",
        headers=_auth(other),
        json={"content": "let me in"},
    ).status_code == 404
    assert api.get("/chats", headers=_auth(other)).json() == []

    assert api.post("/chats", headers=_auth(token), json={"agent": "not-an-agent"}).status_code == 400
    assert api.post(
        f"/chats/{thread['id']}/messages",
        headers=_auth(token),
        json={"content": "   "},
    ).status_code == 400

    deleted = api.delete(f"/chats/{thread['id']}", headers=_auth(token))
    assert deleted.status_code == 204
    assert api.get(f"/chats/{thread['id']}", headers=_auth(token)).status_code == 404
    assert api.get("/chats", headers=_auth(token)).json() == []


def test_research_room_debates_and_synthesizes(api):
    token = _register(api, "room@example.com", "Room Desk")
    created = api.post(
        "/chats",
        headers=_auth(token),
        json={"agents": ["ideator", "data_engineer", "skeptic"]},
    )
    assert created.status_code == 201, created.text
    thread = created.json()["thread"]
    assert thread["mode"] == "room"
    assert thread["agent_name"] == "Research room"
    assert thread["participants"] == ["ideator", "data_engineer", "skeptic"]
    assert len(thread["participant_names"]) == 3

    sent = api.post(
        f"/chats/{thread['id']}/messages",
        headers=_auth(token),
        json={"content": "Compare momentum and mean reversion for a crowded mega-cap.", "use_web": False},
    )
    assert sent.status_code == 200, sent.text
    payload = sent.json()
    assistants = [message for message in payload["messages"] if message["role"] == "assistant"]
    assert [message["agent"] for message in assistants] == [
        "ideator",
        "data_engineer",
        "skeptic",
        "orchestrator",
    ]
    assert [message["kind"] for message in assistants] == [
        "analysis",
        "analysis",
        "analysis",
        "synthesis",
    ]
    assert payload["messages"][0]["role"] == "user"
    assert len(payload["messages"]) == 5
    assert payload["thread"]["message_count"] == 5
    assert payload["thread"]["title"].startswith("Compare momentum")

    second = api.post(
        f"/chats/{thread['id']}/messages",
        headers=_auth(token),
        json={"content": "What evidence would change your mind?", "use_web": False},
    )
    assert second.status_code == 200, second.text
    assert len(second.json()["messages"]) == 10
    assert second.json()["messages"][-1]["agent"] == "orchestrator"
    assert second.json()["messages"][-1]["kind"] == "synthesis"

    listing = api.get("/chats", headers=_auth(token)).json()
    assert listing[0]["mode"] == "room"
    assert listing[0]["message_count"] == 10

    assert api.post(
        "/chats",
        headers=_auth(token),
        json={"agents": ["skeptic"]},
    ).status_code == 400
    assert api.post(
        "/chats",
        headers=_auth(token),
        json={"agents": ["skeptic", "not-an-agent"]},
    ).status_code == 400


def test_auto_research_chooses_specialists(api):
    token = _register(api, "auto@example.com", "Auto Desk")
    created = api.post("/chats", headers=_auth(token), json={"auto": True})
    assert created.status_code == 201, created.text
    thread = created.json()["thread"]
    assert thread["mode"] == "auto"
    assert thread["agent_name"] == "Auto research"
    assert thread["participants"] == []

    sent = api.post(
        f"/chats/{thread['id']}/messages",
        headers=_auth(token),
        json={"content": "What risk controls would keep a momentum idea from blowing up?", "use_web": False},
    )
    assert sent.status_code == 200, sent.text
    payload = sent.json()
    assert payload["thread"]["mode"] == "auto"
    assert "skeptic" in payload["thread"]["participants"]
    assert "risk_analyst" in payload["thread"]["participants"]
    assert payload["messages"][-1]["agent"] == "orchestrator"
    assert payload["messages"][-1]["kind"] == "synthesis"


def test_synthesis_serializes_structured_ui():
    from api.models import ChatMessage
    from api.services.chat import message_dict

    message = ChatMessage(
        id="m1",
        thread_id="t1",
        org_id="o1",
        role="assistant",
        agent="orchestrator",
        kind="synthesis",
        content=(
            "**Read**: Insider selling is noisy.\n\n"
            "**Where the room agrees**: Context matters.\n\n"
            "**Where it disagrees**: The magnitude.\n\n"
            "**What would change our mind**: A clean out-of-sample test.\n\n"
            "**Next sprint**: Test filing-date leakage."
        ),
    )
    blocks = message_dict(message)["ui"]
    assert blocks[0]["type"] == "brief"
    assert [section["heading"] for section in blocks[0]["sections"]] == [
        "Read",
        "Where the room agrees",
        "Where it disagrees",
        "What would change our mind",
        "Next sprint",
    ]


def test_chat_stream_emits_persisted_messages(api, monkeypatch):
    import api.routers.chat as chat_router

    token = _register(api, "stream@example.com", "Stream Desk")
    created = api.post("/chats", headers=_auth(token), json={"agent": "skeptic"}).json()
    monkeypatch.setattr(chat_router, "SessionLocal", api.session_factory)
    with api.stream(
        "POST",
        f"/chats/{created['thread']['id']}/messages/stream",
        headers=_auth(token),
        json={"content": "Give me one falsification test.", "use_web": False},
    ) as response:
        assert response.status_code == 200
        text = "".join(response.iter_text())
    assert "event: start" in text
    assert "event: message" in text
    assert "event: done" in text
    assert "event: error" not in text


def test_greetings_do_not_trigger_web_research():
    from api.services.chat import _query_is_researchable

    assert not _query_is_researchable("hi")
    assert not _query_is_researchable("hello there")
    assert not _query_is_researchable("thanks")
    assert _query_is_researchable("momentum")
    assert _query_is_researchable("what does insider buying predict")


def test_natural_language_universe_is_not_a_bare_ticker():
    from api.routers.research import LARGE_CAPS, SECTOR_ETFS, parse_universe

    assert parse_universe("Backtest insider buying on US large caps") == LARGE_CAPS
    assert parse_universe("Compare sector ETFs with a drawdown cap") == SECTOR_ETFS
    assert parse_universe("Use AAPL and MSFT") == ["AAPL", "MSFT"]
    assert "US" not in parse_universe("US stocks")


def test_general_chat_is_the_default_and_can_be_renamed(api):
    token = _register(api, "general@example.com", "General Desk")
    created = api.post("/chats", headers=_auth(token), json={})
    assert created.status_code == 201, created.text
    thread = created.json()["thread"]
    assert thread["mode"] == "auto"
    assert thread["agent_name"] == "Auto research"
    renamed = api.patch(
        f"/chats/{thread['id']}",
        headers=_auth(token),
        json={"title": "Insider-buying project"},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["title"] == "Insider-buying project"


def test_model_selection_routes_by_task():
    from api.config import Settings
    from api.services.chat import model_for_task

    config = Settings(app_env="test", llm_provider="mock")
    assert model_for_task("orchestrator", "anything", config) == config.model_orchestrator
    assert model_for_task("skeptic", "anything", config) == config.model_skeptic
    assert model_for_task("data_engineer", "short lookup", config) == config.model_cheap
    assert model_for_task("ideator", "write the backtest code", config) == config.model_worker
    assert model_for_task("ideator", "compare the thesis", config, deep=True) == config.model_orchestrator


def test_general_chat_accepts_an_attached_file(api):
    token = _register(api, "attach@example.com", "Attachment Desk")
    thread = api.post("/chats", headers=_auth(token), json={"auto": True}).json()["thread"]
    uploaded = api.post(
        f"/chats/{thread['id']}/upload",
        headers=_auth(token),
        files={"file": ("thesis.md", b"The thesis is that insider buying predicts drift after earnings. " * 8, "text/markdown")},
    )
    assert uploaded.status_code == 201, uploaded.text
    body = uploaded.json()
    assert body["chunks"] >= 1
    assert body["message"]["role"] == "user"
    assert body["message"]["kind"] == "attachment"
    assert body["message"]["ui"][0]["type"] == "attachment"
    assert body["message"]["ui"][0]["filename"] == "thesis.md"
    detail = api.get(f"/chats/{thread['id']}", headers=_auth(token)).json()
    assert any(message["kind"] == "attachment" for message in detail["messages"])


def test_deep_auto_room_uses_all_core_specialists(api):
    token = _register(api, "deep@example.com", "Deep Desk")
    thread = api.post("/chats", headers=_auth(token), json={"auto": True}).json()["thread"]
    sent = api.post(
        f"/chats/{thread['id']}/messages",
        headers=_auth(token),
        json={"content": "Compare a value and momentum blend under a drawdown constraint.", "use_web": False, "deep": True},
    )
    assert sent.status_code == 200, sent.text
    payload = sent.json()
    assert payload["thread"]["mode"] == "auto"
    assert set(payload["thread"]["participants"]) >= {
        "ideator",
        "data_engineer",
        "strategy_coder",
        "backtester",
        "risk_analyst",
        "portfolio_constructor",
        "skeptic",
    }
    assert len([message for message in payload["messages"] if message["kind"] == "analysis"]) == 7


def test_knowledge_upload_and_shared_library(api):
    token = _register(api, "knowledge@example.com", "Knowledge Desk")
    upload = api.post(
        "/knowledge/upload",
        headers=_auth(token),
        data={"title": "Momentum notes", "tags": "momentum,notes"},
        files={"file": ("momentum.md", b"Momentum works best when liquidity is stable and the trend is broad. " * 4, "text/markdown")},
    )
    assert upload.status_code == 201, upload.text
    assert upload.json()["chunks"] >= 1
    stats = api.get("/knowledge", headers=_auth(token)).json()
    assert stats["documents"] >= 1
    search = api.get("/knowledge/search?q=momentum%20liquidity", headers=_auth(token)).json()
    assert search["hits"]

    from api.models import Memory, Organization

    from agents.embeddings import embed
    from agents.knowledge import LIBRARY_ORG_ID

    session = api.session_factory()
    session.add(Organization(id=LIBRARY_ORG_ID, name="Library", slug="test-library", plan="library"))
    session.add(
        Memory(
            org_id=LIBRARY_ORG_ID,
            kind="knowledge",
            title="Shared value note",
            body="A margin of safety is the difference between price and intrinsic value.",
            embedding=embed("A margin of safety is the difference between price and intrinsic value."),
            payload={"source": "library", "embedding_model": "local-hash-256", "tags": ["value"]},
        )
    )
    session.commit()
    session.close()
    shared = api.get("/knowledge/search?q=margin%20of%20safety", headers=_auth(token)).json()
    assert any(hit["title"] == "Shared value note" for hit in shared["hits"])


def test_tenant_isolation(api):
    first = _register(api, "a@example.com", "Desk A")
    second = _register(api, "b@example.com", "Desk B")
    sprint = api.post(
        "/sprints",
        headers=_auth(first),
        json={"objective": "Test isolation", "universe": ["AAA", "BBB"], "max_strategies": 1},
    ).json()
    assert api.get(f"/sprints/{sprint['id']}", headers=_auth(second)).status_code == 404
    assert api.get("/strategies", headers=_auth(second)).json() == []


def test_sprint_launch_records_agents_and_never_papers(api):
    token = _register(api)
    response = api.post(
        "/sprints",
        headers=_auth(token),
        json={
            "objective": "Research cross-sectional momentum on these test symbols",
            "universe": ["AAA", "BBB", "CCC"],
            "max_strategies": 1,
            "start": "2019-01-01",
            "end": "2021-12-31",
        },
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["result"]["dataset"]["synthetic"] is True
    assert body["result"]["sealed_holdout"]["sealed"][0] < body["result"]["sealed_holdout"]["sealed"][1]
    sprint_id = body["id"]

    messages = api.get(f"/sprints/{sprint_id}/messages", headers=_auth(token)).json()
    assert messages["count"] >= 10
    assert messages["messages"][0]["seq"] == 1
    after = messages["messages"][3]["seq"]
    partial = api.get(f"/sprints/{sprint_id}/messages?after={after}", headers=_auth(token)).json()
    assert all(m["seq"] > after for m in partial["messages"])

    replay = api.get(f"/sprints/{sprint_id}/replay", headers=_auth(token)).json()
    assert replay["messages"][0]["content"]

    dashboard = api.get("/dashboard", headers=_auth(token)).json()
    assert dashboard["strategy_counts"]
    assert dashboard["safety"]["sealed_holdout"] is True
    assert dashboard["trials"] > 0

    strategies = api.get("/strategies", headers=_auth(token)).json()
    assert strategies
    assert all(s["status"] not in {"paper", "live_ready"} for s in strategies)
    leaderboard = api.get("/leaderboard", headers=_auth(token)).json()
    assert leaderboard["counts"]["total"] >= 1

    detail = api.get(f"/strategies/{strategies[0]['id']}", headers=_auth(token)).json()
    assert detail["spec"]["signal_code"]
    claim = api.get(f"/strategies/{strategies[0]['id']}/claim", headers=_auth(token)).json()
    assert claim["allowed"] is False
    assert claim["metrics"] is None
    assert claim["blockers"]
    attempts = api.get(f"/sprints/{sprint_id}/attempts", headers=_auth(token))
    assert attempts.status_code == 200
    validation = api.get(f"/strategies/{strategies[0]['id']}/validation", headers=_auth(token))
    assert validation.status_code in (200, 404)
    export = api.get(f"/strategies/{strategies[0]['id']}/export?format=python", headers=_auth(token)).json()
    assert "NativeAdapter" in export["content"]
    pine = api.get(f"/strategies/{strategies[0]['id']}/export?format=pine", headers=_auth(token)).json()
    assert "APPROXIMATE" in pine["content"]
    assert api.get(f"/sprints/{sprint_id}/kill", headers=_auth(token)).status_code == 405

    done = api.post(f"/sprints/{sprint_id}/kill", headers=_auth(token))
    assert done.status_code == 200
    assert done.json()["kill_requested"] is True


def test_approval_gate_and_paper_trading(api):
    token = _register(api)
    session = api.session_factory()
    from api.models import Experiment, Organization, Strategy

    org = session.query(Organization).one()
    strategy = Strategy(
        org_id=org.id,
        name="Approved candidate",
        status="candidate",
        spec={
            "name": "Approved candidate",
            "hypothesis": "test",
            "universe": ["AAA"],
            "signal_code": "def signal(features, params, close):\n    return close*0\n",
            "features": {},
            "params": {},
        },
        metrics={"sharpe": 1.2},
        tags=[],
    )
    session.add(strategy)
    session.commit()
    experiment = Experiment(
        org_id=org.id,
        strategy_id=strategy.id,
        kind="grid",
        returns=[0.001, -0.0005] * 120,
        dates=[str(d.date()) for d in __import__("pandas").bdate_range("2020-01-01", periods=240)],
        metrics={"sharpe": 1.2},
    )
    session.add(experiment)
    session.commit()
    strategy_id = strategy.id
    session.close()

    # Paper cannot start before approval.
    early = api.post("/paper", headers=_auth(token), json={"strategy_id": strategy_id})
    assert early.status_code == 409

    live = api.post(
        f"/strategies/{strategy_id}/approve",
        headers=_auth(token),
        json={"target": "live_ready"},
    )
    assert live.status_code == 400
    assert "no live-trading hook" in live.json()["detail"]

    approved = api.post(
        f"/strategies/{strategy_id}/approve",
        headers=_auth(token),
        json={"target": "paper", "note": "gates reviewed"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "paper"

    paper = api.post("/paper", headers=_auth(token), json={"strategy_id": strategy_id})
    assert paper.status_code == 201, paper.text
    run = paper.json()
    assert run["broker"] == "simulator"
    marked = api.post(f"/paper/{run['run_id']}/mark?steps=5", headers=_auth(token)).json()
    assert marked["drift"]["synthetic"] is True
    listed = api.get("/paper", headers=_auth(token)).json()
    assert any(item["run_id"] == run["run_id"] for item in listed)
    stopped = api.post(f"/paper/{run['run_id']}/stop", headers=_auth(token)).json()
    assert stopped["status"] == "stopped"


def test_data_endpoints_show_licences_and_survivorship(api):
    token = _register(api)
    providers = api.get("/data/providers", headers=_auth(token)).json()
    names = {p["name"] for p in providers["providers"]}
    assert {"yahoo", "stooq", "fred", "sec_edgar"} <= names
    yahoo = next(p for p in providers["providers"] if p["name"] == "yahoo")
    assert "PERSONAL" in yahoo["license_note"]
    universes = api.get("/data/universes", headers=_auth(token)).json()
    sector = next(u for u in universes if u["universe"] == "us_sector_etfs")
    assert sector["survivorship_bias"] is True
    assert "SURVIVORSHIP" in sector["warning"]
    assert api.get("/data/assets", headers=_auth(token)).status_code == 200
    assert api.get("/data/preview?symbol=SPY", headers=_auth(token)).status_code == 404


def test_settings_usage_models_and_agents(api):
    token = _register(api)
    settings_response = api.get("/settings", headers=_auth(token))
    assert settings_response.status_code == 200
    payload = settings_response.json()
    assert payload["runtime"]["live_trading_enabled"] is False
    assert payload["keys"]["anthropic"] is None
    assert "never returned" in str(payload["ignored_secrets"]).lower()

    patched = api.patch("/settings", headers=_auth(token), json={"sprint_budget_usd": 3.5})
    assert patched.status_code == 200
    usage = api.get("/usage", headers=_auth(token)).json()
    assert usage["monthly_budget_usd"] > 0
    agents = api.get("/agents", headers=_auth(token)).json()
    assert len(agents["agents"]) == 10
    models = api.get("/models", headers=_auth(token)).json()
    assert models["configured"]["skeptic"]
    assert api.post("/alerts/test", headers=_auth(token)).status_code == 200
    assert api.get("/safety").status_code == 200


def test_knowledge_endpoints_roundtrip(api):
    token = _register(api)
    stats = api.get("/knowledge", headers=_auth(token)).json()
    assert stats["chunks"] == 0
    assert stats["voyage_configured"] is False

    created = api.post(
        "/knowledge",
        headers=_auth(token),
        json={
            "title": "Momentum evidence",
            "body": "Cross-sectional momentum is sensitive to crowding and transaction costs.",
            "source": "internal",
            "tags": ["momentum", "costs"],
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["created"] > 0

    found = api.get("/knowledge/search", headers=_auth(token), params={"q": "momentum costs"})
    assert found.status_code == 200, found.text
    assert found.json()["hits"]
    assert found.json()["hits"][0]["title"] == "Momentum evidence"


def test_chat_launch_parses_sector_universe(api):
    token = _register(api)
    response = api.post(
        "/sprints/from-chat",
        headers=_auth(token),
        json={"message": "Research mean reversion on sector ETFs"},
    )
    assert response.status_code == 202, response.text
    assert response.json()["parsed_universe"][0] == "SPY"
    assert response.json()["status"] in {"completed", "running", "queued", "failed"}
