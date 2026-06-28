# Session — 2026-06-28 : OpenAI integration

## What was done

Added a centralised OpenAI client service so any part of the bot can make AI requests through a single, interceptable entry point.

## Files modified

| File | Change |
|---|---|
| `services/openai_client.py` | New — `OpenAIClient`, `OpenAIContext`, pre/post hook system, `OpenAIQuotaExceeded` |
| `config.py` | Added `OPENAI_API_KEY` env var + non-blocking startup warning |
| `bot.py` | `self.openai = OpenAIClient(self)` in `__init__`, `await self.openai.start()` in `setup_hook`, `await self.openai.stop()` in `close` |
| `requirements.txt` | Added `openai>=1.0.0` |
| `docs/OPENAI.md` | New — full integration doc |

## Decisions made

- **No quotas yet** — intentionally out of scope for this session; the hook system is the future extension point.
- **Pre-hook raises to block** — any exception from a pre-hook aborts the request and propagates to the caller; `OpenAIQuotaExceeded` is the semantic type for quota errors.
- **Post-hook errors are swallowed** — a broken usage tracker should never break the user-facing response.
- **Default model `gpt-4o-mini`** — cheapest capable model; callers override per-call with `model=`.
- **`complete_text()` convenience wrapper** — avoids boilerplate `response.choices[0].message.content` at every call site.
- **`OpenAIContext.extra` dict** — free-form bag for feature tags so hooks can branch on metadata without changing the context dataclass.

## Follow-ups

- Implement per-guild / per-user quota pre-hook once the DB table is defined.
- Add a post-hook that writes token usage to the technical webhook log.
- Consider model routing pre-hook tied to subscription tier.
