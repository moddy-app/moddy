# Session: Voice Transcription (Groq Whisper)

**Date:** 2026-08-08
**Agent:** Claude Code

## Summary

Added voice message transcription end to end: a new Groq provider in the API
gateway (with the provider-account rate limiting the feature needs), a shared
transcription service, the `Transcribe` message context menu, and a
`voice_transcription` server module that offers a button — or the transcription
itself — under every voice message.

Model: `whisper-large-v3-turbo` via Groq (`GROQ_API_KEY`).

## Changes Made

### Gateway (`gateway/`)

- `ratelimit.py` **(new)** — `RateLimiter`: fixed windows in Redis, one per
  `RateRule`, metered in arbitrary units (requests, audio seconds). Reserve
  before the call (atomic `INCRBYFLOAT` + rollback), release on failure,
  reconcile with the real cost on success. Fails open when Redis is down.
- `adapters/groq.py` **(new)** — `GroqAdapter`, `transcribe` operation,
  multipart upload, `verbose_json` (gives the exact duration + language).
- `clients/transcription.py` **(new)** — `bot.gateway.transcription.transcribe()`
  returning a `Transcription(text, language, duration)`.
- `config.py` — `groq_api_key`, `timeout_transcribe` (90 s), and
  `model_rate_limits` holding the Groq console values (20 rpm / 2 000 rpd /
  7 200 audio-sec per hour / 28 800 per day), each env-overridable.
- `spec.py` — `CallSpec.rate_cost` (cost per unit) and `CallSpec.binary` (the
  audio bytes, deliberately kept out of `payload`, which is logged as JSON).
- `adapters/base.py` — `AdapterResult.rate_cost` for the real, post-call cost.
- `executor.py` — reservation step, release on failure, reconciliation on
  success, `transcribe` timeout.
- `errors.py` — `ModelRateLimitError` (our own limit; the call is never made).
- `__init__.py` — starts the Groq adapter, exposes `gateway.transcription`,
  `groq_available()`, `rate_limit_usage()`.

### Feature

- `services/transcription_service.py` **(new)** — attachment discovery, guard
  rails (25 MB, 30 min, in-flight de-duplication, an off-by-default per-user
  throttle) and typed failures (`ErrorCode`). Single entry point for both UIs.
- `utils/transcription_views.py` **(new)** — loading / result / error cards, the
  `.txt` fallback for long transcriptions and the persistent `TranscribeButton`
  (`DynamicItem`).
- `cogs/voice_transcription.py` **(new)** — the `Transcribe` context menu
  (global, DMs and user installs included).
- `modules/voice_transcription.py` **(new)** — the server module
  (`enabled`, `mode`, `channel_ids`).
- `modules/configs/voice_transcription_config.py` **(new)** — the `/config` panel.
- `bot.py` — `bot.transcription = TranscriptionService(self)`.
- `cogs/module_events.py` — dispatch `on_message` to the module.
- `cogs/config.py` — route the module id to its panel.
- `utils/persistent_views.py` — register the config panel + the button item.
- `utils/emojis.py` — `VOICE_CHAT`, `ROBOT_WORKING`.
- `db/base.py` — `quota_limits` rows for `voice_transcription` (user/guild/global, unlimited).

### i18n & docs

- `locales/{fr,en-US,es-ES,pt-BR,de}.json` — `transcription.*` (cards, errors)
  and `modules.voice_transcription.*` (module + config panel).
- `locales/commands/*.json` — `Transcribe` context menu name, all 32 locales.
- `docs/VOICE_TRANSCRIPTION.md` **(new)**, plus `CLAUDE.md`,
  `docs/API_GATEWAY.md`, `docs/PERSISTENT_VIEWS.md`, `docs/EMOJIS.md`,
  `docs/RAILWAY.md`.

### Tests

- `tests/gateway/test_ratelimit.py`, `tests/gateway/test_executor_ratelimit.py`,
  `tests/gateway/test_gateway_config.py`, `tests/test_transcription.py`;
  `TranscribeButton` added to `tests/test_persistent_views.py`.
  Full suite: 680 passed.

## Decisions & Rationale

- **Rate limiting is a new gateway component, not an extension of quotas.**
  Quotas are per entity, daily, PG-configured; the Groq limits are global, per
  `(provider, model)`, multi-window, and metered in audio seconds. Forcing them
  into `QuotaTarget` would have distorted both. They compose cleanly instead:
  quota check → reservation → call.
- **Reserve before the call, reconcile after.** Checking then consuming (what
  quotas do) races under concurrency, which matters here because a burst of
  clicks on a busy server is the normal case. Debiting up front makes bursts
  safe; audio duration is estimated before and corrected from the provider's
  own measurement afterwards.
- **No per-guild/per-user limit yet, as asked** — the `quota_limits` rows exist
  at `-1` so a single `quota_overrides` INSERT caps a server or a user with no
  deploy. `MAX_USES_PER_MINUTE_PER_USER` is present but `0`.
- **One service, two UIs.** The context menu and the module button share
  `transcribe_message()`, so guard rails and error messages can never drift.
- **The transcription is public and mention-inert.** Public because a
  transcription is for the channel; every send/edit uses
  `AllowedMentions.none()`, so nothing the model writes can ping. Rendered as
  plain text (no code fence) so it reads like a message.
- **A card never loses a word.** Past 3 500 characters the card keeps a readable
  preview and the full transcription rides along as a `.txt`. All three call
  sites go through `build_transcription_message()`, so the fallback cannot be
  wired on one path and forgotten on another.
- **Auto mode fails silently** — deleting its placeholder rather than posting an
  error in the channel on every hiccup. The failure is in the logs and `api_calls`.
- **`Transcribe` takes the fifth and last context menu slot** (Save Message,
  Get Emojis, Translate, AI text tools, Transcribe). A sixth feature will have
  to merge into an existing menu, as AI text tools already does.

## Known Issues / Follow-ups

- [x] Long transcriptions: the card shows the first 3 500 characters and the
      complete text ships as a `transcription.txt` attachment
      (`build_transcription_message()`), so a 20-minute recording loses nothing.
- [ ] Duration for non-voice-message audio attachments is estimated from file
      size (~128 kbps) until the provider answers — fine for accounting, but it
      means the 30-minute guard is approximate for those.
- [ ] `gateway.rate_limit_usage()` is exposed but not surfaced anywhere yet; it
      is the natural input for a staff diagnostics panel.
- [ ] Groq's free tier caps uploads at 25 MB; raise `MAX_FILE_BYTES` if the
      account moves to a dev tier (100 MB).
