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
  resources.py  SharedResources: 1 asyncpg pool (MINIBOTS_DB_POOL_MAX=3), 1 Redis, i18n,
                TechLogger, process patches (icon theme, interaction guard, emoji swap)
  client.py     MiniBotClient(commands.Bot): lean cache + ModuleManager(app=spec.id)
  builtin.py    automatic /config, /about, welcome DM, error handling, views, command sync
  staff.py      staff commands on a mini-bot (@Bot d.jsk …), allowlist
  queries.py    info questions Moddy's staff commands ask the mini-bots (Redis)
  emojis.py     Moddy's application emojis copied to each app + send-time id swap
  runtime.py    MiniBotRuntime: supervision, heartbeat, control channel, periodic jobs
  main.py       entry point (python -m minibots.main)
```

### RAM: what was done and why

- **One process, N clients** on one event loop. The fixed cost is about **59 MB**,
  measured at import time and paid **once**. 45 MB of it is discord.py, the
  rest is asyncpg, Redis and aiohttp. Each extra bot only adds its gateway
  connection and its guild cache.
- **Minimal cache** per client:
  - `Intents.none()` + `guilds` + `guild_messages` + the spec's intents only.
    `guild_messages` is needed for `@Bot d.jsk`: a message that mentions the
    bot carries its content without the privileged intent. Nothing is cached
    (`max_messages=None`), but every message of every server still reaches
    the process, so it costs some CPU;
  - `members` and `presences` are refused by `MiniBotSpec`;
  - `max_messages=None` (the default keeps 1000 messages *per bot*);
  - `MemberCacheFlags.none()`, `chunk_guilds_at_startup=False`.
- **Modules loaded lazily, per server**: an instance exists only where an event
  needed one (`get_module_instance` loads it). Nothing is preloaded at boot.
- **Light imports**:
  - the process never imports `bot.py`, onnxruntime, PIL, c2pa, numpy, openai
    or fastapi. It does import what the built-ins need (`cogs.config`,
    `cogs.error_handler`, the notification views, the 7 allowed staff
    commands): about 57 MB measured at import, all included;
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
`MiniBotClient` (a `commands.Bot`) exposes what the shared code reads:
- `db`, `redis`, `module_manager`;
- `tech_logger` (its own, labelled with the bot), `staff_logger`, `notifications`;
- `is_developer`, `launch_time`;
- `stats` (`None` for now), `module_slash_commands`;
- `resync_module_commands` (no-op: mini-bots publish global commands).

A module that needs another service gets it when the mini-bot using it is
built, not before.

### Built-ins: what every mini-bot gets automatically (`builtin.py`)
- **`/config`** (guild only, Manage Server). With a single module, it opens
  that module's screen directly; with several, the regular main screen. The
  screens and the saves are Moddy's own; the module manager stamps the config
  with the mini-bot's `app`. The main screen shows "Get Moddy" instead of the
  dashboard links. A module Moddy (or another app) runs in that server shows
  "Managed by X here", with no button.
- **`/about`**: what the bot does, "made by the Moddy team", server count, and
  links (add this bot, get Moddy, support, docs).
- **Welcome DM** to whoever added the bot (installer from the audit log,
  otherwise the owner), sent through `client.notifications` (service
  `minibots`, rule #11).
- **Errors**: the `ErrorTracker` cog, so the same error cards and codes as Moddy.
- **Persistent views**: the common /config ones + `MiniBotSpec.persistent_views`.
- **Command sync**: global, and only when the commands or their localizations
  changed (hash in `minibots:cmdhash:<app>`).
- **Status**: `MiniBotSpec.activity` (default `/config • /about`).
- Every text is i18n (`minibots.*`, 5 languages). `/about` is localized in the
  32 command locales; `/config` reuses Moddy's command translation.

### Emojis (`emojis.py`)
Moddy's icons are **application emojis of the Moddy app**, and another
application cannot display them. Nothing has to be re-imported by hand:
- **At each start** (in the background), every emoji used in the code and the
  locales (about 380) is copied into the mini-bot's own application, **under
  the same name**, if it is missing. The images come from Discord's CDN.
  - An emoji replaced in Moddy (new id, same name, e.g. `/dev emoji_replace`)
    is re-copied: the source id of every copy is kept in `minibots:emojis:<app>`.
  - When nothing changed, this is a single API call.
  - Result logged on the lifecycle webhook (`emojis_synced`).
- **At send time** the payload builders are wrapped after
  `utils/icon_theme.py`, so the light-theme swap happens first and the
  mini-bot's copy of the `_dark` twin is used. Each Moddy id is swapped for the
  mini-bot's.
  - The id map is bound to the client's supervisor task through a ContextVar,
    so everything that client sends uses it.
  - Code running outside the client's task (control requests, periodic jobs)
    wraps its sends in `client.emoji_context()`.
  - Technical webhooks keep Moddy's ids (`emojis.suspend()`).
- **Limits**:
  - an application holds 2000 emojis;
  - the first start of a new mini-bot uploads every icon, which takes a few
    minutes; until then, a few icons can show as text.

## 5. Redis contract (`minibots:*`)

| Key / channel | Type | Content |
|---|---|---|
| `minibots:status` | hash | one field per app (JSON `AppStatus`: state, guilds, latency, restarts, last error) + `_process` (RSS, version, start). Rewritten every 30 s; older than 90 s = `silent`. |
| `minibots:apps` | hash | public registry per app (name, modules, `web_surfaces`, `application_id`) for the backend. |
| `minibots:control` | Pub/Sub | `release`, `reload_guild`, `restart`, `query` (read-only info: `guilds`, `mutual_guilds`, `guild_info`, plus `create_invite`, which `t.invite` already does on Moddy). Not signed: every request is idempotent and harmless. Anything else is ignored. |
| `minibots:ack:<request_id>` | list | the runtime's answer to a `release` or a `query` (`{app: result}`), TTL 60 s. |
| `minibots:emojis:<app>` | hash | emoji name → the Moddy emoji id it was copied from. |
| `minibots:cmdhash:<app>` | key | fingerprint of the last global command sync. |
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

### Logs: Moddy's webhooks, each card names its bot
- There is no separate category. A mini-bot writes to the **same** webhooks as
  Moddy: `guild_join`, `guild_remove`, `lifecycle`, `error`, `staff_command`,
  `database`, …
- Every card says which bot it comes from, **Moddy included**:
  - the footer reads ``bot `Bump Reminder` ``;
  - the webhook username becomes "Bump Reminder • Guild Join";
  - the avatar is that bot's.
- How it works: `TechLogger.for_app(bot, app, name)` gives each client its own
  logger, sharing one HTTP session.
- Where each event goes:
  - lifecycle (ready, reconnected, crashed, restarted, emojis synced) → `lifecycle`;
  - server joins/leaves → `guild_join` / `guild_remove`, same card as Moddy's;
  - errors → `error`, with an `App` line;
  - staff commands run on a mini-bot → `staff_command`;
  - transfers → `database`.

### Info commands on Moddy include every mini-bot
Moddy has neither the mini-bots' tokens nor their cache, so it asks the
mini-bots process (`queries.py`, through `minibots:control` → `query`, with an
8 s timeout):

| Command | What it adds |
|---|---|
| `t.mutualserver <user>` | one section per mini-bot sharing servers with the user (top role, key permissions) |
| `t.server <guild>` | when Moddy is not there: the server as a mini-bot sees it |
| `t.invite <guild>` | when Moddy is not there: an invite created by a mini-bot present |
| `d.servers [app]` | Moddy's list plus a count per mini-bot; `d.servers <app>` lists that mini-bot's servers (paginated, persistent) |
| `d.stats` | a "Mini-bots" field: state, servers, latency, process RAM |
| `t.server <guild>` (Moddy present) | the "Mini-bots" section: apps present, modules run by each |

Limits:
- Mini-bots keep no member cache, so `mutual_guilds` asks the gateway server by
  server (`query_members(user_ids=…)`, no privileged intent). It checks at most
  **100 servers per mini-bot**, biggest first, and says when the list is cut.
- If the process does not answer, the card says so and shows Moddy only.
- `t.user` still counts Moddy's shared servers only. Use `t.mutualserver` for
  the mini-bots.

### Action commands on a mini-bot: `@BumpReminder d.jsk …`
- The same router as Moddy, answering the bot's own mention. Same database,
  same staff roles, same denied commands.
- The audit log names the bot.
- Allowed commands (`minibots/staff.py::MINIBOT_STAFF_COMMANDS`):
  `d.jsk`, `d.sql`, `d.presence`, `d.stats`, `d.servers`, `t.server`, `t.invite`.
- Any other command answers "Moddy-only command — run it with @Moddy". It is
  not imported in the mini-bots process.
- Developers are `DEVELOPER_IDS` plus each mini-bot application's owner or team.
- `d.shutdown` and `d.reload` are deliberately left out: they would stop or
  reload the whole process, so every mini-bot at once.

### Control from Moddy
- `/dev minibots`: each app's state, servers, latency, restarts and last error,
  plus the process RAM and version.
- `/dev minibot <restart|reload> <app|*> [guild_id]`.
- There is no staff transfer command: the transfer is the admin's decision,
  and `t.config` opens the same panel for the team.
- Bump prospects: a server where our Bump Reminder mini-bot runs the module
  (`external`) is no longer a prospect.

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
3. Add the module's persistent views to `persistent_views`. `/config`,
   `/about`, the welcome DM, emojis and staff commands come automatically.
4. Write its listeners (`commands_module.setup(client)`): the module's events
   must be wired to `client.module_manager`. A periodic job sends inside
   `client.emoji_context()`.
5. Check which `bot.<service>` the module reads, and expose those on
   `MiniBotClient` (or in `SharedResources` if they are shared).
6. Override `on_transfer_in` / `on_transfer_out` if the module has Discord
   state beyond a panel.
7. Set `MINIBOT_..._TOKEN` on the **mini-bots** Railway service. On first
   start the emojis are copied and the commands published; nothing manual.

## 9. Deployment (Railway)

- A second service from the same repository: start command
  `python -m minibots.main`, same `DATABASE_URL` / `REDIS_URL`.
- It does **not** need `DISCORD_TOKEN`: `MODDY_SERVICE=minibots` is set by the
  entry point and lifts that check.
- Env vars:
  - `MINIBOT_<X>_TOKEN` (one per mini-bot);
  - optional: `MINIBOTS_ENABLED`, `MINIBOTS_DB_POOL_MAX` (3);
  - the same `LOG_WEBHOOK_*` as Moddy (cards are labelled per bot);
  - optional: `MODDY_INVITE_URL` (the "Get Moddy" link);
  - `MALLOC_ARENA_MAX=2`.
- Deploy order: Moddy first. It creates `app_guilds` and starts recording its
  own presence. Then the mini-bots service.
