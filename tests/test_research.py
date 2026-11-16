from __future__ import annotations

from typing import Any

import pytest
from api.config import Settings
from api.models import Organization, Sprint
from sqlalchemy.orm import Session

from agents import knowledge, search
from agents.bus import BudgetGuard, MessageBus
from agents.llm import build_llm
from agents.tools import AgentSpec, ToolContext


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


def _fake_search_post(url: str, **kwargs: Any) -> _Response:
    if url == search.TAVILY_URL:
        return _Response(
            {
                "results": [
                    {
                        "title": "Momentum evidence",
                        "url": "https://example.com/momentum?utm_source=x",
                        "content": "Cross-sectional momentum has survived multiple samples.",
                        "score": 0.9,
                    },
                    {
                        "title": "Crowding risk",
                        "url": "https://example.com/crowding",
                        "content": "Momentum crashes cluster after high volatility rebounds.",
                        "score": 0.7,
                    },
                ]
            }
        )
    if url == search.EXA_URL:
        return _Response(
            {
                "results": [
                    {
                        "title": "Momentum evidence duplicate",
                        "url": "https://example.com/momentum/",
                        "text": "A longer duplicate snippet about momentum.",
                        "score": 0.8,
                    }
                ]
            }
        )
    if url == search.FIRECRAWL_URL:
        return _Response(
            {
                "data": [
                    {
                        "title": "Implementation notes",
                        "url": "https://example.org/implementation",
                        "markdown": "Turnover and costs dominate the implementation.",
                    }
                ]
            }
        )
    raise AssertionError(f"unexpected search URL {url}")


def test_search_router_merges_providers_and_deduplicates(monkeypatch):
    monkeypatch.setattr(search.httpx, "post", _fake_search_post)
    router = search.SearchRouter(
        keys={"tavily": "t", "exa": "e", "firecrawl": "f"},
        cache_ttl_seconds=0,
        max_results=5,
    )
    assert router.configured == ["tavily", "exa", "firecrawl"]
    result = router.search("momentum in sector ETFs")
    assert result.errors == []
    assert len(result.hits) == 3
    urls = {hit.url.rstrip("/") for hit in result.hits}
    assert "https://example.com/momentum?utm_source=x" in urls
    assert len({search.canonical_url(hit.url) for hit in result.hits}) == 3


def test_search_without_keys_reports_honest_error(monkeypatch):
    monkeypatch.setattr(search.httpx, "post", _fake_search_post)
    router = search.SearchRouter(keys={})
    result = router.search("anything")
    assert result.hits == []
    assert result.errors
    assert "no search provider configured" in result.errors[0]


def test_deep_research_expands_queries():
    router = search.SearchRouter(keys={})
    assert len(search.expand_queries("trend following")) == 4
    result = search.deep_research("trend following", router=router, max_queries=2)
    assert result.query == "trend following"
    assert result.errors


def _seed(session: Session) -> tuple[Organization, Sprint]:
    org = Organization(name="Knowledge Desk", slug="knowledge-desk")
    session.add(org)
    session.flush()
    sprint = Sprint(org_id=org.id, objective="Research", universe=["SPY"])
    session.add(sprint)
    session.commit()
    return org, sprint


def test_knowledge_base_offline_roundtrip(db_session, tmp_path):
    org, sprint = _seed(db_session)
    settings = Settings(app_env="test", database_url="sqlite://", lake_root=tmp_path / "lake")
    ctx = ToolContext(
        db_session,
        org.id,
        sprint.id,
        settings=settings,
        bus=MessageBus(db_session, org.id, sprint.id),
        budget=BudgetGuard(1.0, 10, 2),
        allow_network=False,
    )
    added = ctx.knowledge_add(
        "Momentum notes",
        "Cross-sectional momentum has survived multiple samples. "
        + "It is sensitive to crowding and transaction costs. " * 80,
        source="internal",
        tags=["momentum", "risk"],
    )
    assert added["created"] > 0
    assert added["fallback"] is True
    hits = ctx.knowledge_search("momentum crowding costs", limit=3)
    assert hits
    assert hits[0]["title"] == "Momentum notes"
    assert hits[0]["embedding_model"] == "local-hash-256"
    stats = ctx.knowledge_stats()
    assert stats["chunks"] == added["created"]
    assert stats["voyage_configured"] is False


def test_voyage_embeddings_are_used_when_key_is_present(monkeypatch, db_session):
    org, _sprint = _seed(db_session)

    def fake_post(url: str, **kwargs: Any) -> _Response:
        assert url == knowledge.VOYAGE_URL
        inputs = kwargs["json"]["input"]
        return _Response(
            {
                "data": [
                    {"embedding": [float(len(text) % 7 + 1), 0.5, 0.25, 0.125]}
                    for text in inputs
                ]
            }
        )

    monkeypatch.setattr(knowledge.httpx, "post", fake_post)
    kb = knowledge.KnowledgeBase(
        db_session,
        org.id,
        api_key="voyage-test",
        chunk_chars=200,
        overlap_chars=20,
    )
    added = kb.add("Voyage document", "A mechanism with testable predictions. " * 30)
    assert added["fallback"] is False
    assert added["model"] == "voyage-3-large"
    hits = kb.search("testable predictions")
    assert hits
    assert hits[0]["embedding_model"] == "voyage-3-large"


def test_deep_research_tool_returns_knowledge_without_network(db_session, tmp_path):
    org, sprint = _seed(db_session)
    settings = Settings(app_env="test", database_url="sqlite://", lake_root=tmp_path / "lake")
    ctx = ToolContext(
        db_session,
        org.id,
        sprint.id,
        settings=settings,
        bus=MessageBus(db_session, org.id, sprint.id),
        budget=BudgetGuard(1.0, 10, 2),
        allow_network=False,
    )
    ctx.knowledge_add("Prior lesson", "Rejected ideas failed the PBO gate.", source="internal")
    spec = AgentSpec("ideator", "Ideator", "worker", "test", "system", ("deep_research",))
    payload = ctx.invoke(spec, "deep_research", query="PBO gate")
    assert payload["knowledge"]
    assert payload["hits"] == []
    assert "ALLOW_NETWORK=false" in payload["errors"][0]


def test_settings_parse_comma_separated_search_providers(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_PROVIDERS", "tavily, exa")
    settings = Settings(app_env="test", database_url="sqlite://")
    assert settings.search_providers == ["tavily", "exa"]


def test_llm_provider_key_resolution():
    settings = Settings(
        app_env="test",
        database_url="sqlite://",
        llm_provider="openrouter",
        openrouter_api_key="or-key",
    )
    assert settings.llm_key == "or-key"
    custom = Settings(
        app_env="test",
        database_url="sqlite://",
        llm_provider="custom",
        llm_api_key="generic-key",
    )
    assert custom.llm_key == "generic-key"


def test_anthropic_provider_still_requires_its_own_key():
    settings = Settings(app_env="test", database_url="sqlite://", llm_provider="anthropic")
    assert settings.llm_key is None
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        build_llm("model", "anthropic", None)
