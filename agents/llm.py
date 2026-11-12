"""LLM clients for agent reasoning.

Production uses the Anthropic Messages API with tool use. Prices are configurable and only used to
estimate spend for budget enforcement; they are not billing records. When no key is configured (or
``LLM_PROVIDER=mock``) a deterministic mock keeps the research loop fully functional offline: the
agents still run real tools and real validation, only the natural-language reasoning is scripted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

# USD per 1M tokens (input, output). Placeholders for budget estimates only.
PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (15.0, 75.0),
    "claude-sonnet-5-5": (3.0, 15.0),
    "claude-haiku-5-5": (0.8, 4.0),
}
DEFAULT_PRICE = (3.0, 15.0)


def cost_for(model: str, tokens_in: int, tokens_out: int) -> float:
    price_in, price_out = PRICING.get(model, DEFAULT_PRICE)
    return (tokens_in / 1_000_000) * price_in + (tokens_out / 1_000_000) * price_out


@dataclass
class LLMResponse:
    text: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str = "end_turn"


class LLMClient(Protocol):
    model: str

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 1200,
        temperature: float = 0.2,
    ) -> LLMResponse: ...


class AnthropicClient:
    def __init__(self, model: str, api_key: str, max_tokens: int = 1200) -> None:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("install the LLM extra: uv pip install -e '.[llm]'") from exc
        self.model = model
        self.max_tokens = max_tokens
        self._client = anthropic.Anthropic(api_key=api_key)

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.2,
    ) -> LLMResponse:
        response = self._client.messages.create(
            model=self.model,
            system=system,
            messages=messages,
            tools=tools or [],
            max_tokens=max_tokens or self.max_tokens,
            temperature=temperature,
        )
        texts: list[str] = []
        calls: list[dict[str, Any]] = []
        for block in response.content:
            if getattr(block, "type", "") == "text":
                texts.append(block.text)
            elif getattr(block, "type", "") == "tool_use":
                calls.append({"id": block.id, "name": block.name, "input": block.input})
        usage = response.usage
        tokens_in = int(getattr(usage, "input_tokens", 0))
        tokens_out = int(getattr(usage, "output_tokens", 0))
        return LLMResponse(
            text="\n".join(texts),
            model=self.model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_for(self.model, tokens_in, tokens_out),
            tool_calls=calls,
            stop_reason=str(getattr(response, "stop_reason", "end_turn")),
        )


class MockLLM:
    """Deterministic stand-in. It never fabricates results; agents compute those with tools."""

    def __init__(self, model: str = "mock-scripted") -> None:
        self.model = model

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.2,
    ) -> LLMResponse:
        prompt = str(messages[-1].get("content", "")) if messages else ""
        tokens_in = max(1, len(prompt) // 4)
        text = (
            "Offline reasoning policy: proceed with the deterministic template library, "
            "execute the validation gauntlet, and report failures without embellishment."
        )
        return LLMResponse(
            text=text,
            model=self.model,
            tokens_in=tokens_in,
            tokens_out=max(1, len(text) // 4),
            cost_usd=0.0,
        )


def build_llm(model: str, provider: str, api_key: str | None) -> LLMClient:
    if provider == "anthropic":
        if not api_key:
            raise RuntimeError("LLM_PROVIDER=anthropic requires ANTHROPIC_API_KEY")
        return AnthropicClient(model=model, api_key=api_key)
    return MockLLM()
