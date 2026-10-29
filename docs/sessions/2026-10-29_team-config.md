# 2026-10-29 — `t.config`: a server's /config panel for Moddy staff

## What was done
- New staff command `/team config` / `t.config` (`staff/commands/team/config.py`):
  opens the server's real `/config` panel (`ConfigMainView`) so a staffer can
  configure a server they are helping.
- **Only staff can interact with it.** The panel only ever exists as an
  ephemeral message (Discord lets nobody but its recipient see or click it):
  - slash: the answer is forced ephemeral (the `incognito` option is ignored);
  - message: `t.config` posts a launcher card (`StaffConfigLauncherView`,
    persistent, `moddy:staffconfig:launcher:open`) whose button re-checks on
    every click that the clicker may run `t.config`, then sends the panel
    ephemerally. Anyone else gets a refusal.
- `modules/configs/_common.py::check_guild_perms` (and the social
  notifications copy of it) now accept, besides *Manage Server*, a staffer
  clicking in an **ephemeral** panel (`utils/staff_config_views.is_staff_config_session`).
  A public `/config` panel never lets staff through.
- The staff check (`check_command_permission(user, TEAM, "config")`) is cached
  60 s per user so the 3 s interaction budget holds (each click is checked by
  the view and again by the callback; some callbacks open a modal). Failures
  fail closed.
- i18n: `staff.team.config.*` in the 5 locales.
- Tests: `tests/test_staff_config.py`.

## Files
- `staff/commands/team/config.py`, `utils/staff_config_views.py` (new)
- `modules/configs/_common.py`, `modules/configs/social_notifications_config.py`
- `utils/persistent_views.py`, `locales/*.json`
- Docs: `CLAUDE.md`, `docs/guides/STAFF_COMMANDS_FRAMEWORK.md`, `docs/guides/PERSISTENT_VIEWS.md`

## Decisions
- Ephemeral instead of a public panel guarded by a registry of message ids:
  no storage, survives restarts, cannot be clicked by a server member by
  construction, and does not show the server's moderation settings (AltGuard,
  automod…) to everybody in the channel.
- A message command cannot reply ephemerally, hence the one extra click on
  the launcher card.

## Follow-ups
- `tests/test_translate_button.py::TestDefaultEnabled::test_runs_where_nothing_is_stored_and_can_be_paused`
  already failed before this change (unrelated).
