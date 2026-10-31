# Mini-bots

Mini-bots are small single-feature Discord applications (for example
"Bump Reminder") that run one of Moddy's modules under their own name. They
exist to bring in servers: a bot named after a single feature is easy to find
and to add. Once a server is in, it is offered to move to Moddy, where it gets
every option and the dashboard.

This document covers **the core** that runs and manages them. No concrete
mini-bot exists yet (`minibots/spec.py::SPECS` is empty); adding one is
described at the end.

## 1. Principles

| Rule | Why |
|---|---|
| **Same code, same database** as Moddy | A module runs unchanged under a mini-bot, and moving to Moddy needs no data migration. |
| **One process for every mini-bot** (`python -m minibots.main`) | Python, discord.py and the pools are paid once, not once per bot. |
| **One runner per module per server** | The `app` key of the module config names the owner; nobody else acts on it. |
| **Manual handoff** | Nothing moves to Moddy without an admin clicking "Transfer to Moddy". |
| **No dashboard for mini-bots** | They are configured in Discord. The only web pages their users reach are functional ones: AltGuard verification and ticket transcripts (`WEB_SURFACES`). |

## 2. Ownership: `guilds.data.modules.<id>.app`

```json
{"modules": {"bump_reminder": {"reminders": [...], "app": "bump_reminder_bot"}}}
```

- **Absent means `moddy`.** Every config written before mini-bots existed is
  Moddy's, with no migration. A Moddy config never carries the key: `stamp()`
  removes it.
- `minibots/ownership.py` is pure and holds the whole rule:
  - `config_app`, `is_owned_by`, `is_foreign`, `stamp`, `strip`;
  - `can_claim(config, claimant, present_apps, moddy_default_enabled)` — when an
    app may *start* running a module: its own config, or an empty one, or one
    whose owner has left the server. It never applies if Moddy already runs the
    module through its `DEFAULT_ENABLED` defaults.
- `ModuleManager(bot, app=...)`: the **same class** for Moddy and for every
  mini-bot.
  - **Loading.** `load_guild_modules` skips configs owned by another app. A
    foreign config also blocks the `DEFAULT_ENABLED` defaults, so a module never
    runs twice.
  - **Status.** `get_module_statuses` → `external`, `get_module_state` →
    `external`, `get_foreign_modules(guild) -> {module: app}`.
  - **Writes refused** on a foreign config:
    - `save_module_config` refuses with a translated message (`modules.config.external.save_refused`);
    - `delete_module_config` refuses;
    - `set_module_state` refuses with `foreign_app`.
  - **Dashboard push.** `reload_module` refuses with
    `{"ok": false, "error": "foreign_app", "app": ...}` and never touches the
    owner's panel.
  - **`save_module_config` keeps `app` out of validation** and stamps it back
    on, the same way as `state`.
  - `default_gate`: an async hook that decides whether a `DEFAULT_ENABLED`
    module may run its defaults in a server. Mini-bots use it: not where Moddy
    already does.

### The database write is atomic

`ModdyDatabase._update_entity_data` (behind `update_guild_data`) is a
read-modify-write of the whole JSON document. Since two processes now write the
same `guilds` rows, it runs in a transaction with `SELECT ... FOR UPDATE`.
Without that lock, two concurrent writes to different paths lost one of them.
This was verified with 20 parallel writes on PostgreSQL 16.

## 3. Presence: `app_guilds`

```sql
app_guilds (app TEXT, guild_id BIGINT, present BOOL, joined_at, left_at,
            member_count, PRIMARY KEY (app, guild_id))
-- partial index on (guild_id) WHERE present
```

- Written by **every** app, Moddy included (`moddy`):
  - on join and leave;
  - `reconcile_app_guilds(app, ids)` in `on_ready`, two statements whatever the
    number of servers.
- Read by:
  - `can_claim` (who is here);
  - `/dev minibots` (`count_guilds_by_app`);
  - `/team server` (`apps_in_guild`).
- Kept separate from `guild_events` / `guild_installs`, which remain Moddy's own
  retention history.
- Ownership changes go through `set_module_app(guild, module, expected, new)`.
  It is a **compare-and-swap** on the `app` key only: a second click or a
  concurrent transfer gets `False`.

## 4. The runtime (`minibots/`)

```
minibots/
  ownership.py  pure — who owns a config
  spec.py       MiniBotSpec + SPECS (registry), app_display_name — import-light
  status.py     Redis contract (pure serialisation), shared with Moddy
  resources.py  SharedResources: 1 asyncpg pool (MINIBOTS_DB_POOL_MAX=3), 1 Redis, i18n, TechLogger
  client.py     MiniBotClient(discord.Client): lean cache + ModuleManager(app=spec.id)
  runtime.py    MiniBotRuntime: supervision, heartbeat, control channel, periodic jobs
  main.py       entry point (python -m minibots.main)
```

### RAM: what was done and why

- **One process, N clients** on one event loop. The fixed cost is about **59 MB**,
  measured at import time and paid **once**. 45 MB of it is discord.py, the
  rest is asyncpg, Redis and aiohttp. Each extra bot only adds its gateway
  connection and its guild cache.
- **Minimal cache** per client:
  - `Intents.none()` + `guilds` + the spec's intents only;
  - `members` and `presences` are refused by `MiniBotSpec`;
  - `max_messages=None` (the default keeps 1000 messages *per bot*);
  - `MemberCacheFlags.none()`, `chunk_guilds_at_startup=False`.
- **Modules loaded lazily, per server**: an instance exists only where an event
  needed one (`get_module_instance` loads it). Nothing is preloaded at boot.
- **Light imports**:
  - `minibots/*` never imports `bot.py`, `cogs/`, `staff/`, onnxruntime, PIL,
    c2pa, numpy, openai or fastapi;
  - a spec's modules are imported only when its bot starts;
  - guarded by `tests/test_minibots.py::test_minibots_process_imports_stay_light`.
- **One task per periodic job** for every bot (`runtime.register_periodic`), not
  one loop per bot. The job dispatches through `runtime.client_for(app)`.
- **One small DB pool** (3 connections by default) for all bots.
- Recommended env: `MALLOC_ARENA_MAX=2` (docs/infra/RAILWAY.md).

### Supervision
- Each client runs in its own task. A crash restarts **that** client with
  exponential backoff (5 s → 300 s) and logs it; the others are unaffected.
- A rejected token (`LoginFailure`) stops retrying.
- A spec without a token is not started (`no_token` state).
- `MINIBOTS_ENABLED=id1,id2` narrows the list.

### What a module sees on `bot`
`MiniBotClient` exposes the attributes the existing modules read:
- `db`, `redis`, `module_manager`, `tech_logger`;
- `stats` (`None` for now);
- `module_slash_commands`;
- `resync_module_commands` (no-op: mini-bots publish global commands).

A module that needs another service (`bot.notifications`, …) gets it when the
mini-bot using it is built, not before.

## 5. Redis contract (`minibots:*`)

| Key / channel | Type | Content |
|---|---|---|
| `minibots:status` | hash | one field per app (JSON `AppStatus`: state, guilds, latency, restarts, last error) + `_process` (RSS, version, start). Rewritten every 30 s; older than 90 s = `silent`. |
| `minibots:apps` | hash | public registry per app (name, modules, `web_surfaces`, `application_id`) for the backend. |
| `minibots:control` | Pub/Sub | `release`, `reload_guild`, `restart`. Idempotent and non-destructive, so not signed. Anything else is ignored. |
| `minibots:ack:<request_id>` | list | the runtime's answer to a `release` (TTL 60 s). |
| `minibots:transfer:<guild>:<module>` | key | transfer lock (`SET NX EX 60`). |

The runtime **does not** listen to `moddy:bot`: there is no dashboard push for a
mini-bot.

**Backend contract.** A dashboard save on a module whose `app` is not `moddy`
must be refused, and the dashboard should show "Managed by X — transfer it from
/config". Moddy refuses the push anyway (`foreign_app`), but a direct DB write
that drops the `app` key would hand the config to Moddy **without** removing the
mini-bot's panel.

## 6. Transfer to Moddy

Entry points (all end on the same button):
1. **DM to the installer** when Moddy joins a server where a mini-bot runs
   modules (`bot.py::_minibots_on_join` → `utils/minibot_transfer.py::offer_transfer`).
   It sends one card per mini-bot, through `bot.notifications` (rule #11).
2. **`/config` main screen**: one banner + button per mini-bot.
3. **`/config` → a module run by a mini-bot**: an "Managed by X" screen instead
   of the module's own screen.

The button is `moddy:minibots:transfer:<guild_id>:<app>`, a persistent
`DynamicItem`.
- **Auth**: *Manage Server* in that guild, re-checked on every click. From a
  DM, the member is looked up in the guild.
- It moves **every** module of that app in that guild.

Sequence (`services/module_transfer.py::ModuleTransferService`, on
`bot.module_transfer`):
1. **Lock** `minibots:transfer:<guild>:<module>`, so a double click is a no-op.
2. **Release.** `release` is sent on `minibots:control`. The owning client runs
   `release_module` → `ModuleBase.on_transfer_out(to_app)`, which takes **its**
   panel down: a panel's buttons only ever reach the app that posted it. It then
   stops the module (blocked from reloading for 60 s) and acks.
   - No ack within 12 s (process down, client disconnected): Moddy runs
     `on_transfer_out` **itself**, best-effort. Deleting another app's message
     needs *Manage Messages*.
3. **Compare-and-swap** on `app` (`set_module_app`). The settings themselves
   never move.
4. **Adopt.** `ModuleManager.adopt_module` → load, enable, then
   `ModuleBase.on_transfer_in(from_app)`: re-post the panel, re-apply
   permissions, resync services. Then the guild's commands are resynced.
5. **Trace.** Tech log `minibots` and the `minibots.transfer` metric
   (`from_app`, `module`, `via` = `config` | `dm`), which measures conversion.

### The two module hooks
```python
async def on_transfer_out(self, to_app) -> dict:   # default: on_external_config_change(EXTERNAL_DELETED)
async def on_transfer_in(self, from_app) -> dict:  # default: on_external_config_change(EXTERNAL_UPDATED)
```
The defaults already cover the modules that have a panel:
- **tickets**: delete the panels → `refresh_all_panels`;
- **AltGuard**: delete the panel → re-post + channel permissions + membership
  resync to the service.

A module with **more** Discord state must override them. For example, **open
tickets**: their control messages were posted by the mini-bot, so their buttons
stay with it. That override is to be written together with a mini-bot that
runs tickets.

## 7. Staff and logs

- `/dev minibots` shows each app's state, servers, latency, restarts and last
  error, plus the process RAM and version.
- `/dev minibot <restart|reload> <app|*> [guild_id]` acts through
  `minibots:control`. There is deliberately **no** staff transfer command: the
  transfer is the admin's decision, and `t.config` opens the same panel for the
  team.
- `/team server` adds a "Mini-bots" section: apps present, and modules run by
  each.
- Tech logs go to category `minibots` (`LOG_WEBHOOK_MINIBOTS`):
  - lifecycle: ready, reconnected, crashed, restarted;
  - a mini-bot's server joins and leaves;
  - every transfer.
  - A mini-bot's errors go to the `error` category with an `App` line.
- Bump prospects (`services/bump_prospect_service.py`): a server where our
  Bump Reminder mini-bot runs the module (`external`) is no longer a prospect.

## 8. Adding a mini-bot

1. Create the Discord application and get its token.
2. Append to `minibots/spec.py::SPECS`:
   ```python
   MiniBotSpec(
       id="bump_reminder_bot", name="Bump Reminder", token_env="MINIBOT_BUMP_TOKEN",
       modules=("bump_reminder",), module_paths=("modules.bump_reminder",),
       commands_module="minibots.bots.bump_reminder",   # its own slash commands
       intents=frozenset({"guild_messages", "message_content"}),
   )
   ```
3. Write its commands and listeners (`commands_module.setup(client)`). The
   module's events must be wired to `client.module_manager`.
4. Check which `bot.<service>` the module reads, and expose those on
   `MiniBotClient` (or in `SharedResources` if they are shared).
5. Override `on_transfer_in` / `on_transfer_out` if the module has Discord
   state beyond a panel.
6. Set `MINIBOT_..._TOKEN` on the **mini-bots** Railway service.

## 9. Deployment (Railway)

- A second service from the same repository: start command
  `python -m minibots.main`, same `DATABASE_URL` / `REDIS_URL`.
- It does **not** need `DISCORD_TOKEN`: `MODDY_SERVICE=minibots` is set by the
  entry point and lifts that check.
- Env vars:
  - `MINIBOT_<X>_TOKEN` (one per mini-bot);
  - optional: `MINIBOTS_ENABLED`, `MINIBOTS_DB_POOL_MAX` (3);
  - `LOG_WEBHOOK_MINIBOTS` (on both services);
  - `MALLOC_ARENA_MAX=2`.
- Deploy order: Moddy first. It creates `app_guilds` and starts recording its
  own presence. Then the mini-bots service.
