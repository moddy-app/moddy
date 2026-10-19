# Text tools — `/define`, `/tldr`, `/regex`, `/diff`, `/textstats`

Five everyday text tools. They follow the shared contract in
[TOOLS.md](TOOLS.md) (card anatomy, phrase-style source footer, loading and
error cards, Modals V2, persistence, icons).

| File | Role |
|---|---|
| `cogs/tools_text.py` | The five slash commands + the `/tldr` plumbing (history → transcript → gateway) |
| `utils/tools_text_views.py` | Cards, Modals V2, persistent dynamic items (`ToolsTextPersistence`) |
| `tools/wiktionary.py` | Wiktionary parsing for 5 editions + REST fallback + cache (pure) |
| `tools/regex_test.py` | `regex`-package engine with a match timeout (pure) |
| `tools/diffing.py` | Line / word diff on `difflib` (pure) |
| `tools/textstats.py` | Counts, reading time, language guess, per-language readability (pure) |
| `tools/text_escape.py` | Safe markdown for user text shown outside a code block (pure) |
| `tests/test_tools_text.py` | Logic, cards, modals, state, i18n parity, cog declaration |

## Commands

| Command | Scope | Answer | Input |
|---|---|---|---|
| `/define word [language]` | everywhere (guilds, DMs, user install) | `incognito` option, ephemeral by default | slash options |
| `/tldr [messages]` | **servers only** (`guild_only`, guild install only) | `incognito` option, ephemeral by default | choice 25 / 50 / 100 |
| `/regex` | everywhere | `incognito` option | Modal V2 |
| `/diff` | everywhere | `incognito` option | Modal V2 |
| `/textstats [text]` | everywhere | `incognito` option | text option, or a Modal V2 when empty |

Localised names/descriptions: the 32 `locales/commands/*.json` files
(`define`, `tldr`, `regex`, `diff`, `textstats`).

### `/define`

* Edition = the `language` option, else the caller's locale (`fr en es pt de`,
  English when the locale is not one of them). `wiktionary.get_entry` parses
  `action=parse` wikitext of that edition, with the English REST endpoint as a
  fallback (a note says so on the card). Cached 7 days in Redis (24 h for
  misses) + a small in-process LRU, through `bot.public_api`.
* Card: `word · part of speech`, up to 3 numbered definitions of the shown
  sense (leading `(labels)` in italics), synonyms, "N more on Wiktionary", then
  `Provided by <logo> **Wiktionary** · <edition> Wiktionary · CC BY-SA`.
  The CC BY-SA licence is honoured by naming the source and a **link button**
  "View on Wiktionary".
* **Other senses**: when the entry has several parts of speech / etymologies, a
  select switches the card in place. `DefineSenseSelect` is a `DynamicItem`
  with custom_id `moddy:tools:define:sense:<lang>:<word>` — the entry is read
  back from the cache, so nothing else is stored. On a public card someone
  other than the requester gets the new sense as a private message instead of
  editing the shared one.

### `/tldr`

* Reads the last 25/50/100 messages of the channel (`channel.history`), only if
  the caller *and* the bot can view the channel and read its history
  (`interaction.permissions` / `app_permissions`).
* Bots, system messages and empty messages are skipped. Each message is run
  through `clean_content` + `cogs.text_tools.sanitize_text` (no mention
  survives), cut to 400 characters; the transcript is cut to 14 000 characters
  keeping the **newest** messages, numbered `#n author: text`.
* The transcript is fenced with a random nonce (`automod.injection`) and sent
  to `gpt-4.1-mini` through `bot.gateway.ai.chat` (`call_type="tldr"`). The
  model writes 3–7 bullets in the caller's language and may cite `[#n]`; each
  `[#n]` becomes a "jump" link to the real message (unknown numbers are dropped).
* Quota `tldr`: **10 per day per member**, 100 per day per server (seeds in
  `db/base.py`, tunable with `quota_overrides`), plus a 15 s cooldown.
  Needs `OPENAI_API_KEY` (`gateway.openai_available()`); otherwise a clear
  "unavailable" card.
* Footer: `Summarized by <logo> **OpenAI**` and an "AI can make mistakes" hint.

### `/regex`

* Modal V2: **Pattern** (≤ 500), **Text to test** (≤ 4000), **Flags**
  (`CheckboxGroup`: `i m s x`).
* Engine: the third-party `regex` package in V0 (`re`-compatible) mode, with a
  0.5 s timeout and a repeat-bound guard (`{n}` ≤ 1000), run in a worker thread
  behind a 2-slot semaphore. A catastrophic pattern gives a "took too long"
  card, never a frozen bot.
* Card: match count, the text with matches in **__bold underline__** (markdown
  works on every client, unlike ANSI), an aligned table of the first 10 matches
  with positions and groups, pattern and flags. More than 10 matches or a
  clipped text attaches `regex.txt`. Accent green with matches, yellow
  without.
* **Edit** button (`moddy:tools:regex:edit:<token>`): the pattern, text and
  flags are saved with `save_state` (24 h) and the modal reopens pre-filled. On
  an ephemeral card the result replaces the card; on a public card it comes back
  as a private message. Invalid-pattern / timeout error cards carry the same
  button so a typo is one click away.

### `/diff`

* Modal V2: **Before**, **After**, **Compare by** (`RadioGroup`: line / word).
* Line mode → a ```` ```diff ```` block (collapsed to `@@ … @@` with two lines
  of context) and `+x -y`. Word mode → merged text with ~~removed~~ and
  **added** words. Output over ~2800 characters is shortened and the complete
  `diff.diff` / `diff.txt` is attached. Identical texts say so.
* **Edit** button (`moddy:tools:diff:edit:<token>`), same mechanism as `/regex`.

### `/textstats`

* Words, characters (with/without spaces), sentences, paragraphs, words per
  sentence, reading time (230 wpm), speaking time (150 wpm), detected language,
  most used words, and a readability score on a 0–100 bar with a label from
  "very easy" to "very difficult".
* Language is guessed from stop words (fr/en/es/pt/de). Score formulas:
  Kandel-Moles (fr), Flesch (en), Fernández Huerta (es), Amstad (de), Martins
  (pt); syllables come from `pyphen` (vowel-group fallback when a dictionary is
  missing). Below 30 words, or for an unrecognised language, the card says why
  there is no score instead of showing a wrong one.
* Everything is computed on Moddy: footer `Computed by Moddy itself`.

## Persistence

`ToolsTextPersistence` (marker `BaseView`) registers three `DynamicItem`s:
`DefineSenseSelect`, `RegexEditButton`, `DiffEditButton`
(`DYNAMIC_ITEM_CASES` in `utils/tools_text_views.py`, collected by
`utils/tool_views_registry.py`). Callbacks re-derive everything from the
`custom_id` / `interaction`; the two Edit buttons use the short-lived
`save_state` exclusion documented in the persistent-views guide and answer
`expired_card` once the 24 h token is gone.

## i18n

Runtime strings: `tools.define.*`, `tools.tldr.*`, `tools.regex.*`,
`tools.diff.*`, `tools.textstats.*` in `locales/{fr,en-US,es-ES,pt-BR,de}.json`
(identical key sets, checked by `tests/test_tools_text.py`). Each tool also has
an `error_title`. Errors live under `tools.<tool>.errors.<code>` with the shared
`tools.common.errors.*` fallback (`quota`, `cooldown`, `timeout`, `unavailable`,
`guild_only`, `not_found`).

## Dependencies, env, quotas

* `regex` (lazy import, ~1 MB) and `pyphen` (lazy import, ~5 MB with its
  dictionaries) in `requirements.txt`.
* No new env var. `/tldr` needs the existing `OPENAI_API_KEY`.
* Gateway: provider `openai` (existing), `call_type="tldr"`, quota rows
  `user 10 / guild 100 / global unlimited`.

## Icons

Used (empty until uploaded, dropped cleanly): `DICTIONARY`, `REGEX`,
`DIFFERENCE`, `ANALYTICS`, `WIKTIONARY`, `OPENAI`, `EDIT` (uploaded).
`/tldr` reuses `TEXT`, like `/summarize`.

## Known limitations

* `/define` quality depends on each Wiktionary edition's wikitext; unusual
  templates are dropped rather than shown raw, and a few entries come back
  with fewer senses than the page has. The English REST fallback only covers
  English-language entries.
* `/tldr` reads only the last 25/50/100 messages, not threads' parents or
  attachments' content (an attachment-only message shows as `[attachment]`).
  Mentions are always removed from the summary and the model input.
* `/regex` uses the `regex` package, not Python's `re`, and not the JavaScript
  engine: lookbehind and Unicode classes behave as in `regex` V0. The match
  scan is capped at 0.5 s and 1000 matches.
* `/diff` compares at most 4000 characters per side (Modal V2 field limit).
* The readability scores are estimates; they are most meaningful on 100+ words.
