# AltGuard: applying a configuration pushed by the backend/dashboard

## What was done

The dashboard writes module configs straight into `guilds.data.modules.<id>` and
notifies the bot on `moddy:bot`. The bot only dropped its module cache in
response — enough for a module whose config is just values, wrong for AltGuard,
where half the configuration lives in Discord. A dashboard save left the
verification panel in the old channel, in the old language, the newly created
channels open to the unverified role, and the service unaware of the guild's
membership until the next hourly resync. It looked configured and was not.

Added a generic hook rather than special-casing AltGuard in `bot.py`, since
every module with a visible panel has the same gap:

- `ModuleBase.on_external_config_change(action)` — default no-op; a module
  implements it to re-apply the Discord side of a config it did not save itself.
- `ModuleManager.reload_module(guild, module_id, action=...)` — re-reads the
  config, refreshes the instance, runs the hook, returns a JSON-serialisable
  recap. **This method did not exist**: `bot._process_task` has been calling it
  for the `update_panel` stream task since forever, raising `AttributeError` into
  a swallowing `except`. That task has never worked.
- `AltGuardModule.on_external_config_change` — re-posts the panel, re-applies
  channel overwrites, resyncs membership with the service. An incomplete config
  takes the panel down (a button that cannot work must not stay up); a deletion
  takes it down without writing back.
- `bot._handle_module_config_push` — routes both Redis paths (Pub/Sub
  `moddy:bot`, stream `update_panel`) and acks on `moddy:dashboard` with
  `module_config_applied`.
- `AltGuardCog.resync_guild(guild)` — extracted from the hourly loop body so the
  hook reuses the member-cache-completeness guard instead of duplicating it.
- `AltGuardModule.delete_panel(persist=...)` — new keyword.

## Decisions made and why

- **`module_id` is opt-in, not required.** The historical payload (`type` +
  `guild_id`, no `module_id`) still means "drop the cache, apply nothing", so no
  backend change is forced and nothing existing breaks. Carrying `module_id` is
  what upgrades a push into a full apply.
- **The bot never trusts the payload for values.** It re-reads the row from the
  database. The event is a notification, not a transport.
- **An empty stored config is always read as a deletion**, whatever the event
  said — `delete_module_config` stores `{}` rather than removing the key, so the
  two are indistinguishable at rest and treating them differently would leave
  orphan panels.
- **`persist=False` on a pushed deletion.** `delete_panel` normally writes
  `message_id: None` back; doing that after the config row was emptied would
  recreate a half-configured module. This is subtle enough to have its own test.
- **Snowflakes leave as strings** in the ack (`panel_message_id`) — a 64-bit id
  loses precision as a JSON number.
- **A hook failure does not fail the reload.** The config is stored and loaded by
  then; the error is reported as `hook_error` instead of pretending the reload
  did not happen.
- **Both transports kept.** Pub/Sub drops a push made while the bot is
  restarting, which for a user-facing save is a silent wrong state; the
  `moddy:tasks` stream replays it. Documented as: stream for a real save,
  Pub/Sub for low-stakes invalidation.

## Files modified

- `modules/module_manager.py` — `EXTERNAL_UPDATED`/`EXTERNAL_DELETED`,
  `on_external_config_change` hook, `reload_module`, `_run_external_hook`
- `modules/altguard.py` — hook override, `_resync_membership`,
  `delete_panel(persist=...)`
- `cogs/altguard.py` — `resync_guild` extracted from `resync_membership`
- `bot.py` — `_handle_module_config_push`, `update_panel` task rewired
- `tests/test_altguard.py` — 4 tests (push applies all three effects, deletion
  writes nothing back, incomplete config removes the panel, `persist` flag)
- `docs/ALTGUARD_INTEGRATION.md` — new §5 (event, ack, guaranteed delivery,
  backend checklist) + 4 symptom rows
- `docs/ALTGUARD.md` — "Saving from the dashboard"
- `docs/MODULE_SYSTEM.md` — §3 bis on the hook
- `docs/REDIS_COMMUNICATION.md` — inventory rows for `moddy:bot`,
  `moddy:dashboard`, `moddy:tasks`

## Known issues / follow-ups

- **A deletion that arrives with a cold cache cannot clean up.** The config is
  already `{}` in the DB, so nothing knows the panel's `message_id`. The ack says
  `cleaned: false` and the panel is orphaned. Fixing it properly means either the
  backend echoing the previous `message_id` in the payload, or the bot keeping
  panel ids outside the module config. Documented in the backend checklist for
  now.
- No other module implements the hook yet. `welcome_channel`, `starboard` and
  `interserver` have dashboard-visible state worth auditing for the same gap.
- Tests cover the AltGuard hook, not `reload_module` itself (it needs a
  `ModuleManager` with a DB stub). Worth adding when a second module implements
  the hook.
