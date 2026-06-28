# Moddy — API Gateway

> **Read this before writing any code that calls OpenAI, DeepL, or any future external API.**
> No module calls provider APIs directly. Everything goes through `bot.gateway`.

---

## Overview

The `gateway/` package is a centralized, in-process API library shared by the Discord bot.
It enforces a single execution pipeline for every outbound API call:

```
quota check → resilience (timeout / retry / circuit breaker) → provider call → quota consume → log
```

This guarantees:
- **Every call is logged** — both to the staff webhook (`api_call` category) and to the `api_calls` PG table.
- **Quotas are enforced** before any provider is contacted.
- **Failures are typed** — consumers never see raw provider exceptions.
- **One source of truth** for all external API state (circuit breakers, quota counters in Redis).

---

## Quick Start

```python
# From any cog or module — never import openai or aiohttp for API calls
from gateway import QuotaTarget

# AI chat (quota-gated per guild)
result = await bot.gateway.ai.chat(
    system="You are a helpful assistant.",
    user="Summarize the following text: ...",
    model="gpt-4.1-nano",
    temperature=0.3,
    max_tokens=150,
    quota=[QuotaTarget.guild(guild.id, "ban_reason")],
    call_type="ban_reason",
    metadata={"guild_id": guild.id, "user_id": user.id},
)
# result: str (or dict if json_mode=True)

# AI embed (not quota-gated)
vectors = await bot.gateway.ai.embed(
    ["text one", "text two"],
    call_type="embed",
    metadata={"guild_id": guild.id},
)
# vectors: list[list[float]], order preserved

# Translation
out = await bot.gateway.translation.translate(
    text,
    target_lang="EN-US",
    quota=[QuotaTarget.user(user.id, "translation")],
    call_type="translation",
    metadata={"user_id": user.id},
)
# out: {"text": "...", "detected_source_language": "FR"}

# Availability check (for graceful degradation)
available = await bot.gateway.quota_available(QuotaTarget.guild(guild.id, "ban_reason"))
```

---

## Architecture

```
gateway/
├── __init__.py          # Gateway class (bot.gateway)
├── config.py            # GatewayConfig (from env vars)
├── errors.py            # Typed error hierarchy
├── spec.py              # CallSpec, QuotaTarget, QuotaScope
├── quota.py             # QuotaManager (Redis counters + PG limits)
├── resilience.py        # CircuitBreaker + retry/backoff
├── logger.py            # GatewayLogger (Redis buffer → PG + webhook)
├── executor.py          # GatewayExecutor (single execution path)
├── adapters/
│   ├── base.py          # AbstractAdapter + AdapterResult
│   ├── openai.py        # OpenAIAdapter (embed + chat)
│   └── deepl.py         # DeepLAdapter (translate)
└── clients/
    ├── ai.py            # AIClient (gw.ai.embed, gw.ai.chat)
    └── translation.py   # TranslationClient (gw.translation.translate)
```

The `Gateway` is instantiated in `bot.__init__` and started in `setup_hook` after Redis and the DB pool are ready.

---

## Quota System

### How it works

Quotas use **daily Redis counters** that auto-reset via UTC date-keyed keys (no cron job needed).

```
quota:{scope}:{key}:{type}:{YYYYMMDD}  →  integer counter
```

Key TTL is 48 hours; yesterday's key expires automatically.

### Quota scopes

| Scope | Example target | Use case |
|-------|---------------|----------|
| `guild` | `QuotaTarget.guild(guild_id, "ban_reason")` | Protect per-server AI budget |
| `user` | `QuotaTarget.user(user_id, "translation")` | Per-user translation cap |
| `global` | `QuotaTarget.global_("ban_reason")` | Platform-wide safety net |
| `custom` | `QuotaTarget.custom("campaign-X", "chatbot")` | Arbitrary bucket |

### Multi-target plans

A single call can debit multiple targets (e.g. guild + user). **All** must pass the check before the call is made. **All** are consumed on success. A failed call consumes nothing.

### DB tables

```sql
quota_limits    -- default limits per (scope, type, tier)
quota_overrides -- per-entity overrides (a VIP guild, a specific user)
```

Limits are cached in memory with a 60-second TTL to avoid PG hits on the hot path.

### Current limits (all unlimited — tighten via quota_overrides)

| call_type | scope | daily_limit |
|-----------|-------|-------------|
| `ban_reason` | guild | -1 (unlimited) |
| `ban_reason` | global | -1 |
| `translation` | user | -1 |
| `translation` | global | -1 |
| `chatbot` | guild | -1 |

To add a limit for a specific guild:
```sql
INSERT INTO quota_overrides (scope, key, type, daily_limit)
VALUES ('guild', '123456789', 'ban_reason', 100)
ON CONFLICT (scope, key, type) DO UPDATE SET daily_limit = EXCLUDED.daily_limit;
```

---

## Call Types

| `call_type` | Provider/op | Quota target(s) | Gated? |
|-------------|-------------|-----------------|:------:|
| `ban_reason` | openai/chat | guild | ✅ |
| `embed` | openai/embed | — | ❌ |
| `translation` | deepl/translate | user | ✅ |
| `chatbot` | openai/chat | guild + user | ✅ |
| `automod_embed` | openai/embed | — | ❌ |
| `automod_decision` | openai/chat | guild | ✅ |
| `automod_rules_check` | openai/chat | guild | ✅ |

---

## Resilience

### Timeouts
- `embed`: 10s
- `chat`: 30s
- `translate`: 15s

Override via env vars: `GATEWAY_TIMEOUT_EMBED`, `GATEWAY_TIMEOUT_CHAT`, `GATEWAY_TIMEOUT_TRANSLATE`.

### Retry + backoff
- 3 retries by default (configurable via `GATEWAY_MAX_RETRIES`)
- Exponential backoff: `base * 2^n + jitter`
- Retries on: 429, 5xx, timeouts, connection errors
- No retry on 4xx (except 429) — bad request won't fix itself

### Circuit breaker
- Opens after 5 consecutive failures (`GATEWAY_CB_FAILURE_THRESHOLD`)
- Stays open for 60s (`GATEWAY_CB_COOLDOWN`)
- Half-open probe on next call after cooldown
- In-memory per `(provider, operation)` pair

---

## Logging

### Staff webhook (`api_call` category)
Every call fires `bot.tech_logger.log_api_call(entry, request_payload=…, response_data=…)` — this uses the standard `TechLogger._card / _dispatch` pipeline, routing to the `LOG_WEBHOOK_API_CALL` webhook (falls back to `LOG_WEBHOOK_DEFAULT`).

The webhook message **attaches two text files**: `prompt_<cid>.txt` (the request that was sent — chat messages are rendered as `===== SYSTEM/USER =====` sections; other payloads as pretty JSON) and `response_<cid>.txt` (the raw response; bulky embedding vectors are summarized, not dumped). Files are capped at 200k chars and referenced by Components V2 `File` items on the card. The prompt/response are forwarded to the webhook **only** — they are not persisted in the Redis buffer or the `api_calls` PG table.

### PG table (`api_calls`)
All calls are buffered in a Redis list (`gateway:log_buffer`) and flushed to the `api_calls` PG table every 5 seconds by a background task. The hot path never touches PG directly.

Fields logged: `correlation_id`, `call_type`, `provider`, `operation`, `model`, `guild_id`, `user_id`, `quota_targets`, `tokens_prompt`, `tokens_completion`, `tokens_total`, `latency_ms`, `attempts`, `success`, `error_type`, `estimated_cost`.

### correlation_id
Pass the same `correlation_id` across multiple gateway calls that belong to the same user interaction. This links `embed` + `chat` calls in `api_calls` for the same moderation decision.

---

## Error Types

Consumers catch `GatewayError` or its subclasses — never raw provider exceptions.

```python
from gateway import (
    GatewayError,         # base — catch-all
    QuotaExceededError,   # .target: QuotaTarget that was exceeded
    RateLimitError,       # .provider, .retry_after
    APIUnavailableError,  # .provider — circuit open or retries exhausted
    GatewayTimeoutError,  # call timed out
    ProviderError,        # .provider, .status, .body — unrecoverable HTTP error
    ConfigurationError,   # missing API key, unknown operation
)
```

### Recommended consumer behavior

```python
from gateway import QuotaExceededError, APIUnavailableError

try:
    result = await bot.gateway.ai.chat(...)
except QuotaExceededError:
    # Graceful degradation: skip AI, fall back to regex/manual
    return None
except (APIUnavailableError, GatewayError):
    # Provider down: skip, requeue if needed
    return None
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | — | Required for AI features |
| `DEEPL_API_KEY` | — | Required for translation |
| `DEEPL_FREE` | `true` | Use free-tier DeepL endpoint |
| `GATEWAY_TIMEOUT_EMBED` | `10` | Embed timeout (seconds) |
| `GATEWAY_TIMEOUT_CHAT` | `30` | Chat timeout (seconds) |
| `GATEWAY_TIMEOUT_TRANSLATE` | `15` | Translate timeout (seconds) |
| `GATEWAY_MAX_RETRIES` | `3` | Max retry attempts |
| `GATEWAY_RETRY_BASE_DELAY` | `0.5` | Retry base delay (seconds) |
| `GATEWAY_CB_FAILURE_THRESHOLD` | `5` | Circuit breaker failure count |
| `GATEWAY_CB_COOLDOWN` | `60` | Circuit breaker cooldown (seconds) |
| `LOG_WEBHOOK_API_CALL` | — | Webhook for API call logs (falls back to `LOG_WEBHOOK_DEFAULT`) |

---

## Adding a New Provider

1. Create `gateway/adapters/my_provider.py` implementing `BaseAdapter`.
2. Register in `Gateway.start()`:
   ```python
   from .adapters.my_provider import MyAdapter
   if self.config.my_api_key:
       adapter = MyAdapter(self.config.my_api_key)
       await adapter.start()
       self._adapters["myprovider"] = adapter
   ```
3. Add a high-level client in `gateway/clients/` if needed.
4. Add quota_limits rows for the new call types.
5. Document the new `call_type` in the table above.

---

## Adding a New Call Type

1. Choose a `call_type` name (snake_case, descriptive).
2. Decide the quota targets (guild / user / both / none).
3. Add rows to `quota_limits` in `db/base.py::_init_tables` (or via SQL migration).
4. Use it in your cog:
   ```python
   from gateway import QuotaTarget
   result = await bot.gateway.ai.chat(
       ...,
       quota=[QuotaTarget.guild(guild.id, "my_new_type")],
       call_type="my_new_type",
   )
   ```
5. Document it in the Call Types table above.
