# 2026-08-05 — AI text tools: `/fix`, `/rephrase`, `/summarize`

## What was done

Added a new cog exposing three AI text helpers, each available both as a slash
command (opening a Modal V2) and as a message context menu:

| Command / menu | Model | `call_type` |
|---|---|---|
| `/fix` — `Fix text` | `gpt-4.1-nano` | `text_fix` |
| `/rephrase` — `Rephrase text` | `gpt-4.1-mini` | `text_rephrase` |
| `/summarize` — `Summarize text` | `gpt-4.1-mini` | `text_summarize` |

- All three are **global** commands (`allowed_installs` + `allowed_contexts`),
  usable in servers, DMs and user-installs.
- `/rephrase` adds a style select (`neutral`, `professional`, `formal`,
  `friendly`, `casual`, `concise`); `/summarize` adds a length select
  (`short`, `medium`, `bullets`).
- Uniform entry-point behaviour: the slash command (no argument) opens an empty
  modal, the context menu opens the same modal pre-filled with the message
  content, so the user can always edit the text and pick a preset before the
  call is made.
- Output is a Components V2 card ending with
  `-# <:text:…> Corrected by **ChatGPT**` (localized per command).

## Files modified

| File | Change |
|---|---|
| `cogs/text_tools.py` | **New** — `TextTools` cog, 3 modals, `TextResultView`, sanitizer |
| `locales/fr.json`, `locales/en-US.json` | **New** keys: `commands.text_tools`, `commands.fix`, `commands.rephrase`, `commands.summarize` |
| `db/base.py` | Seeded `quota_limits` rows for the 3 new call types (user/guild/global, unlimited) |
| `gateway/config.py` | Added `gpt-4.1-mini` input/output cost estimates (was missing, automod already used the model) |
| `docs/TEXT_TOOLS.md` | **New** — feature documentation |
| `docs/API_GATEWAY.md` | Documented the 3 new call types |
| `CLAUDE.md` | Project structure + doc index (`TEXT_TOOLS.md`, `MODALS_V2.md`) |

## Decisions

- **Slash commands take no `text` option.** The requirement was a Modal V2, and a
  modal also lifts the practical limit of a slash option and gives room for the
  OpenAI disclaimer (`ui.TextDisplay` as the last top-level modal component).
- **Input capped at 2000 characters** (`MAX_INPUT_LENGTH`), well under the modal's
  4000 limit, so a correction/rephrasing always fits back into one Components V2
  container.
- **Mentions can never survive.** `sanitize_text()` runs on the input *and* on the
  model output: mention tokens are dropped and every remaining `@` is removed
  (kills `@everyone` / `@here` / raw `@name`). The message is also sent with
  `AllowedMentions.none()`. Belt and braces, as requested.
- **Anti prompt injection** reuses `automod.injection.fence()` + a nonce declared
  in the system prompt, rather than a second ad-hoc implementation.
- **Quota targets are user + guild**, the guild bucket only when the command runs
  in a server (DMs / user-installs debit the user bucket only).
- **`/summarize` renders as plain markdown**, `/fix` and `/rephrase` inside a code
  block — corrected text is meant to be copy-pasted, a summary is meant to be read.
- **No buttons on the result card**, so there is no persistence concern
  (the mandatory-persistent-views rule only bites on interactive components).

## Known issues / follow-ups

- The `-# … by **ChatGPT**` attribution is localized; if the product wants the
  English wording everywhere, change the three `result.footer` keys in `fr.json`.
- `MAX_USES_PER_MINUTE = 10` is an in-memory per-process limit (same approach as
  `cogs/translate.py`). If the bot is ever sharded across processes, this should
  move to the Redis quota system.
- Context-menu names are not localized (Discord supports it via
  `app_commands.locale_str`, no other menu in the codebase does it yet).
