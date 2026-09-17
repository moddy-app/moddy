# 2026-09-17 — /config no longer requires Administrator; per-module bot permissions

## What was asked

Moddy's `/config` command refused to open at all unless the bot itself held
the **Administrator** permission in the guild — a hard requirement that
blocks listing on bot directories like top.gg, which reject
Administrator-only bots. Asked to make the bot request only the specific
Discord permissions each module actually needs, checked per module at
configuration time.

## What changed

- `modules/module_manager.py` — `ModuleBase` gained
  `REQUIRED_BOT_PERMISSIONS: List[str] = []`, a declarative list of
  `discord.Permissions` flag names a module needs the bot to hold.
- Set on every module that needs more than the baseline (view/send in the
  invoking channel), based on reading each module's (and the service it
  delegates to) actual Discord API calls:
  - `auto_role`, `auto_restore_roles`, `altguard` → `manage_roles`
  - `adaptive_slowmode` → `manage_channels`
  - `tickets` → `manage_channels`, `manage_roles` (channel creation +
    per-role overwrites in `services/ticket_service.py`)
  - `bot_customization` → `change_nickname` (`guild.me` nick patch)
  - `interserver`, `logs` → `manage_webhooks` (relay/log delivery webhooks)
  - `automod_ai` → `manage_messages`, `ban_members`, `moderate_members`
    (message deletion, bans, timeouts)
  - `bump_reminder`, `social_notifications`, `voice_transcription`,
    `welcome_channel`, `welcome_dm`, `starboard` → nothing beyond baseline.
- `modules/configs/_common.py` — new `missing_bot_permissions()` and
  `check_bot_perms()`. The latter builds a Components V2 "missing
  permissions" message naming exactly what's missing (reusing
  `utils/team_access_views.py::permission_label`, already translated in all
  5 locales under `modules.logs.permissions.*`) with a re-invite link scoped
  to only those permissions (`&permissions=<bitfield>&guild_id=<id>`).
- `cogs/config.py`:
  - Removed the blanket `bot_member.guild_permissions.administrator` gate
    in the `/config` command handler.
  - `ConfigMainView.on_module_select` now calls `check_bot_perms()` with the
    selected module's `REQUIRED_BOT_PERMISSIONS` right before building its
    config view, instead of gating the whole command.
- `locales/*.json` (all 5) — `modules.config.errors.no_admin_perms` replaced
  with `missing_bot_perms` (`{module}` + `{permissions}` placeholders).
- `CLAUDE.md` — new rule **#12: Never require Administrator — declare
  per-module bot permissions instead**.
- `docs/MODULE_SYSTEM.md` — documented `REQUIRED_BOT_PERMISSIONS` on the
  worked module example.

## Decisions made and why

- Reused the existing `/team access` permission catalogue's i18n labels
  (`modules.logs.permissions.*`) instead of inventing a new translation set
  — same wording an admin already reads in their own audit log config.
- The re-invite link requests only the *missing* permissions rather than
  the full recommended set, so the OAuth screen matches exactly what the
  error names.
- The user-facing "Manage Server" requirement on whoever *runs* `/config`
  was untouched — it was already scoped correctly (`modules/configs/_common.py::check_guild_perms`).

## Known issues / follow-ups

- None of the module permission lists were smoke-tested against a live
  Discord guild in this session (no gateway connection available); they are
  based on static code review of each module's own Discord API calls.
