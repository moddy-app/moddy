# Utility tools — shared design system

Moddy ships a family of everyday tools: text (`/define`, `/tldr`, `/regex`,
`/diff`, `/textstats`), security & dev (`/pwned`, `/password`, `/uuid`, `/hash`,
`/jwt`, `/color`), domains & links (`/whois`, `/dns`, `/ssl`, `/subdomains`,
`/link-check`), lookups (`/status`, `/company`, `/username`), media & files
(`/image`, `/convert`), inspection (`/exif`, `/sauce`) and two automatic
modules (music links, translate button).

This page is the **contract every tool follows**. Per-theme details:

| Theme | Doc | Cog | Views |
|---|---|---|---|
| Text | [TOOLS_TEXT.md](TOOLS_TEXT.md) | `cogs/tools_text.py` | `utils/tools_text_views.py` |
| Security & dev | [TOOLS_SECURITY.md](TOOLS_SECURITY.md) | `cogs/tools_security.py` | `utils/tools_security_views.py` |
| Domains & links | [TOOLS_WEB.md](TOOLS_WEB.md) | `cogs/tools_web.py` | `utils/tools_web_views.py` |
| Lookups | [TOOLS_LOOKUP.md](TOOLS_LOOKUP.md) | `cogs/tools_lookup.py` | `utils/tools_lookup_views.py` |
| Media & files | [TOOLS_MEDIA.md](TOOLS_MEDIA.md) | `cogs/tools_media.py` | `utils/tools_media_views.py` |
| Inspection | [TOOLS_INSPECT.md](TOOLS_INSPECT.md) | `cogs/tools_inspect.py` | `utils/tools_inspect_views.py` |
| Music links | [MUSIC_LINKS.md](MUSIC_LINKS.md) | `cogs/music_links.py`, `modules/music_links.py` | `utils/music_links_views.py` |
| Translate button | [TRANSLATE_BUTTON.md](TRANSLATE_BUTTON.md) | `modules/translate_button.py` | `utils/translate_button_views.py` |

Pure logic (no Discord, no DB) lives in the `tools/` package and is unit-tested
on its own.

---

## 1. One card anatomy — `utils/tool_card.py`

Every answer is built with `ToolCard` so all tools read the same way:

```
### <icon> Tool title               ← heading(); an empty icon is simply dropped
<main result, first, copyable>       ← card.code(...) / card.text(...)
**Label** · `value`                  ← card.details([(label, value), ...])
────────
-# Provided by <logo> **Service** · requested by @user   ← card.footer(...)
[ action buttons ]                   ← card.build(rows=[...]) (outside the container)
```

- **The main result comes first**, in a code block when it is something to copy
  (a hash, a password, a UUID, JSON).
- **Details** are `**Label** · \`value\`` lines; dynamic values are always in
  backticks (`inline()` does it and neutralises backticks).
- **Accent = meaning**: `ACCENT_INFO` for information, `ACCENT_SUCCESS` /
  `ACCENT_WARNING` / `ACCENT_ERROR` for a verdict (pwned, contrast, domain age,
  link reputation, service status). Green is never decoration.
- **Footer = one dim line** with where the information comes from (§2) and, on
  public answers only, `requested_by()`.
- Sub-parts of a long card use `card.section(title, body)` (bold sub-heading),
  not extra `###` headings.
- `ToolCard.build(view=SomeBaseView(...))` fills a persistent `BaseView`;
  without `view=` it returns a plain non-interactive `LayoutView`.

## 2. Say where the information comes from — as a phrase

Like `/translate`'s `Translated by **DeepL**`, every card that relies on an
outside service names it **with a verb phrase**, never `Source: X`:

```python
from utils.emojis import WIKTIONARY
card.footer(source_line(locale, (WIKTIONARY, "Wiktionary"), verb="provided_by"))
# -> "-# Provided by <wiktionary logo> **Wiktionary**"   (fr: "Fourni par …")
```

- The **logo sits right next to the name** it belongs to (`(logo, name)` tuple),
  never at the start of the line. An empty logo (artwork not uploaded yet) is
  dropped, leaving `Provided by **Wiktionary**`.
- Verbs live under `tools.common.by.*`: `provided_by`, `data_provided_by`,
  `translated_by`, `checked_by`, `scanned_by`, `converted_by`,
  `summarized_by`, `found_by`, and `computed_locally` (no `{source}`: "Computed
  by Moddy itself, nothing is sent elsewhere" — use it for local tools like
  `/hash`, `/diff`, `/password`, it reassures people).
- Several services: `source_line(locale, (A_LOGO, "SauceNAO"), (B_LOGO, "trace.moe"), verb="found_by")`.
- Licence attributions (Wiktionary and WhatsMyName are CC BY-SA) are satisfied
  by naming them this way plus a link button to the original page when there is one.

## 3. Loading, errors, expired state

- Anything that can take more than ~1 s answers first with
  `loading_card(locale, text)` and then **edits** that message (the `/ocr`
  pattern). Long jobs pass `progress=(done, total)` for a live bar.
- Expected failures raise `ToolError(code, tool="<tool>", **params)` (or are
  rendered with `tool_error_card`). Text lookup: `tools.<tool>.errors.<code>`
  → `tools.common.errors.<code>` → `tools.common.errors.failed`. Params are
  wrapped in backticks automatically. Shared codes already exist for
  `timeout`, `network`, `rate_limited`, `quota`, `cooldown`, `not_found`,
  `unavailable`, `too_large`, `empty_input`, `guild_only`, `expired`.
- Every error tells the user what to do next. Never surface a raw exception.
- Unexpected exceptions are left to the central error handler (BaseView /
  BaseModal / `guarded` for DynamicItems).

## 4. Input: options for short values, Modals V2 for the rest

- A short value (a word, a domain, a colour) is a slash option.
- A long text, several fields, or **any secret** is a Modal V2
  (docs/guides/MODALS_V2.md): `Label` + `TextInput` / `RadioGroup` /
  `CheckboxGroup` / `Select` / `FileUpload`, with a short `TextDisplay` help
  line at the top of the modal.
- **Secrets never go in slash options**: `cogs/command_logger.py` logs option
  values to the technical webhooks. `/pwned` and `/jwt` take their input only
  from a modal; nothing typed there is logged, stored or cached.
- File input: a `discord.Attachment` option (and, where it helps, a
  `FileUpload` in a modal).

## 5. Visibility

- Default: `@add_incognito_option()` + `get_incognito_setting(interaction)`
  (user preference, ephemeral by default).
- **Always ephemeral, no incognito option**: `/pwned`, `/password`, `/uuid`,
  `/jwt`, `/exif` (may reveal GPS).
- Public answers containing user text are sent with `NO_MENTIONS`.
- Locale: `i18n.get_user_locale(interaction)` for anything one person reads;
  `guild_locale(bot, guild)` for what a whole server reads (module replies).

## 6. Persistence (CLAUDE.md rule 8)

- Every button/select has a namespaced custom_id
  `moddy:tools:<tool>:<action>[:params]` (modules: `moddy:<module>:...`).
- State that fits in the custom_id → `ui.DynamicItem` with a `template=` regex,
  callback wrapped with `@guarded` (from `utils/tool_card.py`).
- State that does not fit (a regex + its test text, a result list) →
  `save_state(bot, kind, payload)` returns a 10-char token stored in the
  custom_id; `load_state()` reads it back (Redis, 24 h TTL, LRU fallback). On
  expiry answer `expired_card(locale)`. This is the documented "short-lived
  state" exclusion in docs/guides/PERSISTENT_VIEWS.md.
- State that can be **re-derived from the message** (the image on an `/image`
  card, the SIREN in a select value) needs neither.
- Register classes by appending to `PERSISTENT_VIEWS` and DynamicItem samples to
  `DYNAMIC_ITEM_CASES` in the theme's views module — `utils/tool_views_registry.py`
  feeds `utils/persistent_views.py` and `tests/test_persistent_views.py`.
- Link buttons and result-only cards need nothing.

## 7. Network

| What | Use |
|---|---|
| Free keyless public API on a fixed host (Wiktionary, HIBP range, RDAP, DoH, crt.sh, Statuspage, data.gouv, Deezer, iTunes, Spotify pages, WhatsMyName sites) | `bot.public_api` (`services/public_api.py`): shared session, descriptive User-Agent, public-only resolver, per-host concurrency, `get_json(..., cache_ttl=)` |
| A URL or host **typed by a user** (link-check, ssl, image URL) | `utils/safe_http.py` (redirects re-validated hop by hop, public IPs only) — or `public_api`, which uses the same resolver, when following redirects yourself |
| Paid / keyed / quota-bound provider (OpenAI, DeepL, VirusTotal, urlscan, SauceNAO, trace.moe, CloudConvert) | the gateway (`docs/infra/API_GATEWAY.md`): adapter + client + rate rules + quota seeds |

`PublicAPIError.code` values: `timeout`, `network`, `rate_limited`,
`not_found`, `http_error`, `bad_payload`, `too_large` — map them to
`ToolError` codes of the same name.

CPU-heavy work (hashing big files, image processing, regex) runs in
`asyncio.to_thread` behind a small semaphore; never on the event loop.

## 8. Icons

All tool icons are constants in `utils/emojis.py` (block "UTILITY TOOLS" and
"Service logos"). Icons whose artwork is not uploaded yet are **empty strings**:
`heading()`, `with_icon()`, `button_emoji()` and `source_line()` drop them
cleanly, so filling an id later is a one-line change. Never borrow an unrelated
icon to fill a gap.

## 9. Strings

- Runtime strings: `tools.<tool>.*` in the 5 locale files (`fr`, `en-US`,
  `es-ES`, `pt-BR`, `de`); shared ones in `tools.common.*`.
- Short, plain sentences; a tool explains itself in its first line.
- Command names/descriptions: every `locales/commands/<locale>.json` (32 files).
- Each theme ships a test asserting its `tools.<tool>` keys are identical in
  the 5 locales.
