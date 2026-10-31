# 2026-10-31 — Bump Reminder: activation offer when a directory joins

## What was done
- When a supported directory (DISBOARD, DiscordL, French.gg…) joins a server
  Moddy is already in, Moddy DMs **the person who added it** (audit log
  `bot_add`, fallback: owner) an offer to activate the Bump Reminder.
- The DM holds a channel picker: one pick creates the reminder for that
  directory with its defaults and turns the card into an "activated" card.
- Not offered when the directory already has a reminder, the module is paused,
  or the same (server, directory) was offered in the last 30 days.

## Files modified
- `services/bump_suggestion_service.py` (new)
- `cogs/bump_reminder.py` — `on_member_join` listener
- `utils/bump_views.py` — offer card, done card, `BumpSuggestChannelSelect`
  (persistent DynamicItem registered by `BumpReminderPersistence`)
- `locales/{fr,en-US,es-ES,pt-BR,de}.json` — `modules.bump_reminder.suggest.*`
- `tests/test_bump_reminder.py` — `TestSuggestion`
- `docs/features/BUMP_REMINDER.md`, `CLAUDE.md`

## Decisions
- DM to the adder rather than a channel post: they are the one who acts, and a
  public card would reach everyone but them (same rule as the install welcome).
- Picker of channels (StringSelect) rather than ChannelSelect: a ChannelSelect
  in a DM cannot list the server's channels. Channels named "bump" first.
- Defaults only (no roles, `button` ping mode, directory cooldown): the least
  friction; everything else stays in `/config`.

## Known issues / follow-ups
- Channel list is captured when the DM is sent (25 max); a stale pick is
  re-validated by `save_module_config`.
- Pre-existing unrelated failures: `test_automod_images.py::test_safesearch_respects_pacing`,
  `test_translate_button.py::TestDefaultEnabled::test_runs_where_nothing_is_stored_and_can_be_paused`.
