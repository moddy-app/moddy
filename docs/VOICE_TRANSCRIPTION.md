# Voice Transcription

> Turns Discord voice messages (and any audio attachment) into readable text,
> through Groq's `whisper-large-v3-turbo`.
>
> Read this before touching `modules/voice_transcription.py`,
> `services/transcription_service.py`, `utils/transcription_views.py` or the
> Groq side of the gateway.

---

## What the user sees

Two entry points, one behaviour:

| Entry point | Where | Who triggers it |
|---|---|---|
| **`Transcribe` message context menu** | Everywhere — servers, DMs, user-installed contexts | The person who opens the menu |
| **The button under a voice message** | Servers where the `voice_transcription` module is enabled | Anyone who can see the message |

The answer is **public** in both cases — a transcription exists so the channel
can read a voice note. Only failures are ephemeral, and only for the person who
asked.

```
┌───────────────────────────────────────┐
│  🎙 Transcription                      │
│  On se retrouve à 14h devant la salle │
│  -# 🕐 0:12 • Français • demandé par @x │
└───────────────────────────────────────┘
```

While the model runs, the card shows `<a:Robot:…> **Transcription en cours**`.
The finished card uses `<:voice_chat:…>`.

Public cards speak the **server's** language (`card_locale()`), since they stay
in the channel for everyone; ephemeral errors speak the clicker's.

The transcription is rendered as plain text — **not** inside a code fence, so
it reads like a message. Every send and edit passes
`discord.AllowedMentions.none()`: whatever the model transcribes (`@everyone`,
a user mention read out loud, …) can never notify anyone.

---

## Architecture

```
cogs/voice_transcription.py          "Transcribe" context menu (global)
modules/voice_transcription.py       server module: when to offer it
modules/configs/voice_transcription_config.py   /config panel
   │
   └──> services/transcription_service.py      ← the only place with the rules
              │
              └──> bot.gateway.transcription   ← the only place that calls Groq
                        gateway/clients/transcription.py
                        gateway/adapters/groq.py
                        gateway/ratelimit.py    ← provider-account limits
utils/transcription_views.py         cards + the persistent button
```

`TranscriptionService` lives on `bot.transcription` (built in `bot.__init__`).
Both UIs call the same `transcribe_message()`, so a guard rail added there
applies everywhere, immediately.

---

## The module (`/config` → Voice Transcription)

Config stored at `guilds.data.modules.voice_transcription`:

```json
{
  "enabled": true,
  "mode": "button",
  "channel_ids": []
}
```

| Key | Type | Meaning |
|---|---|---|
| `enabled` | bool | Module on/off. Persisted (not derived). |
| `mode` | `"button"` \| `"auto"` | `button`: reply with a Transcribe button. `auto`: transcribe immediately. |
| `channel_ids` | list[int] | Empty = every channel. Otherwise only these (max 25). |

`mode` is the marker the config panel uses to detect an existing configuration
(never `enabled`, which reads as a legitimate `false`).

In `auto` mode a failure is silent: the placeholder is deleted and nothing is
posted. An automatic feature that apologises in the channel on every hiccup is
worse than one that stays quiet — the failure is in the logs and in `api_calls`.

Dispatch happens in `cogs/module_events.py::on_message`, like every other
message-driven module.

---

## The context menu

`Transcribe` is the **fifth and last** message context menu Discord allows
(with `Save Message`, `Get Emojis`, `Translate`, `AI text tools`). A sixth one
means merging two of them, as `AI text tools` already does for `/fix`,
`/rephrase` and `/summarize`.

Its localized names live in `locales/commands/<locale>.json` under
`context_menus.Transcribe` — all 32 Discord locales, see
[COMMAND_LOCALIZATION.md](COMMAND_LOCALIZATION.md).

---

## Guard rails

All in `services/transcription_service.py`, all checked **before** any upload:

| Guard | Value | Why |
|---|---|---|
| `MAX_FILE_BYTES` | 25 MB | Groq's own cap — a bigger file is a guaranteed 413. |
| `MAX_DURATION_SECONDS` | 30 min | Stops one message eating the hourly audio budget. |
| `MAX_USES_PER_MINUTE_PER_USER` | `0` (disabled) | Machinery for a per-user throttle, deliberately off — per-user/per-guild caps are meant to arrive through `quota_overrides`, no code change. |
| in-flight set | per message id | Two people clicking the same button pay for the audio once. |

Duration comes from Discord for voice messages (`Attachment.duration`); for a
plain audio attachment it is estimated at ~128 kbps and then **reconciled** with
the exact duration the model reports (see rate limiting below).

Failures are typed (`ErrorCode`) and map 1:1 to `transcription.errors.<code>`
i18n keys, so a new failure mode is a constant plus a translation.

---

## Cost control

Two independent layers — see [API_GATEWAY.md](API_GATEWAY.md).

**1. Quotas (per guild / per user).** `call_type = voice_transcription`, targets
`user` + `guild`. **Unlimited today** (`daily_limit = -1`); tighten one server
or one user with a `quota_overrides` row, no deploy needed.

**2. Provider-account rate limits (global).** `gateway/ratelimit.py`, configured
in `gateway/config.py`, mirroring the Groq console:

| Rule | Limit | Window | Env override |
|---|---|---|---|
| `rpm` | 20 requests | 1 min | `GROQ_WHISPER_RPM` |
| `rpd` | 2 000 requests | 1 day | `GROQ_WHISPER_RPD` |
| `ash` | 7 200 audio seconds | 1 hour | `GROQ_WHISPER_AUDIO_SECONDS_PER_HOUR` |
| `asd` | 28 800 audio seconds | 1 day | `GROQ_WHISPER_AUDIO_SECONDS_PER_DAY` |

Counters are fixed windows in Redis, shared across shards. A call **reserves**
its cost before the request leaves the bot (atomic `INCRBYFLOAT` + rollback), so
concurrent calls cannot collectively overshoot; the reservation is released if
the call fails and reconciled with the real audio duration when it succeeds.
Redis being unavailable fails **open** — a limiter that cannot count must not
take the feature down.

When a limit is hit the user gets "try again in `N` seconds", with `N` computed
from the window that actually blocked.

---

## Adding a limit later

- **Per guild**: `INSERT INTO quota_overrides (scope, key, type, daily_limit) VALUES ('guild', '<guild_id>', 'voice_transcription', 50);`
- **Per user**: same with `scope = 'user'`.
- **Globally, per day**: update the `('global', 'voice_transcription')` row in `quota_limits`.
- **Provider limits** (tier upgrade): raise the numbers in
  `gateway/config.py::_whisper_turbo_rules`, or set the env vars above.
- **Hard per-user throttle** (instant, not daily): set
  `MAX_USES_PER_MINUTE_PER_USER` in the service.

---

## Persistence

The Transcribe button is a `DynamicItem` whose `custom_id` encodes the voice
message it transcribes (`moddy:vtr:go:<channel_id>:<message_id>`), registered by
`TranscriptionPersistence`. It keeps working after a restart, with no in-memory
state: the callback re-fetches the message and re-derives everything from the
interaction. `VoiceTranscriptionConfigView` follows the standard guild-config
pattern (Manage Server re-checked on every click). See
[PERSISTENT_VIEWS.md](PERSISTENT_VIEWS.md).

---

## Environment

| Variable | Required | Description |
|---|---|---|
| `GROQ_API_KEY` | yes | Without it the Groq adapter stays disabled and both entry points report "unavailable". |
| `GATEWAY_TIMEOUT_TRANSCRIBE` | no | Seconds, default `90`. |

---

## Tests

```bash
pytest tests/test_transcription.py tests/gateway -q     # helpers, cards, limits
pytest tests/test_persistent_views.py -k Transcribe -q  # button persistence
```
