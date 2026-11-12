from __future__ import annotations

import pytest
from api.config import settings
from api.db import Base, get_db
from api.main import app
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
