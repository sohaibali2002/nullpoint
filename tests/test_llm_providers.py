from __future__ import annotations

from typing import Any

import pytest

from agents import llm as llm_module


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self.payload


def test_openai_compatible_client_normalizes_text_and_tool_calls(monkeypatch):
    calls: list[dict[str, Any]] = []

    def fake_post(url: str, **kwargs: Any) -> _Response:
        calls.append({"url": url, **kwargs})
        return _Response(
            {
                "choices": [
                    {
                        "message": {
                            "content": "Use a shorter lookback.",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "function": {
                                        "name": "web_search",
                                        "arguments": '{"query": "momentum crowding"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 120, "completion_tokens": 30},
            }
        )

    monkeypatch.setattr(llm_module.httpx, "post", fake_post)
    client = llm_module.build_llm(
        "openai/gpt-test",
        "openrouter",
        "secret",
        base_url="https://example.test/v1",
    )
    response = client.complete(
        system="Be skeptical.",
        messages=[{"role": "user", "content": "Review this strategy."}],
        tools=[{"type": "function", "function": {"name": "web_search"}}],
    )
    assert calls[0]["url"] == "https://example.test/v1/chat/completions"
    assert calls[0]["headers"]["authorization"] == "Bearer secret"
    assert calls[0]["json"]["messages"][0] == {"role": "system", "content": "Be skeptical."}
    assert response.text == "Use a shorter lookback."
    assert response.tokens_in == 120
    assert response.tokens_out == 30
    assert response.tool_calls[0]["name"] == "web_search"
    assert response.tool_calls[0]["input"] == {"query": "momentum crowding"}
    assert response.stop_reason == "tool_calls"


def test_openrouter_free_defaults_use_ordered_free_models(monkeypatch):
    from api.config import Settings

    settings = Settings(
        app_env="test",
        llm_provider="openrouter",
        openrouter_api_key="or-test-key",
        model_orchestrator="auto",
        model_worker="auto",
        model_cheap="auto",
        model_skeptic="auto",
    )
    assert settings.model_orchestrator == "openrouter/free"
    assert settings.model_worker == "openrouter/free"

    captured: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> _Response:
        captured.update({"url": url, **kwargs})
        return _Response(
            {
                "model": "inclusionai/ling-3.2-flash:free",
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            }
        )

    monkeypatch.setattr(llm_module.httpx, "post", fake_post)
    client = llm_module.build_llm(
        settings.model_worker,
        settings.llm_provider,
        settings.llm_key,
        session_id="thread-1",
    )
    response = client.complete(
        system="Be skeptical.",
        messages=[{"role": "user", "content": "Review this."}],
        tools=[{"type": "function", "function": {"name": "web_search"}}],
        response_format={"type": "json_object"},
    )

    payload = captured["json"]
    assert "model" not in payload
    assert payload["models"][0] == "openrouter/free"
    assert "inclusionai/ling-3.2-flash:free" in payload["models"]
    assert payload["provider"]["allow_fallbacks"] is True
    assert payload["provider"]["require_parameters"] is True
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["plugins"] == [{"id": "context-compression", "engine": "middle-out"}]
    assert payload["session_id"] == "thread-1"
    assert "max_completion_tokens" in payload
    assert "max_tokens" not in payload
    assert captured["headers"]["HTTP-Referer"]
    assert captured["headers"]["X-OpenRouter-Title"] == "Nullpoint"
    assert response.model == "inclusionai/ling-3.2-flash:free"
    assert response.cost_usd == 0.0


def test_auto_provider_prefers_openrouter_when_key_is_present():
    from api.config import Settings

    settings = Settings(
        app_env="test",
        llm_provider="auto",
        openrouter_api_key="or-test-key",
        model_orchestrator="auto",
        model_worker="auto",
        model_cheap="auto",
        model_skeptic="auto",
    )
    assert settings.llm_provider == "openrouter"
    assert settings.model_skeptic == "openrouter/free"


def test_free_models_cost_zero_even_with_numeric_pricing():
    assert llm_module.cost_for("openrouter/free", 10_000, 2_000) == 0.0
    assert llm_module.cost_for("vendor/model:free", 10_000, 2_000) == 0.0
    assert llm_module.cost_for("vendor/model", 1_000_000, 0) > 0


def test_custom_provider_requires_a_base_url():
    with pytest.raises(RuntimeError, match="LLM_BASE_URL"):
        llm_module.build_llm("some-model", "custom", "key")


def test_named_provider_default_base_url_and_mock(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> _Response:
        captured["url"] = url
        return _Response(
            {
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }
        )

    monkeypatch.setattr(llm_module.httpx, "post", fake_post)
    client = llm_module.build_llm("llama", "groq", "key")
    client.complete(system="s", messages=[{"role": "user", "content": "u"}])
    assert captured["url"] == "https://api.groq.com/openai/v1/chat/completions"

    mocked = llm_module.build_llm("anything", "mock", None)
    response = mocked.complete(system="s", messages=[{"role": "user", "content": "u"}])
    assert response.cost_usd == 0.0
    # The offline client stays role-aware: it answers in the requested agent's voice and
    # always ends with a concrete next check.
    assert "Next check:" in response.text
    streamed = "".join(
        mocked.stream(system="s", messages=[{"role": "user", "content": "u"}])
    )
    assert streamed.strip() == response.text.strip()


def test_unknown_provider_fails_loudly():
    with pytest.raises(ValueError, match="unknown LLM provider"):
        llm_module.build_llm("model", "not-a-provider", "key")
