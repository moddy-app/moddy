# 2026-09-09 — Announcement translation (support server)

## What was done

Announcements posted in the two support-server announcement channels are now
translated automatically into every language Moddy speaks except the one they
are written in, and offered under the announcement as one flag button per
language.

- A message posted in a watched channel is sent to DeepL **once per language
  other than its own** (through the gateway), and the set is stored in a new
  `announcement_translations` table keyed by the announcement's message id.
  DeepL reports the detected source language with the first translation, so an
  English announcement is never translated into English.
- Moddy replies with a container holding **only** the buttons (flag + language
  name written in that language, e.g. `🇩🇪 Deutsch`) — one per stored
  translation, so the announcement's own language gets no button.
- A click reads the stored row and answers ephemerally with the translated text
  alone, in a container: no title, no code block, no attribution line.

DeepL is therefore called at most five times per announcement, whatever the
number of readers or clicks — the requirement that drove the whole shape.

## Files

| File | Change |
|---|---|
| `cogs/announcement_translation.py` | New — listener, language table, buttons-only view, `AnnouncementLanguageButton` dynamic item |
| `db/repositories/announcement_translations.py` | New — save/get one announcement's translations |
| `db/base.py` | New `announcement_translations` table + repository mixin |
| `config.py` | New `ANNOUNCEMENT_TRANSLATION_CHANNEL_IDS` (defaults to the two channels, env-overridable) |
| `utils/persistent_views.py` | Registers the flag buttons (group 12l) |
| `tests/test_persistent_views.py` | Covers `AnnouncementLanguageButton` |
| `locales/{fr,en-US,es-ES,pt-BR,de}.json` | `announcement_translation.error.*` |
| `docs/ANNOUNCEMENT_TRANSLATION.md`, `CLAUDE.md` | Documentation |

## Decisions

- **Not a module.** Servers cannot enable this; it is hardcoded to the support
  guild and its two announcement channels. The channel list lives in `config.py`
  behind an env var so a staging server can point it elsewhere without a code
  change.
- **Buttons are `DynamicItem`s** carrying the announcement's message id, so they
  keep working across restarts with nothing held in memory — the stored row is
  the only state.
- **Button labels are not i18n'd.** A German reader looking at a French
  announcement finds their language because the button says "Deutsch", not
  because it was translated into the language they cannot read.
- **The source language gets no button**, and neither does a language whose
  DeepL call failed — a missing key in the stored set simply means no button,
  rather than a button that apologises on click. The error string exists only
  for a row that has gone missing.
- Mentions are neutralised before translation, so DeepL sees words and a
  translated copy cannot ping anyone.

## Follow-ups

- Message **edits** are not re-translated: the stored set is the announcement as
  first posted. An `on_message_edit` hook re-running the translation would be a
  small addition if it turns out to matter.
- Announcements posted by bots/webhooks are ignored (loop safety). If the team
  starts publishing through a webhook, that guard needs relaxing to "ignore
  Moddy itself" only.
