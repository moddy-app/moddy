# Session: Server language standardization

**Date:** 2026-08-23
**Agent:** Claude Code

## Summary

The server language was decided in five different places: AltGuard's *panel
language*, Automod AI's `langue_serveur`, the logs' `locale`, a language per
**ticket category**, and everything else reading `guild.preferred_locale`
directly. The same server could be greeted in English, warned in French and
logged in German.

There is now **one** setting — `guilds.data.settings.language`, edited in
`/config` → **Server settings** — read by every module through
`utils/guild_language.py`. Values: `auto` (default; follows
`guild.preferred_locale` **only when Community is enabled**, English
otherwise) or one of the five translated locales (`en-US`, `fr`, `es-ES`,
`pt-BR`, `de`).

Every per-module language dropdown was removed.

## Changes Made

### New

- `utils/guild_language.py` — the setting: pure resolution rules
  (`match_supported_locale`, `auto_locale`, `resolve_locale`), the stored value
  (`get_language_setting` / `set_language_setting`, cached per guild,
  `invalidate_guild_language`), and the two readers: `guild_locale()` (async,
  authoritative) and `guild_locale_cached()` (sync hot paths — automod, appeal
  cards — cache-or-automatic + background warm-up).
- `modules/configs/server_settings_config.py` — `ServerSettingsConfigView`,
  persistent, Manage Server auth, one dropdown that saves on selection.
- `docs/SERVER_LANGUAGE.md` — the contract (rules, code usage, dashboard
  event, adding a language).
- `tests/test_guild_language.py` — 14 tests: locale matching, the Community
  rule, cache/invalidation, DB failure degradation, the sync reader.

### Wiring

- `cogs/config.py` — a **Server settings** button on the `/config` root panel
  (`moddy:config:main:settings`), present in all three render paths including
  the registration shell.
- `utils/persistent_views.py` — `ServerSettingsConfigView` registered.
- `modules/module_manager.py` — `ModuleBase.LANGUAGE_DEPENDENT_MESSAGES` and
  `ModuleManager.apply_language_change(guild_id)`: reloads the modules that own
  a *posted* message so it is re-posted translated. Its own two
  `preferred_locale` reads now use the setting.
- `bot.py` — `settings_updated` Pub/Sub event → `_handle_settings_push()`:
  invalidate + re-apply panels.

### Removed language selectors

- **AltGuard** (`modules/altguard.py`, `modules/configs/altguard_config.py`):
  `panel_locale`, `PANEL_LOCALES`, `DEFAULT_PANEL_LOCALE` and the dropdown
  gone. `panel_locale` is now resolved from the server language on every
  `load_config`, so a language change (which reloads the module) re-posts a
  translated panel. `LANGUAGE_DEPENDENT_MESSAGES = True`.
- **Automod AI** (`modules/automod_ai.py`,
  `modules/configs/automod_ai_config.py`): `langue_serveur` gone from the
  schema, the defaults, the validation and the UI; `guild_locale()` delegates
  to `guild_locale_cached`. The "Limits & language" section is now "Limits".
- **Server logs** (`modules/logs.py`, `serverlogs/service.py`,
  `modules/configs/logs_config.py`): `locale` gone from the stored config and
  the options screen; `LogService.locale_for()` is async and reads the server
  language.
- **Tickets** (`modules/tickets.py`, `services/ticket_service.py`,
  `modules/configs/tickets_category_config.py`,
  `tickets_panel_config.py`): the per-category `locale`, `TICKET_LOCALES`,
  `DEFAULT_TICKET_LOCALE`, the `TicketCategoryLocale` dynamic item and the
  category-screen language section are gone. `ticket_locale()` is async and
  takes the guild. `LANGUAGE_DEPENDENT_MESSAGES = True`.
  `utils/emojis.py`: `TICKET_LANGUAGE` removed.

### Converted from `guild.preferred_locale`

`modules/welcome_channel.py`, `modules/welcome_dm.py`, `modules/starboard.py`,
`modules/voice_transcription.py`, `utils/transcription_views.py`
(`card_locale` is now async), `services/expiration_notifier.py`,
`services/appeal_service.py`, `cogs/social_notifications.py`,
`cogs/blacklist_check.py`, `cogs/moderation_commands.py` (`_guild_locale`,
`_suggestion_language`).

### i18n

- Added `modules.config.settings.*` (button, title, description, language
  section, `auto`, `auto_resolves_to`, `applies_to`) to the five locale files.
- Removed the now-dead blocks: `modules.altguard.config.language`,
  `modules.automod_ai.config.language`, `modules.logs.config.options.locale`,
  `modules.tickets.category.language_{title,hint}`; `section_limits` reworded;
  `modules.tickets.panel.category_summary` lost its `{language}` parameter.

### Docs

`CLAUDE.md` (i18n rule + structure + index), `docs/MODULE_SYSTEM.md` (which
language a module speaks), `docs/ALTGUARD.md`, `docs/AUTOMOD_AI.md`,
`docs/AUTOMOD_AI_CONFIG.md`, `docs/LOGS.md`, `docs/TICKETS.md`,
`docs/BACKEND-INTEGRATION.md`, `docs/REDIS_COMMUNICATION.md`.

## Decisions & Rationale

- **Stored outside `modules`** (`guilds.data.settings.language`): it is not a
  module's property, and putting it in one would have made every other module
  depend on that module's config being present.
- **Community gates the automatic mode**, as requested: outside Community,
  `preferred_locale` is an account default nobody on the server chose, so it
  falls back to English rather than guessing.
- **Per-category ticket languages were removed too.** They are a real (if
  rare) capability — a French and an English support category in one server —
  but they are exactly the fragmentation this task set out to remove, and a
  ticket now speaks the same language as the rest of the bot. Worth revisiting
  as an explicit per-category *override* if a server asks for it.
- **Saves on selection** rather than behind a Save button: a single setting has
  no half-saved state worth explaining, and the dropdown shows its own value
  (CLAUDE.md rule #9). The only text line is what "automatic" currently
  resolves to — the one thing the control cannot show — and it is printed only
  while `auto` is selected.
- **A sync reader exists on purpose.** Automod runs once per moderated message
  and the appeal cards render synchronously; making them await a DB read would
  have been a worse trade than a cold-cache first message rendered with the
  automatic language.
- **Panels are re-posted, not left stale**: `LANGUAGE_DEPENDENT_MESSAGES` +
  `apply_language_change()` reuse the dashboard-push path rather than adding a
  second mechanism.

## Known Issues / Follow-ups

- [ ] **Backend/dashboard**: the dashboard must write
      `guilds.data.settings.language` and publish
      `{"type": "settings_updated", "guild_id": …}` on `moddy:bot`. Without
      that event the bot serves the cached value until it restarts.
- [ ] **Stored leftovers**: old `panel_locale` / `langue_serveur` / `locale` /
      per-category `locale` values are simply ignored on load (no migration
      written — nothing breaks, they just stop being read). A one-off cleanup
      could drop them from `guilds.data`.
- [ ] Servers that relied on a non-Community `preferred_locale` (e.g. a French
      non-Community server) now get English until an admin picks a language
      explicitly. This is the requested behaviour, but worth announcing.
