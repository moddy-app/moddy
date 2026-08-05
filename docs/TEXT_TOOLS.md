# Moddy — AI Text Tools (`/fix`, `/rephrase`, `/summarize`)

> Implementation: [`cogs/text_tools.py`](../cogs/text_tools.py)
> Every model call goes through the gateway — see [API_GATEWAY.md](API_GATEWAY.md).

---

## Overview

Three global commands (available in servers, DMs and user-installs) that run a
piece of text through OpenAI:

| Command | What it does | Model | `call_type` |
|---|---|---|---|
| `/fix` | Fixes spelling, grammar, punctuation, accents — nothing else | `gpt-4.1-nano` | `text_fix` |
| `/rephrase` | Rewrites the text in a chosen style, same meaning | `gpt-4.1-mini` | `text_rephrase` |
| `/summarize` | Condenses the text to its key points | `gpt-4.1-mini` | `text_summarize` |

Each one also exists as a **message context menu**: `Fix text`, `Rephrase text`,
`Summarize text`.

The behaviour is uniform across the three commands:

| Entry point | Result |
|---|---|
| Slash command (no argument) | Opens an **empty** modal |
| Message context menu | Opens the **same modal, pre-filled** with the message content (always ephemeral) |

Pre-filling rather than running straight away means the user can still edit the
text and pick a style / a length before the call is made.

---

## Entry points

### Slash → Modal V2

The slash commands take no `text` option: they open a Modal V2
(see [MODALS_V2.md](MODALS_V2.md)) built from `_BaseTextModal`:

1. `ui.Label` + `ui.TextInput` (paragraph, `max_length = MAX_INPUT_LENGTH = 2000`)
2. *(rephrase / summarize only)* `ui.Label` + `ui.Select` — style or length preset
3. `ui.TextDisplay` — the `commands.text_tools.ai_notice` disclaimer

Modals cannot be persistent (Discord limitation, documented in `BaseModal`), so
the ephemeral preference is captured at command time and carried into the modal.

### Presets

Presets live in `cogs/text_tools.py` as `REPHRASE_STYLES` and `SUMMARY_LENGTHS`:
each key maps to the English instruction injected in the system prompt, and to an
i18n label under `commands.rephrase.styles.*` / `commands.summarize.lengths.*`.
**Adding a preset = one entry in the dict + one key in each locale file.**

| `/rephrase` styles | `/summarize` lengths |
|---|---|
| `neutral` (default), `professional`, `formal`, `friendly`, `casual`, `concise` | `short`, `medium` (default), `bullets` |

---

## Safety

### No mentions, ever

`sanitize_text()` is applied **twice** — to the input before it reaches the model,
and to the model output before it is rendered:

1. mention-shaped tokens (`<@id>`, `<@!id>`, `<@&id>`, `<#id>`, `<id:…>`) are dropped;
2. every remaining `@` character is removed, which also kills `@everyone`,
   `@here` and raw `@name` pings.

On top of that the result is sent with `discord.AllowedMentions.none()`, and
`/fix` + `/rephrase` render inside a code block (`escape_code_block()` neutralizes
nested backtick fences).

### Prompt injection

User content is wrapped with `automod.injection.fence()` using a fresh random
nonce, and the shared `_guard_rules()` block tells the model that everything
between the markers is data, never instructions. Same hardening as the automod
pipeline — mitigation, not a guarantee.

### Rate limit

10 uses per minute per user, in-memory (`TextTools.check_rate_limit`), on top of
the gateway quotas (`user` + `guild` buckets, unlimited by default — tighten via
`quota_overrides`).

---

## Output card

Components V2, built by `TextResultView` (no interactive component, so nothing to
register as persistent):

```
### <:text:…> Corrected text
```
<the result>
```
-# Style: **Professional**        ← rephrase / summarize only
──────────────────────────────
-# <:text:…> Corrected by **ChatGPT**
```

`/summarize` renders its result as plain markdown instead of a code block — a
summary is meant to be read, not copy-pasted.

---

## i18n keys

| Namespace | Contents |
|---|---|
| `commands.text_tools` | `ai_notice`, `errors.*` (shared by the three commands) |
| `commands.fix` | `description`, `working`, `modal.*`, `result.{title,footer}` |
| `commands.rephrase` | + `modal.style*`, `styles.*`, `result.style` |
| `commands.summarize` | + `modal.length*`, `lengths.*`, `result.length` |

---

## Failure modes

| Situation | Behaviour |
|---|---|
| Empty input / empty target message | Ephemeral error card, no API call |
| Input > 2000 chars | Ephemeral error card, no API call |
| OpenAI not configured (`gateway.openai_available()` false) | `errors.unavailable` |
| Rate limit hit | `errors.rate_limit` with the wait time |
| Quota exceeded, circuit open, timeout (25 s), empty output | Loading card is replaced by `errors.api_error` |
