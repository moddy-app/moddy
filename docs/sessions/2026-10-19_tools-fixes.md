# Session: utility tools fixes (menus, /diff, /color, /image, /subdomains, /sauce, /music)

**Date:** 2026-10-19
**Agent:** Claude Code

## Summary

A batch of fixes and removals on the utility tools after a first run in production.

## What was done

* **"Computed by Moddy itself…" footer removed** everywhere: the `computed_locally`
  verb is gone from `tools.common.by.*` (5 locales) and from every card. A tool
  computed on Moddy now shows no source line; `requested by` stays.
* **Startup error `CommandLimitReached` (5 message menus)**: the bot declared 7
  message context menus. `Find source` (tools_inspect) and `Check links` (tools_web,
  with its multi-link summary card) are removed; `/sauce` and `/link-check` stay as
  slash commands. Remaining menus, the original five: `Save Message`, `Get Emojis`,
  `Translate`, `AI text tools`, `Transcribe`. Loading the 12 affected cogs on a bare bot
  now succeeds. `tests/test_context_menu_limit.py` guards it (and checks every menu
  is localized in the 32 command locale files).
* **`/diff`**: the word mode (struck-through / bold) is gone; the answer is only a
  coloured ```` ```diff ```` block, with a one-line legend. The "Compare by" radio
  is removed from the modal.
* **`/sauce`**: could not be reproduced against SauceNAO (no key in the sandbox).
  trace.moe was checked live (URL and upload paths, real payload parsed correctly).
  Without `SAUCENAO_API_KEY` only anime screenshots can be recognised, so every
  other image ends on "no reliable source". The empty card now says so
  (`nothing_body_anime_only`) when SauceNAO was not asked.
* **`/color`**: the WCAG grid (AA / AAA × Discord dark / light / white / black) is
  replaced by a short "readability" section — black text and white text on the
  colour, each with a plain verdict and the ratio.
* **`/exif` removed**: cog command, `tools/metadata.py`, its cards and persistent
  "All tags" button, the `exif.used` metric, `mutagen` from requirements, the
  `tools.exif.*` strings and the command locale entries.
* **`/image`** ("This card no longer holds its image"): the Resize / Compress modals
  now keep the message the button was clicked on, and when a card message shows no
  image it is fetched back through the interaction token (works for ephemeral cards)
  with a warning log listing what was seen. Root cause on Discord's side could not
  be observed from the sandbox; the log will tell if it persists.
* **`/subdomains`**: crt.sh is regularly down (502 even for small domains, checked
  live). On failure Moddy now falls back to Cert Spotter (keyless, 3 pages max); the
  card names the provider that answered. The fallback answer is cached 30 min.
* **`/music link` → `/music`**: the group is now a single command (32 locale files
  updated, French is `/musique`).

## Files modified

`cogs/{emoji,tools_inspect,music_links}.py`, `services/web_tools_service.py`,
`tools/{diffing,color}.py`, `tools/domain/crtsh.py`, `tools/metadata.py` (deleted),
`utils/{tools_text,tools_security,tools_media,tools_web,tools_inspect}_views.py`,
`utils/{tool_card,tool_views_registry}.py`, `stats/registry.py`, `requirements.txt`,
`locales/*.json`, `locales/commands/*.json`, tests, docs (TOOLS*, MUSIC_LINKS,
VOICE_TRANSCRIPTION, OCR, TEXT_TOOLS, COMMAND_LOCALIZATION, EMOJIS, CLAUDE.md).

## Decisions

* Which two menus to give up: the two newest ones (Find source, Check links), keeping
  the five documented as taken (see VOICE_TRANSCRIPTION.md). `Get Emojis` was first
  dropped then restored at the user's request.
* No source line rather than a replacement wording for local tools.

## Known issues / follow-ups

* `/sauce` returning nothing everywhere should be checked against the Railway
  variable `SAUCENAO_API_KEY` first.
* `FRAME_INSPECT`, `LOCATION_ON`, `OPENSTREETMAP` emojis are unused now.
* The sandbox cannot install `asyncpg`; tests that import `db` were run with a stub.
