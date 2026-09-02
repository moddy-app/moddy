# Moddy — API Gateway

> **Read this before writing any code that calls OpenAI, DeepL, Groq, or any future external API.**
> No module calls provider APIs directly. Everything goes through `bot.gateway`.

---

## Overview

The `gateway/` package is a centralized, in-process API library shared by the Discord bot.
It enforces a single execution pipeline for every outbound API call:

```
quota check → rate-limit reservation → resilience (timeout / retry / circuit breaker)
    → provider call → quota consume + rate-limit reconciliation → log
```

This guarantees:
- **Every call is logged** — both to the staff webhook (`api_call` category) and to the `api_calls` PG table.
- **Quotas are enforced** before any provider is contacted.
- **Our provider-account limits are never breached** (requests/minute, audio-seconds/hour, …).
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

# Speech-to-text (Groq Whisper) — `duration_hint` is what the audio-seconds
# rate limit reserves; the exact duration reported by the model reconciles it.
result = await bot.gateway.transcription.transcribe(
    audio_bytes,
    filename="voice-message.ogg",
    content_type="audio/ogg",
    duration_hint=12.4,
    quota=[QuotaTarget.user(user.id, "voice_transcription")],
    call_type="voice_transcription",
    metadata={"guild_id": guild.id, "user_id": user.id},
)
# result: Transcription(text=..., language=..., duration=...)

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
├── ratelimit.py         # RateLimiter (provider-account windows, weighted units)
├── resilience.py        # CircuitBreaker + retry/backoff
├── logger.py            # GatewayLogger (Redis buffer → PG + webhook)
├── executor.py          # GatewayExecutor (single execution path)
├── adapters/
│   ├── base.py          # AbstractAdapter + AdapterResult
│   ├── openai.py        # OpenAIAdapter (embed + chat)
│   ├── deepl.py         # DeepLAdapter (translate)
│   └── groq.py          # GroqAdapter (transcribe)
└── clients/
    ├── ai.py            # AIClient (gw.ai.embed, gw.ai.chat)
    ├── translation.py   # TranslationClient (gw.translation.translate)
    └── transcription.py # TranscriptionClient (gw.transcription.transcribe)
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
| `voice_transcription` | user / guild / global | -1 |

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
| `text_fix` | openai/chat (`gpt-4.1-nano`) | user + guild | ✅ |
| `text_rephrase` | openai/chat (`gpt-4.1-mini`) | user + guild | ✅ |
| `text_summarize` | openai/chat (`gpt-4.1-mini`) | user + guild | ✅ |

| `voice_transcription` | groq/transcribe (`whisper-large-v3-turbo`) | user + guild | ✅ |

> `text_*` calls come from `cogs/text_tools.py` (`/fix`, `/rephrase`, `/summarize`).
> The guild target is only added when the command runs inside a server — in DMs
> and user-installed contexts only the user bucket is debited.
>
> `voice_transcription` comes from `services/transcription_service.py`
> (see [VOICE_TRANSCRIPTION.md](VOICE_TRANSCRIPTION.md)). Same rule for the
> guild target; it is additionally capped by the provider rate limits below.

---

## Provider Rate Limits

Quotas answer "may this *guild/user* spend one more call today?".
`gateway/ratelimit.py` answers a different question: **"would this call breach
the limit our provider account is capped at?"** — global, per `(provider, model)`,
several windows at once, metered in arbitrary units.

Rules live in `gateway/config.py` and mirror the provider console:

| Provider / model | Rule | Limit | Window | Env override |
|---|---|---|---|---|
| groq / `whisper-large-v3-turbo` | `rpm` | 20 requests | 1 min | `GROQ_WHISPER_RPM` |
| | `rpd` | 2 000 requests | 1 day | `GROQ_WHISPER_RPD` |
| | `ash` | 7 200 audio seconds | 1 hour | `GROQ_WHISPER_AUDIO_SECONDS_PER_HOUR` |
| | `asd` | 28 800 audio seconds | 1 day | `GROQ_WHISPER_AUDIO_SECONDS_PER_DAY` |
| openai / `text-embedding-3-small` | `rpm` | 3 000 requests | 1 min | `OPENAI_EMBED_RPM` |
| openai / `gpt-4.1-nano` | `rpm` | 500 requests | 1 min | `OPENAI_NANO_RPM` |
| openai / `gpt-4.1-mini` | `rpm` | 500 requests | 1 min | `OPENAI_MINI_RPM` |

The OpenAI values mirror our org's Tier 1 limits (platform.openai.com → Limits
→ Rate limits). Only RPM is enforced (not TPM) — estimating tokens ahead of
the call would need a tokenizer, and RPM alone stops the bot from bursting
past what the account tier allows. Raise them (env vars above) when the
account tier changes.

How it works:
- Fixed windows in Redis (`ratelimit:{provider}:{model}:{rule}:{window}`), shared across shards.
- A call **reserves** its cost before the provider is contacted (atomic `INCRBYFLOAT`,
  rolled back if it breached) — concurrent calls cannot collectively overshoot.
- The reservation is **released** when the call fails, and **reconciled** when the
  provider reports the real cost (audio duration is estimated before, measured after).
- Redis unavailable ⇒ **fail open**. A limiter that cannot count must never take a feature down.

Breaching one raises `ModelRateLimitError` (`.rule`, `.limit`, `.retry_after`)
**without contacting the provider**.

### Adding limits for a new model
```python
# gateway/config.py
def _default_model_rate_limits():
    return {
        ("groq", "whisper-large-v3-turbo"): _whisper_turbo_rules(),
        ("myprovider", "my-model"): [RateRule("rpm", UNIT_REQUESTS, MINUTE, 100)],
    }
```

---

## Resilience

### Timeouts
- `embed`: 10s
- `chat`: 30s
- `translate`: 15s
- `transcribe`: 90s (uploads a file and processes minutes of audio)

Override via env vars: `GATEWAY_TIMEOUT_EMBED`, `GATEWAY_TIMEOUT_CHAT`,
`GATEWAY_TIMEOUT_TRANSLATE`, `GATEWAY_TIMEOUT_TRANSCRIBE`.

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
    RateLimitError,       # .provider, .retry_after — provider-side 429
    ModelRateLimitError,  # .rule, .limit, .retry_after — OUR limit, call not made
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
| `GROQ_API_KEY` | — | Required for voice transcription |
| `DEEPL_FREE` | `true` | Use free-tier DeepL endpoint |
| `GATEWAY_TIMEOUT_EMBED` | `10` | Embed timeout (seconds) |
| `GATEWAY_TIMEOUT_CHAT` | `30` | Chat timeout (seconds) |
| `GATEWAY_TIMEOUT_TRANSLATE` | `15` | Translate timeout (seconds) |
| `GATEWAY_TIMEOUT_TRANSCRIBE` | `90` | Transcribe timeout (seconds) |
| `GROQ_WHISPER_RPM` | `20` | Whisper requests per minute (provider account) |
| `GROQ_WHISPER_RPD` | `2000` | Whisper requests per day |
| `GROQ_WHISPER_AUDIO_SECONDS_PER_HOUR` | `7200` | Whisper audio seconds per hour |
| `GROQ_WHISPER_AUDIO_SECONDS_PER_DAY` | `28800` | Whisper audio seconds per day |
| `OPENAI_EMBED_RPM` | `3000` | `text-embedding-3-small` requests per minute (provider account) |
| `OPENAI_NANO_RPM` | `500` | `gpt-4.1-nano` requests per minute |
| `OPENAI_MINI_RPM` | `500` | `gpt-4.1-mini` requests per minute |
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
