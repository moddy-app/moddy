# 2026-09-09 — Staff commands for Bot Customization access via a guild attribute

## What was done

Added two staff commands (Manager role, `bot_customization_manage` permission)
to grant/revoke access to the Bot Customization module's identity fields
(nickname, avatar, banner, bio) for a specific server independently of its
premium subscription, and to list every server that currently has that grant:

- `/manage customization grant <guild_id> [add|remove]` —
  `staff/commands/manage/bot_customization/grant.py`
- `/manage customization list` —
  `staff/commands/manage/bot_customization/list.py`

The grant is stored as a new `BOT_CUSTOMIZATION` guild attribute (via the
existing `db.set_attribute` / `db.get_guilds_with_attribute` attribute
system), following the same pattern as the existing `OFFICIAL` attribute
(`staff/commands/dev/official.py`).

`modules/bot_customization.py` gained `has_identity_access(bot, guild_id)`,
which returns `True` when the guild is premium (`is_guild_premium`) **or**
carries the `BOT_CUSTOMIZATION` attribute. All four places that previously
called `is_guild_premium` directly to gate the identity fields now call this
helper instead:

- `modules/bot_customization.py::handle_backend_task` (dashboard task path)
- `modules/configs/bot_customization_config.py::_submit_identity`
- `modules/configs/bot_customization_config.py::BotCustomizationConfigView.create`
- `modules/configs/bot_customization_config.py::BotCustomizationConfigView.on_edit_identity`

The free name style (font/effect/colours) stays ungated, as before.

## Files modified

- `staff/commands/manage/bot_customization/__init__.py` (new, empty package marker)
- `staff/commands/manage/bot_customization/grant.py` (new)
- `staff/commands/manage/bot_customization/list.py` (new)
- `modules/bot_customization.py` — added `has_identity_access`, used it in `handle_backend_task`
- `modules/configs/bot_customization_config.py` — replaced the three direct `is_guild_premium` checks
- `utils/staff_role_permissions.py` — new `bot_customization_manage` permission node (Manager role)
- `locales/{en-US,fr,es-ES,pt-BR,de}.json` — new `staff.manage.customization.*` keys
- `docs/BOT_CUSTOMIZATION.md`, `docs/PREMIUM.md`, `docs/STAFF_SYSTEM.md` — documented the new attribute and commands

## Decisions made and why

- **New attribute name, not `PREMIUM`.** `docs/PREMIUM.md` explicitly warns
  there is no `PREMIUM` guild attribute and that one must not be used to gate
  a server feature (premium is subscription-derived only, via
  `subscription_servers`). A dedicated `BOT_CUSTOMIZATION` attribute keeps
  this a feature-specific staff override — exactly like `OFFICIAL` is a
  staff-only switch, not a premium proxy — instead of reviving that
  anti-pattern.
- **OR'd with premium in one helper**, not a separate code path, so every
  existing gate (config panel, modal open, dashboard task) stays a single
  source of truth and can't drift.
- Modeled the two commands directly on `staff/commands/dev/official.py`
  (grant/revoke by attribute) and `staff/commands/manage/redirect/list.py`
  (paginated listing panel) — both established patterns in this codebase.

## Known issues / follow-ups

- None. `tests/test_bot_customization.py` (22 tests) and
  `tests/test_persistent_views.py` (314 tests) pass unchanged.
