# OpenAI Integration

Moddy uses a centralised `OpenAIClient` service (attached to the bot as `bot.openai`) for all calls to the OpenAI API.  Every call goes through the same client so hooks, quotas, and usage tracking can be added in one place without touching individual call sites.

---

## Quick start

```python
from services.openai_client import OpenAIContext

# Full response object
response = await bot.openai.complete(
    messages=[{"role": "user", "content": "Summarise this: ..."}],
    context=OpenAIContext(guild_id=interaction.guild_id, user_id=interaction.user.id),
)
text = response.choices[0].message.content

# Convenience wrapper — returns the text directly
text = await bot.openai.complete_text(
    messages=[{"role": "user", "content": "..."}],
    context=OpenAIContext(guild_id=interaction.guild_id, user_id=interaction.user.id),
)
```

Always pass a `context` with `guild_id` and `user_id` — they are optional today but will be required once quota enforcement is added.

---

## Configuration

| Env var | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | Yes (for AI features) | Standard OpenAI API key |

If the key is absent the client starts in disabled mode — `bot.openai.available` returns `False` and every call raises `RuntimeError`.  A warning is printed at startup; the bot itself keeps running.

---

## API reference — `OpenAIClient`

### `bot.openai.available → bool`
`True` when the client is ready to accept requests.

### `bot.openai.complete(messages, *, model, context, **kwargs) → ChatCompletion`
Low-level wrapper.  Returns the raw OpenAI `ChatCompletion` object so callers can inspect choices, finish reason, usage, etc.

| Arg | Default | Description |
|---|---|---|
| `messages` | — | OpenAI-format message list |
| `model` | `"gpt-4o-mini"` | Model name |
| `context` | `OpenAIContext()` | Caller metadata (guild / user IDs) |
| `**kwargs` | — | Forwarded to `client.chat.completions.create()` (e.g. `temperature`, `max_tokens`) |

### `bot.openai.complete_text(messages, *, model, context, **kwargs) → str`
Same as `complete()` but returns `response.choices[0].message.content` directly.

---

## `OpenAIContext`

```python
@dataclass
class OpenAIContext:
    guild_id: Optional[int] = None
    user_id:  Optional[int] = None
    extra:    dict = field(default_factory=dict)  # arbitrary tags
```

`extra` is a free-form dict for feature tags, cog names, or any metadata hooks may need:

```python
OpenAIContext(guild_id=guild.id, user_id=user.id, extra={"feature": "moderation"})
```

---

## Hook system

Hooks let you intercept every OpenAI request without modifying call sites.  Register them once (e.g. in `setup_hook` or a cog's `cog_load`) and they run automatically.

### Pre-hooks — run before the API call

```python
from services.openai_client import OpenAIContext, OpenAIQuotaExceeded

async def my_quota_checker(ctx: OpenAIContext, params: dict) -> None:
    if await is_over_limit(ctx.guild_id):
        raise OpenAIQuotaExceeded("Monthly quota reached for this server")

bot.openai.add_pre_hook(my_quota_checker)
```

- `ctx` — the `OpenAIContext` passed by the caller
- `params` — the dict that will be sent to the API (`model`, `messages`, …); you can mutate it
- Raising any exception aborts the request; the exception propagates to the caller
- `OpenAIQuotaExceeded` is the semantic exception for quota errors

### Post-hooks — run after the API call

```python
async def track_usage(ctx: OpenAIContext, response, usage: dict) -> None:
    await db.record_openai_usage(
        guild_id=ctx.guild_id,
        user_id=ctx.user_id,
        tokens=usage["total_tokens"],
    )

bot.openai.add_post_hook(track_usage)
```

- `response` — the raw `ChatCompletion` object
- `usage` — `{"prompt_tokens": int, "completion_tokens": int, "total_tokens": int}`
- Exceptions in post-hooks are caught and logged; they do **not** affect the response returned to the caller

### Hook execution order
Pre-hooks run in registration order; if one raises, subsequent pre-hooks and the API call are skipped.  Post-hooks always run in registration order regardless of each other's outcome.

---

## Planned extensions

These are not implemented yet but the hook system is designed to support them without any changes to call sites:

- **Per-guild token quotas** — pre-hook reads the guild's monthly allowance from DB/Redis
- **Per-user rate limiting** — pre-hook checks a Redis counter per `(user_id, day)`
- **Usage billing / analytics** — post-hook writes token counts to a DB table
- **Request logging** — post-hook emits to the technical webhook log (see `docs/TECHNICAL_LOGS.md`)
- **Model routing** — pre-hook can swap `params["model"]` based on guild subscription tier

---

## File locations

| File | Role |
|---|---|
| `services/openai_client.py` | `OpenAIClient`, `OpenAIContext`, hook types, `OpenAIQuotaExceeded` |
| `config.py` | `OPENAI_API_KEY` env var |
| `bot.py` | `self.openai = OpenAIClient(self)`, `start()` / `stop()` wiring |
| `requirements.txt` | `openai>=1.0.0` |
