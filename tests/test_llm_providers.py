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
    assert "Offline reasoning" in response.text


def test_unknown_provider_fails_loudly():
    with pytest.raises(ValueError, match="unknown LLM provider"):
        llm_module.build_llm("model", "not-a-provider", "key")
