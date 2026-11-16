"""LLM clients for agent reasoning.

Production uses the Anthropic Messages API with tool use. Prices are configurable and only used to
estimate spend for budget enforcement; they are not billing records. When no key is configured (or
``LLM_PROVIDER=mock``) a deterministic mock keeps the research loop fully functional offline: the
agents still run real tools and real validation, only the natural-language reasoning is scripted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

# USD per 1M tokens (input, output). Placeholders for budget estimates only.
PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (15.0, 75.0),
    "claude-sonnet-5-5": (3.0, 15.0),
    "claude-haiku-5-5": (0.8, 4.0),
}
DEFAULT_PRICE = (3.0, 15.0)

# Named providers that expose an OpenAI-compatible /chat/completions endpoint.
# LLM_PROVIDER=custom + LLM_BASE_URL covers anything else (Azure, vLLM, LM Studio, ...).
OPENAI_COMPATIBLE_BASE_URLS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "groq": "https://api.groq.com/openai/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "mistral": "https://api.mistral.ai/v1",
    "xai": "https://api.x.ai/v1",
    "together": "https://api.together.xyz/v1",
    "fireworks": "https://api.fireworks.ai/inference/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "google": "https://generativelanguage.googleapis.com/v1beta/openai",
    "ollama": "http://localhost:11434/v1",
}


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


class OpenAICompatibleClient:
    """Any provider that implements the OpenAI chat-completions shape.

    This is intentionally SDK-free: one HTTP call, one normalized response.  That keeps the
    dependency surface small and makes Azure/vLLM/LM Studio/Ollama first-class rather than
    second-class adapters.  Token usage is used only for the existing budget guard.
    """

    def __init__(
        self,
        model: str,
        api_key: str | None,
        *,
        base_url: str,
        timeout: float = 90.0,
        max_tokens: int = 1200,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.api_key = api_key
        self.extra_headers = extra_headers or {}

    @property
    def endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.2,
    ) -> LLMResponse:
        headers = {"content-type": "application/json", **self.extra_headers}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "max_tokens": max_tokens or self.max_tokens,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
        response = httpx.post(self.endpoint, headers=headers, json=payload, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"{self.model}: provider returned no choices")
        choice = choices[0]
        message = choice.get("message") or {}
        calls: list[dict[str, Any]] = []
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            raw_args = function.get("arguments") or "{}"
            try:
                parsed = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError:
                parsed = {"raw": raw_args}
            calls.append(
                {"id": call.get("id"), "name": function.get("name"), "input": parsed}
            )
        usage = data.get("usage") or {}
        tokens_in = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        tokens_out = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        return LLMResponse(
            text=str(message.get("content") or ""),
            model=self.model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_for(self.model, tokens_in, tokens_out),
            tool_calls=calls,
            stop_reason=str(choice.get("finish_reason") or "stop"),
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


def build_llm(
    model: str,
    provider: str,
    api_key: str | None,
    *,
    base_url: str | None = None,
    timeout: float = 90.0,
) -> LLMClient:
    """Build the selected provider without importing every vendor SDK.

    ``mock`` is explicit and offline.  ``anthropic`` keeps the native Messages client.
    Everything else routes through the OpenAI-compatible client, which covers OpenAI,
    OpenRouter, Groq, DeepSeek, Mistral, xAI, Gemini's compatibility endpoint, Together,
    Fireworks, Ollama/vLLM/LM Studio, and any custom URL.
    """
    key = provider.strip().lower()
    if key == "mock":
        return MockLLM()
    if key == "anthropic":
        if not api_key:
            raise RuntimeError("LLM_PROVIDER=anthropic requires ANTHROPIC_API_KEY")
        return AnthropicClient(model=model, api_key=api_key)
    if key in OPENAI_COMPATIBLE_BASE_URLS or key in {"custom", "openai-compatible", "compatible"}:
        resolved = base_url or OPENAI_COMPATIBLE_BASE_URLS.get(key)
        if not resolved:
            raise RuntimeError(
                "LLM_PROVIDER=custom requires LLM_BASE_URL (for example https://api.openai.com/v1)"
            )
        if not api_key and key != "ollama":
            raise RuntimeError(
                f"LLM_PROVIDER={key} requires LLM_API_KEY or the provider-specific API key"
            )
        return OpenAICompatibleClient(model=model, api_key=api_key, base_url=resolved, timeout=timeout)
    raise ValueError(
        f"unknown LLM provider {provider!r}; use mock, anthropic, one of "
        f"{sorted(OPENAI_COMPATIBLE_BASE_URLS)} or custom with LLM_BASE_URL"
    )
