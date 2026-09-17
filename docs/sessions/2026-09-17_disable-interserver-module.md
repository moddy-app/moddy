# 2026-09-17 — Temporarily disable the Inter-Server module

## What was asked

Temporarily disable the `interserver` module (server-to-server message
relay), reversibly, without losing any guild's existing configuration.

## What changed

`modules/module_manager.py`:

- New `DISABLED_MODULES: set` at module level, currently `{"interserver"}`.
- `ModuleManager.register_module()` skips registration (with a log line)
  for any `MODULE_ID` in that set, before the usual registration path.

Effect: `InterServerModule` is discovered (its file still imports fine) but
never added to `registered_modules`. Consequences, all handled by existing
code paths (same as an unregistered/renamed legacy module):

- It disappears from the `/config` module dropdown
  (`ModuleManager.get_available_modules()` only lists `registered_modules`).
- `ModuleManager.load_guild_modules()` silently skips any guild's stored
  `interserver` config at startup — logged as "configured but not
  registered - likely obsolete", exactly like `LEGACY_MODULE_IDS`.
- Nothing is deleted from the database: a guild's `modules.interserver`
  JSONB blob is untouched and becomes active again the moment `interserver`
  is removed from `DISABLED_MODULES`.

Not touched: `cogs/interserver_commands.py` (`/interserver report` /
`/interserver info`) — those look up already-relayed messages by id and
don't depend on the module being currently enabled anywhere, so they keep
working for messages relayed before the module was paused.

## Decisions made and why

- Reused the same "unregistered module id" code path `LEGACY_MODULE_IDS`
  already exercises, instead of adding a new enabled/disabled flag read at
  every call site — zero new branching in the load/save/reload paths.
- Left the module's own file (`modules/interserver.py`) completely
  unmodified so re-enabling is a one-line revert
  (`modules/module_manager.py::DISABLED_MODULES`).

## Known issues / follow-ups

- Re-enable by removing `"interserver"` from `DISABLED_MODULES` in
  `modules/module_manager.py`.
