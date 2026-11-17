"""LLM clients for agent reasoning.

Production uses the Anthropic Messages API with tool use. Prices are configurable and only used to
estimate spend for budget enforcement; they are not billing records. When no key is configured (or
``LLM_PROVIDER=mock``) a deterministic mock keeps the research loop fully functional offline: the
agents still run real tools and real validation, only the natural-language reasoning is scripted.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

# Free models are the default when OpenRouter is selected.  `openrouter/free` is the
# maintained free-model router; the two concrete models are free fallbacks that support
# tools and structured outputs.  Keep this list short (OpenRouter accepts at most three
# entries in `models`).
OPENROUTER_FREE_MODEL = "openrouter/free"
OPENROUTER_FREE_FALLBACKS: tuple[str, ...] = (
    "inclusionai/ling-3.2-flash:free",
    "baidu/ernie-5.5-flash-preview:free",
)
OPENROUTER_APP_URL = "https://nullpoint-web.vercel.app"
OPENROUTER_APP_TITLE = "Nullpoint"

# USD per 1M tokens (input, output). Placeholders for budget estimates only.
PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (15.0, 75.0),
    "claude-sonnet-5-5": (3.0, 15.0),
    "claude-haiku-5-5": (0.8, 4.0),
    "openrouter/free": (0.0, 0.0),
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
    if model == OPENROUTER_FREE_MODEL or model.endswith(":free"):
        return 0.0
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
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse: ...

    def stream(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int = 1200,
        temperature: float = 0.2,
    ) -> Iterator[str]: ...


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
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        # The native Anthropic client does not take the OpenAI response_format object;
        # callers use OpenRouter for that capability.
        _ = response_format
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

    def stream(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int = 1200,
        temperature: float = 0.2,
    ) -> Iterator[str]:
        with self._client.messages.stream(
            model=self.model,
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        ) as stream:
            yield from stream.text_stream


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
        extra_body: dict[str, Any] | None = None,
        modern_tokens: bool = False,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.api_key = api_key
        self.extra_headers = extra_headers or {}
        self.extra_body = dict(extra_body or {})
        self.modern_tokens = modern_tokens

    @property
    def endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json", **self.extra_headers}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        return headers

    def _payload(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        max_tokens: int | None,
        temperature: float,
        response_format: dict[str, Any] | None,
    ) -> dict[str, Any]:
        token_field = "max_completion_tokens" if self.modern_tokens else "max_tokens"
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            token_field: max_tokens or self.max_tokens,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
        if response_format:
            payload["response_format"] = response_format
        if self.extra_body:
            extra = dict(self.extra_body)
            # OpenRouter's `models` list is the failover chain.  Do not also send a
            # single `model` field when the ordered list is present.
            if extra.get("models"):
                payload.pop("model", None)
            provider = dict(extra.get("provider") or {})
            if tools and provider and "require_parameters" not in provider:
                provider["require_parameters"] = True
                extra["provider"] = provider
            payload.update(extra)
        return payload

    def stream(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int = 1200,
        temperature: float = 0.2,
    ) -> Iterator[str]:
        """Yield text deltas from a server-sent-events chat completion."""
        payload = self._payload(
            system=system,
            messages=messages,
            tools=None,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format=None,
        )
        payload["stream"] = True
        with httpx.stream(
            "POST",
            self.endpoint,
            headers=self._headers(),
            json=payload,
            timeout=self.timeout,
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    return
                try:
                    event = json.loads(chunk)
                except json.JSONDecodeError:
                    continue
                for choice in event.get("choices") or []:
                    delta = choice.get("delta") or {}
                    text = delta.get("content")
                    if text:
                        yield str(text)

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.2,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        headers = self._headers()
        payload = self._payload(
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format=response_format,
        )
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
        actual_model = str(data.get("model") or self.model)
        return LLMResponse(
            text=str(message.get("content") or ""),
            model=actual_model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_for(actual_model, tokens_in, tokens_out),
            tool_calls=calls,
            stop_reason=str(choice.get("finish_reason") or "stop"),
        )


# The offline client has to stay role-aware and conversation-aware, otherwise every agent
# reads like the same robot and a multi-agent room has nothing to respond to.  These markers
# match the roster's system prompts without importing the agents package.
MOCK_ROLE_MARKERS: tuple[tuple[str, str], ...] = (
    ("research lead", "orchestrator"),
    ("propose testable hypotheses", "ideator"),
    ("data integrity", "data_engineer"),
    ("point-in-time-safe", "strategy_coder"),
    ("run the grid and the walk-forward", "backtester"),
    ("your job is to falsify", "skeptic"),
    ("quantify what can go wrong", "risk_analyst"),
    ("combine only validated", "portfolio_constructor"),
    ("write the tearsheet", "reporter"),
    ("keep the memory honest", "librarian"),
)

MOCK_SCRIPTS: dict[str, dict[str, Any]] = {
    "orchestrator": {
        "name": "Orchestrator",
        "stance": (
            "I am treating this as one hypothesis to be tested, not a conclusion, and I will only "
            "promote it if the red team's check comes back clean."
        ),
        "challenge": (
            "I want the mechanism named in one sentence before anyone writes code, because a story "
            "that cannot be stated plainly cannot be falsified."
        ),
        "checks": [
            ("Name the mechanism", "One sentence: who is on the other side and why the edge persists."),
            ("Register the falsifier", "State in advance which measurement would kill the idea."),
            ("Bound the search", "Pre-register the parameter grid so the trial count is honest."),
        ],
    },
    "ideator": {
        "name": "Ideator",
        "stance": (
            "There is a testable hypothesis here, but the interesting version is the conditional one: "
            "the effect should concentrate where positioning or liquidity is stretched."
        ),
        "challenge": (
            "A single unconditional average would hide exactly the regime where this works, and that "
            "is also where it is most likely to be luck."
        ),
        "checks": [
            ("Split by regime", "Does the effect survive in at least three distinct vol and trend regimes?"),
            ("Check the mechanism", "Is the driver positioning, financing, or a data artefact?"),
            ("Define the null", "What does the random-entry version of this trade look like?"),
        ],
    },
    "data_engineer": {
        "name": "Data Engineer",
        "stance": (
            "Before any of this is measurable, the data has to be point-in-time. Adjusted prices and a "
            "current-constituent universe will flatter almost any signal."
        ),
        "challenge": (
            "The sample here is survivorship-biased, so I would treat every result as an upper bound "
            "until the universe is reconstructed."
        ),
        "checks": [
            ("Point-in-time universe", "Rebuild constituents as of each date, including delisted names."),
            ("Corporate actions", "Confirm splits and dividends are handled on the correct date."),
            ("Coverage gaps", "Report missing bars per symbol instead of silently forward-filling."),
        ],
    },
    "strategy_coder": {
        "name": "Strategy Coder",
        "stance": (
            "This is implementable as a small, stateless signal: one feature panel, one ranking rule, "
            "and sizing left to the risk layer."
        ),
        "challenge": (
            "I would keep the signal free of any path-dependent state, because stateful code is where "
            "lookahead and silent position drift hide."
        ),
        "checks": [
            ("No future data", "Signals use bars up to t-1; fills happen at the next bar."),
            ("No hidden state", "The signal is a pure function of the feature frame."),
            ("One-bar delay test", "The edge must survive an extra bar of execution delay."),
        ],
    },
    "backtester": {
        "name": "Backtester",
        "stance": (
            "The grid is cheap; the honest trial count is not. I would run every pre-registered "
            "parameter set and count each one."
        ),
        "challenge": (
            "Reporting only the best configuration is how a search becomes a story. The distribution "
            "across trials is the result."
        ),
        "checks": [
            ("Walk-forward", "Rolling and anchored folds, with purging and an embargo."),
            ("Cost stress", "Same signal at two and three times modelled costs."),
            ("Turnover budget", "Check that the sigma of turnover is affordable, not just the mean."),
        ],
    },
    "skeptic": {
        "name": "Skeptic",
        "stance": (
            "My prior is that this is an artefact until the nulls say otherwise. The burden of proof "
            "sits with the strategy, not with the sceptic."
        ),
        "challenge": (
            "A convincing Sharpe on one split is the least interesting number in the report; the "
            "probability of backtest overfitting is the one that decides it."
        ),
        "checks": [
            ("Signal shuffle", "Keep positions and costs, shuffle returns: does the edge vanish?"),
            ("PBO", "Is the in-sample winner also the out-of-sample winner across CSCV splits?"),
            ("Deflated Sharpe", "Does it clear the expected maximum of the trial family?"),
        ],
    },
    "risk_analyst": {
        "name": "Risk Analyst",
        "stance": (
            "The question I care about is what this does in the worst 5% of days, not what it averages."
        ),
        "challenge": (
            "Concentration and correlation to what we already hold decide whether this is diversifying "
            "or just levered beta."
        ),
        "checks": [
            ("Drawdown path", "Report max drawdown and time under water, not just Sharpe."),
            ("Correlation", "Cap correlation with existing candidates before sizing."),
            ("Capacity", "Estimate the dollar volume this turnover can absorb."),
        ],
    },
    "portfolio_constructor": {
        "name": "Portfolio Constructor",
        "stance": (
            "One more strategy only helps if it changes the shape of the portfolio, not just the "
            "headline Sharpe."
        ),
        "challenge": (
            "I would size by hierarchical risk parity and let the correlation structure decide, rather "
            "than trusting an optimiser on estimated means."
        ),
        "checks": [
            ("Marginal contribution", "Does the new sleeve add return per unit of portfolio risk?"),
            ("Weight cap", "No single strategy above the concentration limit."),
            ("Rebalance cost", "Price the turnover the construction implies."),
        ],
    },
    "reporter": {
        "name": "Reporter",
        "stance": (
            "I would lead the write-up with what was measured and how it could be wrong, then the "
            "numbers with their caveats attached."
        ),
        "challenge": (
            "A tearsheet that omits the failed gates is marketing, not research."
        ),
        "checks": [
            ("State the sample", "Dates, universe, provider and whether the data is synthetic."),
            ("List the failures", "Every failed and skipped gate, in plain language."),
            ("No advice", "Describe evidence; do not recommend a position."),
        ],
    },
    "librarian": {
        "name": "Librarian",
        "stance": (
            "This connects to prior work on the desk; the failure modes recorded there should shape the "
            "next test rather than be rediscovered."
        ),
        "challenge": (
            "Repeating a test the desk has already falsified is the most expensive way to learn nothing."
        ),
        "checks": [
            ("Search the desk memory", "Has a variant of this already failed, and why?"),
            ("Record the mechanism", "Store the failure with its cause, not only its metric."),
            ("Cite or drop", "Unsourced claims stay hypotheses."),
        ],
    },
}


def _mock_role(system: str) -> str:
    lowered = system.lower()
    for marker, role in MOCK_ROLE_MARKERS:
        if marker in lowered:
            return role
    return "orchestrator"


def _mock_messages(messages: list[dict[str, Any]]) -> tuple[str, str, str]:
    """Return (question, previous speaker, previous text) from the visible transcript."""
    question = ""
    prior_name = ""
    prior_text = ""
    for message in reversed(messages):
        role = str(message.get("role") or "")
        content = str(message.get("content") or "")
        if role == "user" and not question:
            question = content
            continue
        if role == "assistant" and not prior_text:
            match = re.match(r"\[([^\]]+)\]\s*(.*)", content, re.S)
            if match:
                prior_name, prior_text = match.group(1).strip(), match.group(2).strip()
            else:
                prior_text = content
    return question, prior_name, prior_text


def _short(text: str, limit: int = 220) -> str:
    compact = " ".join(text.split())
    if not compact:
        return ""
    return compact[:limit] + ("…" if len(compact) > limit else "")


def mock_text(system: str, messages: list[dict[str, Any]]) -> str:
    """Deterministic, role-specific prose that responds to the earlier turns.

    The offline provider must not invent numbers. It reasons about the question, names the
    previous speaker's claim when there is one, and ends with a concrete next check.
    """
    role = _mock_role(system)
    script = MOCK_SCRIPTS.get(role, MOCK_SCRIPTS["orchestrator"])
    question, prior_name, prior_text = _mock_messages(messages)
    parts = [str(script["stance"])]
    if prior_text:
        who = prior_name or "the previous speaker"
        quoted = _short(prior_text, 180)
        parts.append(f"{who} argued: \u201c{quoted}\u201d {script['challenge']}")
    elif question:
        parts.append(str(script["challenge"]))
    if question:
        parts.append(f"On \u201c{_short(question, 160)}\u201d: this stays a hypothesis until the checks below pass.")
    checks = script["checks"]
    parts.append(f"Next check: {checks[0][1]}")
    text = " ".join(part for part in parts if part)
    if "```ui" in system or "ui block" in system.lower():
        specs: list[dict[str, Any]] = []
        # Only render numbers the context actually contained.  The pattern matches the desk
        # results block, so the card is grounded in stored backtests rather than invented.
        stored = re.search(
            r"- (?P<name>.+?) \[[a-z_]+\]: Sharpe (?P<sharpe>[-\d.]+), CAGR (?P<cagr>[-\d.]+), "
            r"Max drawdown (?P<dd>[-\d.]+), (?P<n>[\d]+) trades",
            system,
        )
        if stored:
            specs.append(
                {
                    "type": "metrics",
                    "title": f"Latest stored backtest \u00b7 {stored.group('name')}",
                    "items": [
                        {"label": "Sharpe", "value": stored.group("sharpe"), "hint": "annualised"},
                        {"label": "CAGR", "value": stored.group("cagr")},
                        {"label": "Max drawdown", "value": stored.group("dd")},
                        {"label": "Trades", "value": stored.group("n")},
                    ],
                }
            )
        specs.append(
            {
                "type": "checklist",
                "title": f"Next checks \u00b7 {script['name']}",
                "items": [{"label": label, "detail": detail} for label, detail in checks],
            }
        )
        text += "\n\n```ui\n" + json.dumps(specs) + "\n```"
    return text


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
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        _ = tools, max_tokens, temperature, response_format
        prompt = " ".join(str(message.get("content") or "") for message in messages)
        tokens_in = max(1, len(prompt) // 4)
        text = mock_text(system, messages)
        return LLMResponse(
            text=text,
            model=self.model,
            tokens_in=tokens_in,
            tokens_out=max(1, len(text) // 4),
            cost_usd=0.0,
        )

    def stream(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int = 1200,
        temperature: float = 0.2,
    ) -> Iterator[str]:
        """Emit the scripted reply in small chunks so streaming code paths are exercised."""
        _ = max_tokens, temperature
        text = mock_text(system, messages)
        for match in re.finditer(r"\S+\s*", text):
            yield match.group(0)


def build_llm(
    model: str,
    provider: str,
    api_key: str | None,
    *,
    base_url: str | None = None,
    timeout: float = 90.0,
    session_id: str | None = None,
    models: list[str] | None = None,
    provider_preferences: dict[str, Any] | None = None,
    context_compression: bool = True,
    app_url: str | None = None,
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
        extra_body: dict[str, Any] = {}
        extra_headers: dict[str, str] = {}
        is_openrouter = key == "openrouter"
        if is_openrouter:
            ordered = list(models or [])
            if not ordered and (model == OPENROUTER_FREE_MODEL or model.endswith(":free")):
                ordered = [model, *OPENROUTER_FREE_FALLBACKS]
            if ordered:
                extra_body["models"] = list(dict.fromkeys(ordered))[:3]
            provider_preferences = {"allow_fallbacks": True, **(provider_preferences or {})}
            extra_body["provider"] = provider_preferences
            if session_id:
                extra_body["session_id"] = session_id[:256]
            if context_compression:
                extra_body["plugins"] = [
                    {"id": "context-compression", "engine": "middle-out"}
                ]
            extra_headers = {
                "HTTP-Referer": app_url or OPENROUTER_APP_URL,
                "X-OpenRouter-Title": OPENROUTER_APP_TITLE,
            }
        return OpenAICompatibleClient(
            model=model,
            api_key=api_key,
            base_url=resolved,
            timeout=timeout,
            extra_headers=extra_headers,
            extra_body=extra_body,
            modern_tokens=is_openrouter,
        )
    raise ValueError(
        f"unknown LLM provider {provider!r}; use mock, anthropic, one of "
        f"{sorted(OPENAI_COMPATIBLE_BASE_URLS)} or custom with LLM_BASE_URL"
    )
