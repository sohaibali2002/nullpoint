# Agent Instructions

## OpenRouter: mandatory reference check

Before writing or editing **any AI client code**, implementing **dynamic model switching**, or building
**chat history compaction**, read:

1. [`docs/openrouter/ADVANCED_FEATURES.md`](docs/openrouter/ADVANCED_FEATURES.md) — the project's
   consolidated, verified request schemas and routing/caching/structured-output policy.
2. [`docs/openrouter/openapi.json`](docs/openrouter/openapi.json) — the full OpenRouter OpenAPI 3.1
   contract. It is authoritative when it differs from prose examples.

Use the exact current parameters documented there. In particular:

- Use `models: [...]` (max 3) for model fallbacks; `route: "fallback"` is a deprecated alias for
  `provider.sort.partition`.
- Use `plugins: [{ "id": "context-compression", "engine": "middle-out" }]` for context compaction;
  do not add the legacy `transforms: ["middle-out"]` field, which is absent from the current schema.
- Use `provider` for sorting, allow/deny lists, quantization, privacy, price, latency, and throughput
  constraints.
- Use `cache_control` for Anthropic-style caching and `prompt_cache_key` /
  `prompt_cache_options` / `prompt_cache_breakpoint` for supporting OpenAI models.
- Use `response_format` with `type: "json_schema"`, `strict: true`, and
  `provider.require_parameters: true` for structured output.
- Never hardcode API keys. Read `OPENROUTER_API_KEY` from the environment.
- The default model stance is **free first**: `openrouter/free` with free fallbacks. Do not change
the defaults to a paid model without an explicit opt-in. Use namespaced OpenRouter model IDs when
`LLM_PROVIDER=openrouter`; bare Claude IDs belong to the native Anthropic provider.
- Keep the saved documentation and spec with the code when the API contract changes; update both this
  rule and the cheatsheet if OpenRouter deprecates a parameter.

This rule applies to agent reasoning, research synthesis, classification, embeddings-adjacent model
calls, streaming, retries, and any adapter that talks to an OpenAI-compatible or OpenRouter endpoint.
