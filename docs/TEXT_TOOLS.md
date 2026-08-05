# Moddy — AI Text Tools (`/fix`, `/rephrase`, `/summarize`)

> Implementation: [`cogs/text_tools.py`](../cogs/text_tools.py)
> Every model call goes through the gateway — see [API_GATEWAY.md](API_GATEWAY.md).

---

## Overview

Three global commands (available in servers, DMs and user-installs) that run a
piece of text through OpenAI:

| Command | What it does | Model | `call_type` | Accent |
|---|---|---|---|---|
| `/fix` | Fixes spelling, grammar, punctuation, accents — nothing else | `gpt-4.1-nano` | `text_fix` | `success` |
| `/rephrase` | Rewrites the text in a chosen style, same meaning | `gpt-4.1-mini` | `text_rephrase` | `primary` |
| `/summarize` | Condenses the text to its key points | `gpt-4.1-mini` | `text_summarize` | `developer` |

All three work with **any input language** — the model detects it and answers in
that same language. The one exception is `/summarize`, see *Languages* below.

---

## Entry points

| Entry point | Result |
|---|---|
| Slash command (no argument) | Opens an **empty** modal |
| `AI text tools` context menu | Opens a modal **pre-filled** with the message (always ephemeral) |

### Slash → Modal V2

The slash commands take no `text` option: they open a Modal V2
(see [MODALS_V2.md](MODALS_V2.md)) built from `_BaseTextModal`:

1. `ui.Label` + `ui.TextInput` (paragraph, `max_length = MAX_INPUT_LENGTH = 2000`)
2. *(rephrase / summarize only)* `ui.Label` + `ui.Select` — style or length preset
3. `ui.TextDisplay` — the `commands.text_tools.ai_notice` privacy notice

The length limit lives **only** on the field: Discord refuses an over-long
submission client-side, so there is no server-side length check.

Modals cannot be persistent (Discord limitation, documented in `BaseModal`), so
the ephemeral preference is captured at command time and carried into the modal.

### One context menu for three tools

Discord caps **message context menus at 5 globally**, and the bot already ships
three (`Save Message`, `Get Emojis`, `Translate`). Shipping one menu per tool
overflows the limit and makes cog loading fail with
`CommandLimitReached: maximum number of message context menu commands exceeded 5`.

So the three tools share a single `AI text tools` menu. Its modal (`MenuModal`)
is the normal pre-filled modal plus one select that carries the action **and**
its preset in a single choice:

```
Fix
Rephrase · Professional      → value "rephrase:professional"
Summarize · Key points       → value "summarize:bullets"
```

`1 + len(REPHRASE_STYLES) + len(SUMMARY_LENGTHS)` options — stays well under the
25-option cap as long as presets are added in moderation.

### Presets

Presets live in `cogs/text_tools.py` as `REPHRASE_STYLES` and `SUMMARY_LENGTHS`:
each key maps to the English instruction injected in the system prompt, and to an
i18n label under `commands.rephrase.styles.*` / `commands.summarize.lengths.*`.
**Adding a preset = one entry in the dict + one key in each locale file** (the
context-menu select picks it up automatically).

| `/rephrase` styles | `/summarize` lengths |
|---|---|
| `neutral` (default), `professional`, `formal`, `friendly`, `casual`, `concise` | `short`, `medium` (default), `bullets` |

---

## Languages

- **UI** — everything goes through i18n (`fr` / `en-US`).
- **`/fix` and `/rephrase`** — the prompt states the input may be in any language
  and that the answer must be in that same language, never translated.
- **`/summarize`** — the summary is written in the **reader's own Discord
  language** (`_user_language()` resolves `interaction.locale` to an English
  language name through the `languages.*` i18n namespace), translating from the
  source when needed. Summarizing a foreign-language wall of text into your own
  language is the point of the command.

---

## Safety

### Mentions

`sanitize_text()` drops mention-shaped tokens (`<@id>`, `<@!id>`, `<@&id>`,
`<#id>`, `<id:…>`) and every remaining `@` — which also kills `@everyone`,
`@here` and raw `@name` pings.

It is applied **only to public answers** (input *and* output). An incognito
answer keeps mentions exactly as typed: the card is visible to its author alone,
and `discord.AllowedMentions.none()` is applied on every send regardless, so a
mention renders but never notifies anyone.

The `'@'` prompt rule follows the same switch — it is only injected in the
system prompt when the answer is public.

`/fix` and `/rephrase` render inside a code block (`escape_code_block()`
neutralizes nested backtick fences).

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
register as persistent). The container carries a per-command **accent bar**, the
title always uses the `<:text:…>` emoji, and the attribution line is its own
`TextDisplay` — no separator, no emoji:

```
### <:text:…> Corrected text
```
<the result>
```
-# Style: **Professional**        ← rephrase / summarize only
-# Corrected by **ChatGPT**
```

`/summarize` renders its result as plain markdown instead of a code block — a
summary is meant to be read, not copy-pasted.

---

## i18n keys

| Namespace | Contents |
|---|---|
| `commands.text_tools` | `ai_notice`, `menu.*` (shared context-menu modal), `errors.*` |
| `commands.fix` | `description`, `working`, `modal.*`, `result.{title,footer}` |
| `commands.rephrase` | + `modal.style*`, `styles.*`, `result.style` |
| `commands.summarize` | + `modal.length*`, `lengths.*`, `result.length` |

---

## Failure modes

| Situation | Behaviour |
|---|---|
| Empty input / empty target message | Ephemeral error card, no API call |
| Input > 2000 chars | Blocked by the modal field itself, never reaches the bot |
| OpenAI not configured (`gateway.openai_available()` false) | `errors.unavailable` |
| Rate limit hit | `errors.rate_limit` with the wait time |
| Quota exceeded, circuit open, timeout (25 s), empty output | Loading card is replaced by `errors.api_error` |
