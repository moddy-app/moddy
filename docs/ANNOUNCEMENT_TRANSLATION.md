# Announcement Translation

> Support-server only. Every announcement posted in the Moddy support server's
> announcement channels is translated once into every language Moddy speaks
> except its own, and offered behind one flag button per language.

---

## What it does

1. Somebody posts a message in one of the watched channels.
2. Moddy sends the message to DeepL **once per language other than its own**.
   DeepL reports the detected source language with every translation, so the
   announcement's own language is skipped once it is known and dropped from the
   set afterwards — the order the calls happen in does not matter. A result that
   comes back identical to the announcement is dropped too, which covers a
   detection DeepL got wrong or did not report (it happens on very short texts).
3. The whole set is stored in `announcement_translations`, keyed by the
   announcement's message id.
4. Moddy replies to the announcement with a container holding **only** buttons
   — flag + language name written in that language, one per stored translation.
   The announcement's own language gets no button: offering to translate a
   message into the language it is already written in is noise.
5. Clicking a button shows the stored translation ephemerally, as plain text in
   a container: no title, no code block, no attribution line.

Translating at post time rather than on click is the point of the design: an
announcement read by a thousand people costs four or five DeepL calls, not a
thousand. A click is an indexed primary-key read.

---

## Scope

| | |
|---|---|
| Guild | `MODDY_TEAM_GUILD_ID` (support server) |
| Channels | `ANNOUNCEMENT_TRANSLATION_CHANNEL_IDS` — defaults to `1398625728467173376` and `1444505508546478232` |
| Languages | `fr`, `en` (EN-US), `es`, `pt` (PT-BR), `de` |
| Provider | DeepL, through the API gateway (`bot.gateway.translation`) |

This is **not** a module: servers cannot enable it, there is no `/config`
screen, and nothing is stored under `guilds.data.modules`. It is a support-server
tool, hardcoded to those channels (overridable by env var so a staging server
can point it elsewhere).

Messages from bots and messages with no text content are ignored. Mentions are
neutralised (`@everyone` → zero-width-joined, `<@id>` → `@display name`) before
the text reaches DeepL, so the translation contains words rather than raw ids
and nothing can ping from a translated copy.

---

## Files

| File | Role |
|---|---|
| `cogs/announcement_translation.py` | The listener, the language table, the buttons-only view and the `AnnouncementLanguageButton` dynamic item |
| `db/repositories/announcement_translations.py` | `save_announcement_translations()` / `get_announcement_translations()` |
| `db/base.py` | `announcement_translations` table creation |
| `config.py` | `ANNOUNCEMENT_TRANSLATION_CHANNEL_IDS` |
| `locales/*.json` | `announcement_translation.error.*` (the one error a click can produce) |

---

## Storage

```sql
CREATE TABLE announcement_translations (
    message_id   BIGINT      PRIMARY KEY,  -- the announcement itself
    guild_id     BIGINT      NOT NULL,
    channel_id   BIGINT      NOT NULL,
    source_lang  TEXT,                     -- detected by DeepL: fr/en/es/pt/de
    translations JSONB       NOT NULL,     -- {"fr": "...", "en": "...", …}
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

One row per announcement, upserted. The detected source language is **not** a
key of `translations`; neither is a language whose result came back identical to
the announcement, nor one whose DeepL call failed. A missing key simply means no
button, rather than a button that apologises.

---

## Persistence

The buttons are `discord.ui.DynamicItem`s whose `custom_id` is
`moddy:anntr:lang:<code>:<message_id>`. The class is registered at startup via
`AnnouncementTranslationView.register_persistent()`
(`utils/persistent_views.py`), so a click after a restart reconstructs the item
from its custom_id and reads the row back — nothing is kept in memory. Auth is
public: the button only ever shows a translation of a message its clicker can
already read.

---

## Adding a language

Add an entry to `LANGUAGES` in `cogs/announcement_translation.py`
(`code → (DeepL target, flag, label in that language)`), map DeepL's source code
for it in `_SOURCE_TO_CODE`, and extend the button's `template=` alternation to
accept the new code. Five buttons is the per-ActionRow maximum — with the source
language dropped, five languages fit; a sixth needs a second row in
`build_view()`.

The labels are deliberately **not** translated: a button offering German reads
"Deutsch" whoever is looking at it, which is the only way a reader who does not
speak the announcement's language can find their own.
