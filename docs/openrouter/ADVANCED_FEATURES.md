# OpenRouter Advanced Features — Project Cheatsheet

> Retrieved from the official OpenRouter documentation and OpenAPI spec on **2026-11-16**.
> Source snapshot lives beside this file:
>
> - [`openapi.json`](./openapi.json) — full OpenAPI 3.1 description (3.0 MB)
> - [`llms.txt`](./llms.txt) — root documentation index
> - [`docs-llms.txt`](./docs-llms.txt) and [`docs-full.txt`](./docs-full.txt) — documentation index / combined docs feed
> - [`api-reference-full.txt`](./api-reference-full.txt) — API-reference index
> - [`source/`](./source) — raw Markdown for the guides summarized here
>
> Where the prose docs and the OpenAPI schema disagree, **the schema wins**. In particular, several
> `route`/`transforms` examples still found in older blog posts are no longer current request fields.

## 1. Base endpoint and headers

All chat requests use the OpenAI-compatible endpoint:

```http
POST https://openrouter.ai/api/v1/chat/completions
Authorization: Bearer $OPENROUTER_API_KEY
Content-Type: application/json
HTTP-Referer: https://nullpoint-web.vercel.app
X-OpenRouter-Title: Nullpoint
```

Optional request headers:

| Header | Value | Purpose |
|---|---|---|
| `X-OpenRouter-Metadata` | `enabled` | Include router metadata (`openrouter_metadata`) in the response, including the resolved model and routing reason. |
| `x-session-id` | stable conversation/agent ID, max 256 chars | Sticky provider routing for cache reuse and observability. The body field `session_id` takes precedence if both are present. |

Minimum body:

```json
{
  "model": "anthropic/claude-sonnet-4.5",
  "messages": [
    { "role": "system", "content": "You are a research analyst." },
    { "role": "user", "content": "Explain the carry trade." }
  ]
}
```

Streaming adds `"stream": true`. The response is SSE (`data: {...}` lines, terminated by `data: [DONE]`).
Use `max_completion_tokens`, not the deprecated `max_tokens`, for new code.

## 2. Model routing and fallbacks

### Ordered model fallbacks — current API

`models` is an ordered priority list. The first model is tried first; later models are used when the
primary's providers are down, rate-limited, or refuse due to moderation.

```json
{
  "models": [
    "anthropic/claude-sonnet-4.5",
    "openai/gpt-4o",
    "google/gemini-2.5-flash"
  ],
  "messages": [{ "role": "user", "content": "..." }],
  "provider": { "allow_fallbacks": true }
}
```

Schema facts from `ChatModelNames`:

- Maximum **3** entries.
- Every entry is a model ID string.
- Billing uses the model that actually served the request; the response `model` field reports it.
- A `:free` model followed by a paid model can fall through to the paid model; list only `:free`
  models when the request must remain free.

With the OpenAI SDK, put `models` in `extra_body` because the SDK does not know the field:

```python
response = client.chat.completions.create(
    model="anthropic/claude-sonnet-4.5",
    messages=messages,
    extra_body={"models": ["openai/gpt-4o", "google/gemini-2.5-flash"]},
)
```

### `route: "fallback"` — deprecated alias

The OpenAPI schema still accepts `route` but marks it deprecated:

```json
{ "route": "fallback" }
```

It is a backwards-compatible alias for `provider.sort.partition`:

- `"fallback"` → `"model"` (group endpoints by model before sorting; fallback models stay fallbacks)
- `"sort"` → `"none"` (sort all endpoints together)

New code should use the explicit provider object instead:

```json
{
  "provider": {
    "sort": { "by": "price", "partition": "model" }
  }
}
```

Do **not** use `route` as the primary way to implement failover. Use `models` for model failover and
`provider.sort.partition` for endpoint ordering.

### Automatic model selection — `openrouter/auto`

Use the auto router when the application should not choose a model:

```json
{
  "model": "openrouter/auto",
  "messages": [{ "role": "user", "content": "..." }]
}
```

The response reports the chosen model in `model`. Auto routing considers task type, difficulty,
latency sensitivity, vision/code needs, live prices, cache status, provider health, and your provider
preferences. Session signals carry forward across turns.

The equivalent explicit plugin (do not combine it with the `openrouter/auto` model alias) is:

```json
{
  "plugins": [
    {
      "id": "auto-router",
      "cost_tier": "low",
      "allowed_models": ["anthropic/*", "openai/*"],
      "excluded_models": ["openai/gpt-4o"],
      "pin_model": false
    }
  ]
}
```

`cost_tier` values: `low`, `medium`, `high`, `xhigh`, `max`. Lower tiers favor cheaper models.
`pin_model: true` reuses the previous assistant message's model on later turns. `allowed_models` and
`excluded_models` support wildcards; exclusions win.

To inspect the decision, send `X-OpenRouter-Metadata: enabled` and read
`openrouter_metadata.pipeline` (`resolved_models`, candidate estimates, `reason`).

### Anthropic Messages fallback parameter

The `/api/v1/messages` endpoint accepts:

```json
{
  "model": "anthropic/claude-sonnet-4.5",
  "max_tokens": 1024,
  "fallbacks": [{ "model": "anthropic/claude-opus-4.1" }],
  "messages": [{ "role": "user", "content": "..." }]
}
```

Limits:

- At most **3** fallback entries.
- Each entry accepts only `model`; per-attempt overrides are rejected with HTTP 400.
- `fallbacks` cannot be combined with `models`; doing so returns HTTP 400.
- This is OpenRouter-side routing, not Anthropic's server-side fallback feature.

## 3. Provider routing

The `provider` object controls which upstream endpoint serves a model. It is independent of model
selection.

```json
{
  "model": "meta-llama/llama-3.3-70b-instruct",
  "provider": {
    "sort": "throughput",
    "order": ["deepinfra", "fireworks"],
    "only": ["deepinfra", "fireworks", "together"],
    "ignore": ["openai"],
    "allow_fallbacks": true,
    "require_parameters": true,
    "data_collection": "deny",
    "zdr": true,
    "quantizations": ["fp8", "bf16"],
    "max_price": {
      "prompt": "3.00",
      "completion": "15.00"
    },
    "preferred_max_latency": 5,
    "preferred_min_throughput": 100
  },
  "messages": [{ "role": "user", "content": "..." }]
}
```

### Sorting

Simple form — one of:

```json
{ "provider": { "sort": "price" } }
{ "provider": { "sort": "throughput" } }
{ "provider": { "sort": "latency" } }
{ "provider": { "sort": "exacto" } }
```

Explicit form:

```json
{
  "provider": {
    "sort": {
      "by": "price",
      "partition": "model"
    }
  }
}
```

`partition`:

- `"model"` — group endpoints by model before sorting; this preserves fallback-model boundaries.
- `"none"` — sort every eligible endpoint together across models.

When `order` is set, it wins over `sort`. When `sort` is set, OpenRouter disables its normal
load-balancing behavior and tries providers in that order.

### Provider allow/deny lists

| Field | Meaning |
|---|---|
| `order` | Prefer these provider slugs in order, then continue to other eligible providers unless fallbacks are disabled. |
| `only` | Allow only these providers for this request. Narrows account-wide allowed providers; can reduce recovery options. |
| `ignore` | Skip these providers; merged with the account-wide ignore list. |
| `allow_fallbacks` | `true` (default) permits another provider when the primary is unavailable; `false` returns the upstream error instead. Private endpoints remain eligible. |
| `require_parameters` | Route only to endpoints that support every parameter in the request. Essential for structured outputs, tools, and other optional capabilities. |

Provider slugs can target a specific endpoint variant with `/`, for example `amazon-bedrock/claude-on-aws`.
Use the copy button on a model's provider section to get the exact slug.

### Quantizations

Allowed values:

```text
int4, int8, fp4, mxfp4, nvfp4, fp6, fp8, mxfp8, fp16, bf16, fp32, unknown
```

```json
{ "provider": { "quantizations": ["fp8", "bf16"] } }
```

### Price, latency, and throughput constraints

`max_price` values are strings in **USD per million tokens** for `prompt` and `completion` (also
`request`, `image`, and `audio` where applicable). A request fails if no eligible endpoint is at or
below the cap.

`preferred_max_latency` and `preferred_min_throughput` are preferences, not hard filters. Numeric
values apply to p50; percentile objects can set separate cutoffs. Endpoints outside the preference may
still be used but are deprioritized.

### Privacy controls

```json
{
  "provider": {
    "data_collection": "deny",
    "zdr": true,
    "enforce_distillable_text": true
  }
}
```

- `data_collection: "deny"` — only providers that do not store/train on data.
- `zdr: true` — only zero-data-retention endpoints.
- `enforce_distillable_text: true` — only models whose authors allow text distillation.

These can return an error when no endpoint satisfies the constraint; surface that error rather than
silently dropping the requirement.

## 4. Message transforms and context compaction

### Current API — context-compression plugin

OpenRouter's current documentation exposes middle-out compression as a request plugin:

```json
{
  "model": "anthropic/claude-sonnet-4.5",
  "plugins": [
    {
      "id": "context-compression",
      "engine": "middle-out"
    }
  ],
  "messages": [{ "role": "user", "content": "..." }]
}
```

`engine` is optional; its only allowed value is `"middle-out"` and it is the default.
Disable compression explicitly with:

```json
{
  "plugins": [{ "id": "context-compression", "enabled": false }]
}
```

Behavior:

- Removes or truncates messages from the **middle** of the prompt until it fits the chosen model's
  context window.
- When the message-count limit is exceeded (for example Claude's 100,000-message cap), keeps half the
  messages from the start and half from the end.
- Selects a model whose context is at least half the required total (input + completion), falling back
  to the largest available context.
- Is enabled by default for endpoints with **8,192 tokens or fewer** of context. Disable it explicitly
  there if truncation is unacceptable.
- Is skipped for image-generation-only models to preserve reference images.

### Legacy `transforms: ["middle-out"]` — not in the current schema

Older OpenRouter examples used:

```json
{ "transforms": ["middle-out"] }
```

`transforms` does **not** appear in the current OpenAPI `ChatRequest` schema, and the current message-
transforms guide documents only the `context-compression` plugin. Do not add `transforms` to new code.
If an older deployed client still sends it, migrate it to the plugin rather than relying on undocumented
compatibility.

For application-level history compaction, compact before sending: keep the system prompt, the most
recent turns, and a structured summary of older turns. Use the plugin as a safety net for oversized
prompts, not as a substitute for a deliberate history policy.

## 5. Prompt caching

Caching is provider-specific; OpenRouter translates compatible markers where it can, but TTLs are not
translated between OpenAI and Anthropic styles.

### Sticky routing

Use `session_id` (or `x-session-id`) so related requests hit the same provider and can reuse the cache:

```json
{
  "model": "anthropic/claude-sonnet-4.5",
  "session_id": "thread-123",
  "messages": [{ "role": "user", "content": "..." }]
}
```

### Anthropic-style `cache_control`

Automatic caching (recommended for multi-turn chats) — top-level directive:

```json
{
  "model": "anthropic/claude-sonnet-4.5",
  "cache_control": { "type": "ephemeral" },
  "messages": [
    { "role": "system", "content": "Long shared instructions..." },
    { "role": "user", "content": "What changed?" }
  ]
}
```

One-hour TTL:

```json
{ "cache_control": { "type": "ephemeral", "ttl": "1h" } }
```

Explicit block-level breakpoint:

```json
{
  "model": "anthropic/claude-sonnet-4.5",
  "messages": [
    {
      "role": "system",
      "content": [
        { "type": "text", "text": "Shared instructions" },
        {
          "type": "text",
          "text": "Large reusable document...",
          "cache_control": { "type": "ephemeral", "ttl": "1h" }
        }
      ]
    }
  ]
}
```

Facts:

- `cache_control` type is `"ephemeral"`; TTL is `"5m"` (default) or `"1h"`.
- At most **four** explicit breakpoints for Claude.
- Minimum cacheable prompt length varies by Claude model (often 1,024 or 2,048 tokens; some newer
  models 512).
- Cache writes cost more than normal input (Anthropic: 1.25× for 5m, 2× for 1h); reads cost less.
- Prefer one large stable prefix (system prompt + documents) and append changing turns after it.

### OpenAI-style explicit caching

Only OpenAI GPT-5.6 and newer support these controls:

```json
{
  "model": "openai/gpt-5.6",
  "prompt_cache_key": "thread-123",
  "prompt_cache_options": {
    "mode": "explicit",
    "ttl": "30m"
  },
  "messages": [
    {
      "role": "system",
      "content": [
        {
          "type": "text",
          "text": "Shared instructions and tools...",
          "prompt_cache_breakpoint": { "mode": "explicit", "ttl": "12h" }
        }
      ]
    },
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "Conversation so far...",
          "prompt_cache_breakpoint": { "mode": "explicit" }
        }
      ]
    }
  ]
}
```

- `prompt_cache_options.mode: "explicit"` disables OpenAI-managed breakpoints; only marked blocks are cached.
- `ttl`: `"30m"` (default) or `"12h"`; per-breakpoint TTL overrides the request-level TTL.
- `prompt_cache_breakpoint` is the OpenAI-style block marker; `cache_control` is the Anthropic-style
  marker. OpenRouter converts between them when routing, but does not translate TTLs.
- Minimum automatic prompt size is 1,024 tokens. GPT-5.6+ charges cache writes at 1.25× input price.
- Disable caching per request with `"prompt_cache_options": { "mode": "explicit" }` and no breakpoints,
  or omit the OpenAI controls when caching is not useful.

### Reading cache usage

Chat Completions:

```json
{
  "usage": {
    "prompt_tokens": 4200,
    "completion_tokens": 180,
    "prompt_tokens_details": {
      "cached_tokens": 3900,
      "cache_write_tokens": 0
    }
  }
}
```

Responses API reports the same details under `usage.input_tokens_details`.
Log `cached_tokens` and `cache_write_tokens` so cache hit rate and write cost are visible.

## 6. Structured outputs

### Chat Completions — JSON Schema

```json
{
  "model": "openai/gpt-4o",
  "messages": [
    { "role": "user", "content": "Extract the thesis and risks from this note." }
  ],
  "response_format": {
    "type": "json_schema",
    "json_schema": {
      "name": "research_brief",
      "description": "Structured research brief",
      "strict": true,
      "schema": {
        "type": "object",
        "properties": {
          "thesis": { "type": "string" },
          "risks": {
            "type": "array",
            "items": { "type": "string" }
          },
          "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1
          }
        },
        "required": ["thesis", "risks", "confidence"],
        "additionalProperties": false
      }
    }
  },
  "provider": {
    "require_parameters": true
  }
}
```

Rules:

- `json_schema.name` is required: `a-z`, `A-Z`, `0-9`, `_`, `-`, max 64 chars.
- `strict: true` requests strict adherence; use `additionalProperties: false` and list every required
  field.
- Set `provider.require_parameters: true` so the request is not routed to an endpoint that ignores
  `response_format`. Structured-output support is per endpoint, not just per model.
- The model returns a JSON object (not a JSON string).
- This is supported by a subset of models/endpoints; check the model's supported parameters before
  relying on it for production extraction.
- For plain JSON mode use `{ "response_format": { "type": "json_object" } }`; still instruct the model
  to emit JSON and validate the result.

### Responses API

The Responses API uses the same JSON Schema shape under `text.format`:

```json
{
  "model": "openai/gpt-4o",
  "input": "Extract the thesis.",
  "text": {
    "format": {
      "type": "json_schema",
      "name": "research_brief",
      "strict": true,
      "schema": {
        "type": "object",
        "properties": { "thesis": { "type": "string" } },
        "required": ["thesis"],
        "additionalProperties": false
      }
    }
  }
}
```

Validate parsed output against the schema in application code even when `strict: true`; a provider
error or truncation can still produce invalid JSON.

## 7. Recommended Nullpoint request policy

These are the exact parameter choices to use in this project unless a feature has a documented reason
to differ.

When `LLM_PROVIDER=openrouter`, configure the existing model slots with **namespaced OpenRouter IDs**
(`anthropic/claude-opus-5-5`, `openai/gpt-4o-mini`, and so on), not the bare IDs used by the native
Anthropic client. Bare `claude-*` IDs only belong on `LLM_PROVIDER=anthropic`.

### Free-first defaults

This project uses **free OpenRouter models by default** (`LLM_PROVIDER=auto` with an OpenRouter key,
and all four `MODEL_*` slots left at `auto`):

- Primary model: `openrouter/free`
- Ordered free fallbacks (max three total): `inclusionai/ling-3.2-flash:free`,
  `baidu/ernie-5.5-flash-preview:free`
- No paid model is placed in the normal fallback chain.
- The adapter sends `provider.allow_fallbacks: true`, a stable `session_id`, and the current
  `context-compression` plugin.
- Free model usage is estimated at **$0** for the budget guard; the actual response `model` is
  persisted so a paid fallback could never be silently recorded as free.

### Shared headers

```http
Authorization: Bearer $OPENROUTER_API_KEY
Content-Type: application/json
HTTP-Referer: https://nullpoint-web.vercel.app
X-OpenRouter-Title: Nullpoint
```

Add `X-OpenRouter-Metadata: enabled` only for diagnostics; it adds response payload but does not change
routing.

### Long research threads

The adapter already sends the compression plugin and `session_id` for every OpenRouter request. For a
raw request, add them only when the thread may exceed the chosen model's context:

```json
{
  "session_id": "thread-<id>",
  "plugins": [{ "id": "context-compression", "engine": "middle-out" }]
}
```

Free models may not support prompt caching, so `cache_control` is **not** sent by default. If you opt
into a paid Anthropic route, add `cache_control`; if you opt into a supporting OpenAI model, add
`prompt_cache_key` / `prompt_cache_options` / `prompt_cache_breakpoint`. Do not send both styles to the
same request; choose based on the primary model family, and let OpenRouter translate block markers only
when necessary.

### Streaming

```json
{
  "stream": true
}
```

OpenRouter returns full usage details in streaming responses; `stream_options.include_usage` is marked
**deprecated** in the current schema and has no effect, so omit it. Parse SSE `data:` frames, handle
`[DONE]`, and accumulate `usage` from the final frame. A stream that ends without `[DONE]` is an error;
do not treat partial text as a completed answer.

## 8. Error and fallback behavior

`models` fallbacks trigger on:

- provider downtime
- rate limits
- context-length validation errors
- moderation refusals for filtered models

Only one fallback chain is attempted; if the last model fails, OpenRouter returns that error. There is
no infinite retry loop. The default chain contains only `:free` models, so a failed request cannot
silently become a paid request. On the application side:

- Retry only transport/5xx errors, with bounded exponential backoff and idempotency where possible.
- Do not retry a 4xx parameter/schema error without fixing the request.
- If `models` contains a paid model after a `:free` model, a fallback can incur charges; budget checks
  must account for the actual response `model`.
- Persist the response `model` and usage so spend and routing are auditable.

## 9. Version notes / discrepancies to watch

| Topic | Older examples | Current schema/docs |
|---|---|---|
| Context compaction | `transforms: ["middle-out"]` | `plugins: [{ "id": "context-compression", "engine": "middle-out" }]`; `transforms` is absent from `ChatRequest` |
| Model fallback routing | `route: "fallback"` | `models: [...]`; `route` is deprecated and aliases `provider.sort.partition` |
| Auto model | `openrouter/auto` | Still valid as `model`; equivalent explicit `auto-router` plugin also available |
| Provider sorting | `provider.sort: "price"` | Still valid; explicit object `{ "by": "price", "partition": "model" }` adds partition control |
| Prompt caching | Anthropic `cache_control` only | Anthropic-style blocks and OpenAI-style `prompt_cache_breakpoint`/`prompt_cache_options`; TTLs are not translated |
| Max fallback models | unbounded lists in old examples | `ChatModelNames` max **3**; Anthropic `fallbacks` max **3** |

Check [`openapi.json`](./openapi.json) before adding a parameter that is not shown here. The spec is the
authoritative request/response contract; the prose guides explain behavior.
