# 2026-06-23 — Staff commands redesign (framework + /dev group)

## Goal
Rebuild the staff command system: one scalable, standardized engine exposing
every command as a **slash command** on OFFICIAL Moddy servers and as a
**message command** everywhere Moddy is. Localized, Components V2 with colour
accents, Modals V2 where useful, default-ephemeral slash with an `incognito`
option, one file per command.

## What was done
- **New framework** under `staff/framework/`:
  - `command.py` — `StaffCommand` base, `SlashOption`, `@staff_command` registry.
  - `context.py` — `StaffContext` unifying message + slash (`send`, `open_modal`,
    locale, `incognito`).
  - `registry.py` — auto-discovers `staff/commands/**`, builds one
    `app_commands.Group` per type (`/dev`…), **dynamically** generates each slash
    callback and auto-injects the `incognito` option.
  - `design.py` — standardized Components V2 panels (`### <emoji> Title`, accent
    bars per kind/type), all returning `BaseView`.
  - `cog.py` — single dispatcher: message routing, slash runner, centralized
    permission checks, automatic audit logging, error handling.
  - `parsing.py` — user/guild id helpers.
  - Entrypoint extension `staff/staff_commands.py` (auto-loaded).
- **`/dev` group fully migrated** (15 commands, one file each in
  `staff/commands/dev/`): reload, shutdown, stats, sql (danger-confirm view),
  jsk (Modal V2), error, sync, serverlist (pagination), disable, enable,
  disabled, cogs, presence (choices), announcements, **official** (new — toggles
  the `OFFICIAL` attribute and re-syncs that guild).
- **OFFICIAL-only sync** in `bot.py`: `get_official_guild_ids()` +
  `_register_guild_command_set()`; staff groups are synced only to guilds with
  the `OFFICIAL` attribute, never to the global tree.
- **Permissions**: framework supports a fine-grained `permission` node per
  command; added `redirect_manage`, `banner_manage`, `stripe_manage`,
  `official_manage` nodes (+ labels) so "dev-by-laziness" commands can move to
  proper departments later.
- **Localization**: added `staff.*` keys to `locales/en-US.json` and `fr.json`
  (108 keys, FR + EN, audited — no missing keys).
- **No double-dispatch**: legacy `staff/dev_commands.py` now defers any command
  owned by the new router.
- Docs: `docs/STAFF_COMMANDS_FRAMEWORK.md`, CLAUDE.md structure update.

## Validation
- All files byte-compile.
- Built the registry against discord.py 2.7.1: 15 commands discovered, `/dev`
  group builds, options typed correctly, choices resolved, `incognito` injected,
  and the full group **serializes to a valid Discord payload**.
- i18n key audit: every referenced `staff.*` key exists in both locales.

## Known follow-ups
- Migrate remaining groups (`/team`, `/manage`, `/mod`, `/support`, `/com`).
- Recategorize dev `redirect` / `banner` / `sub-refresh` into proper departments
  using the new permission nodes (per user request — Stripe/ops aren't dev).
- Optional: an `app_commands.Translator` to localize slash command *descriptions*
  (response bodies are already localized).
- `t.flex`, help, etc. to be redesigned when their groups are migrated.
```
