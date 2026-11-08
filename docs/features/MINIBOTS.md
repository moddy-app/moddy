# Mini-bots

Mini-bots are small single-feature Discord applications (for example
"Bump Reminder") that run one of Moddy's modules under their own name. They
exist to bring in servers: a bot named after a single feature is easy to find
and to add. Once a server is in, it is offered to move to Moddy, where it gets
every option and the dashboard.

This document covers **the core** that runs and manages them; adding one is
described in §8, the first one, **Bump Reminder**, in §10, and **AltGuard** in
§11.

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
  - `members` and `presences` are refused by `MiniBotSpec`, with one
    exception listed in `INTENT_EXCEPTIONS`: `members` for **AltGuard**
    (`altguard_bot`) only. It brings the events (joins, departures, the
    member-list endpoints), never the cache, which stays off whatever the
    intents. Privileged: Developer Portal, justification at 100 servers.
    Without a cache, discord.py fires `on_raw_member_remove` but never
    `on_member_remove`. Any other bot asking for it is refused at import;
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
  - **One-module bot (Bump Reminder)**: `/config` *is* the module screen. There
    is no home screen, no "Back" to one, and no module name in the trail
    (`Config › Manage`). Any leftover "back to main" lands on the module.
  - How: the single module is a ContextVar set per client
    (`branding.standalone_module()`), read by `modules/configs/_common.py`
    (`breadcrumb`, `open_main_config`). A module home hides its own Back
    button with `standalone_module()`; this is done for Bump Reminder, and a
    future one-module bot does the same on its module's home.
  - **Language**: with no home screen, the module screen carries a **Language**
    button (`ServerLanguageButton`, persistent `moddy:config:language`). It
    opens the server settings, whose Back returns to the module.
  - The server language is one setting shared with Moddy, but each process
    caches it. A change made on one side refreshes the other:
    - from a mini-bot, a `settings_updated` on `moddy:bot`: Moddy drops its
      cache and re-posts its panels;
    - from Moddy (or a dashboard push), a `reload_guild` on `minibots:control`:
      the mini-bots drop theirs.
    - See `utils/guild_language.py::broadcast_language_change`.
- **`/about`**: what the bot does, "made by the Moddy team", server count, and
  links (add this bot, get Moddy, support, docs).
- **Welcome DM** to whoever added the bot (installer from the audit log,
  otherwise the owner), sent through `client.notifications` (rule #11).
  - Its source is the `moddy` service, so the attribution line reads
    "Sent by **Bump Reminder** ✓" once rebranded.
  - On a bot's very first start it waits for the emoji copy (up to 10 min),
    so the first message the admin reads is not raw `:emoji:` text.
- **Users never read the word "mini-bot"**: it is an internal name, kept to
  the staff screens. `test_users_never_read_the_word_minibot` guards the
  locales.
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
  - The id map fills **as the copy progresses**: what already exists is
    usable immediately, each uploaded emoji the second it is created.
  - One copy per **name**. When Moddy uses two ids under one name (an old and
    a new `warning`), the one defined in `utils/emojis.py` is copied, and both
    ids point at it.
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
    minutes. Until an icon is copied, a message using it shows it as text.
    The welcome DM waits for the copy; other messages in that window
    (unlikely so early) do not.

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
| `minibots:moddy` | Pub/Sub | to **Moddy**'s process: `release` (a mini-bot takes a module over) and `altguard_gate_opened` `{app, guild_id, user_id}` (§11: Moddy's Auto Role may hand out its roles; Moddy re-reads `altguard_members` first, so the unsigned message grants nothing the stored state does not allow). |

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
   An optional permission the module uses when it has it (AltGuard: *Manage
   Server* to list invites) goes in `extra_permissions`, so the add-to-server
   link asks for it. Never `administrator` (refused).
4. Write its listeners (`commands_module.setup(client)`): the module's events
   must be wired to `client.module_manager`. A periodic job sends inside
   `client.emoji_context()`.
5. Check which `bot.<service>` the module reads, and expose those on
   `MiniBotClient` (or in `SharedResources` if they are shared).
6. Override `on_transfer_in` / `on_transfer_out` if the module has Discord
   state beyond a panel.
7. Set `MINIBOT_..._TOKEN` on the **mini-bots** Railway service. On first
   start the emojis are copied and the commands published; nothing manual.

**No configuration in a DM.** A mini-bot's DMs (welcome, offers) carry no
channel or role select. A select lists 25 options at most, so on most servers
the channel or role the admin is looking for is missing, and a picker that
does not show it frustrates more than it helps. The DM says what the bot does
on its own and points to `/config`, whose channel and role selects search the
whole server. Link buttons (Get Moddy, Support) are fine.
`tests/test_minibots.py` guards it for Bump Reminder (welcome + offer);
AltGuard's welcome has no view at all.

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

## 10. Bump Reminder (`minibots/bots/bump_reminder.py`)

The first mini-bot: Moddy's `bump_reminder` module under its own name and
token (`MINIBOT_BUMP_REMINDER_TOKEN`, app id `bump_reminder_bot`). Same
detection, cards, `/config` panel and table as Moddy. What it adds is aimed at
**zero friction**:

| Moment | What happens |
|---|---|
| Added to a server | DM to whoever added it (audit log, else the owner). It lists the directories **already present** (one gateway lookup by ids, no member intent): "nothing to do, bump as usual", and points to `/config` for a role or the delay. **No channel picker** (rule in §8). If Moddy already runs the module there, it says so and stays out of the way. |
| First successful bump | No reminder for that directory yet → it **sets itself up in that channel**: directory cooldown, no role, `button` ping mode. It arms the reminder and says so in one `-#` line on the thank-you card. This happens once per (server, directory): marker `guilds.data.minibots.<app>.bump_autosetup.<directory>`, so a reminder the admin deletes stays deleted. A paused module, a failed bump (refusal), or a module Moddy runs → nothing. |
| A directory added later | `on_integration_create` (intent `integrations`, since the bot has no member events) → Moddy's offer (`services/bump_suggestion_service.py`) **without its picker** (`picker=False`, template `bump_reminder.suggest.<bot>.auto`): "nothing to do, bump as usual — I set myself up on the first bump; other choices in `/config`". It is deduplicated with Moddy's through the same Redis key, so a server with both bots gets one DM. |
| Every bump after | Exactly Moddy's behaviour: thank-you, "remind me" button, reminder at the end of the cooldown. |

Other points:
- **Texts**: the module's texts say "Moddy"; under a mini-bot they name the bot
  (`minibots/branding.py`: wraps `i18n.get` in that process only, keeps the
  `minibots.*` texts and "Moddy Team / Premium / Support").
- **`bump_reminders.app`**: every pending row belongs to the app that armed it,
  and each sweeper claims only its own (`claim_due_bumps(app=…)`). Otherwise
  Moddy's sweeper would claim a mini-bot's row, mark it sent and drop it.
  `BumpReminderModule.on_transfer_in` reassigns the rows when the module moves
  to Moddy, so a countdown in progress survives the transfer.
- **Moddy's offer** (`BumpSuggestionService`) is skipped when another app owns
  the module in that server.
- **Invite link** (`/about`): `bot + applications.commands`, permissions computed
  from the module (view, send, history, external emojis) + *View Audit Log*.
  Never Administrator.
- **Intents**: `guilds`, `guild_messages`, `message_content` (directory replies
  are other bots' messages), `integrations`. `message_content` is
  **privileged**: it needs to be justified to Discord once the bot passes
  100 servers ("reads the replies of bump bots to detect a successful bump").
- **Bump prospects** (team side): a server where this bot runs the module is no
  longer a prospect.

Setup checklist:
1. Create the application on the Developer Portal: name, avatar, description.
   Enable the **Message Content** intent.
2. Railway, mini-bots service: `MINIBOT_BUMP_REMINDER_TOKEN`.
3. Optional: set `application_id` in its `MiniBotSpec` if the bot user id ever
   differs from the application id. They are the same for every bot created
   since 2016.
4. Start: the emojis are copied, the commands published, and the bot shows in
   `/dev minibots`.

## 11. AltGuard (`minibots/bots/altguard.py`)

Moddy's `altguard` module under its own name and token
(`MINIBOT_ALTGUARD_TOKEN`, app id `altguard_bot`, accent `#2EB84A`). Same panel, consent modal, `/config` screen, tables and service
contract as on Moddy ([ALTGUARD.md](ALTGUARD.md)). What the file adds is the
wiring Moddy does in other places, for a client that caches no member:

| Moddy | AltGuard bot |
|---|---|
| `cogs/module_events.py` gates the join, the AltGuard cog reports it | `AltGuardBotCog.on_member_join`: gate first, then `altguard:membership` `active` |
| `on_member_remove` (member cache) | `on_raw_member_remove` (no cache; skipped when discord.py did hand a cached `Member`) |
| `bot._listen_pubsub` → `altguard:verdict` | the cog's own subscription, opened once the gateway is ready, reconnecting with backoff (5 → 60 s); each verdict in its own task |
| `bot.altguard` (HTTP client) | `client.altguard`, created in `setup`, closed with the cog |
| `/altguard verify|unverify` registered per guild where the module runs | global, guild-only commands (`kick_members` by default) |

- **Cog name `AltGuard`** (`class AltGuardBotCog(AltGuard, name="AltGuard")`):
  the module reaches the invite tracker and the membership resync through
  `get_cog("AltGuard")`.
- **Both processes hear every verdict.** Each acts only where it owns the
  module: the module lookup comes **before** the idempotency insert in
  `AltGuard.handle_verdict`, so the process that does not run the gate never
  records the verdict as seen.
- **Intents**: `guilds`, `guild_messages`, `members` (the only mini-bot
  allowed it, `INTENT_EXCEPTIONS`; privileged: joins,
  departures, the hourly full member list), `moderation` (bans), `invites`
  (invite baseline). No member cache, no chunking: the hourly resync pulls the
  list with `fetch_all_members(cache=False)` and drops it.
- **Invite link**: *Manage Roles* (gate roles + channel overwrites), the
  baseline, *View Audit Log* (kick vs leave, installer welcome) and *Manage
  Server* (`extra_permissions`, inviter attribution — skipped without it).
- **`/config`** is the module screen (no Back, no trail) with the **Language**
  button: the panel speaks the server language.
- **Icon**: `shield` on the bot (welcome DM, `/config` title,
  `altguard_config.config_icon`); Moddy keeps the `fingerprint`. The animated
  AltGuard face stays on the panel and the member's cards.
- **Welcome DM** (`build_welcome`): the three
  things the gate needs (channel,
  the two roles, the bot's role above them), then `/config`. Where Moddy
  already runs the gate, it says so and stays out of the way.

### Auto Role across the two processes

Auto Role stays a Moddy module. Without care, a server using both would hand
Moddy's auto roles to a member the AltGuard bot still holds back (role
overwrites add up: a member role allowing a channel beats the unverified
role's denial).
- **Hold back.** `AutoRoleModule._altguard_holds_back`: when Moddy has no
  AltGuard instance, the stored config is read; if another app owns it and it
  runs a gate (`modules/altguard.py::gate_configured`: channel + both roles,
  not paused), the member waits until `altguard_members` says `verified`.
  A lookup failure fails closed.
- **Hand off.** When the gate opens on the bot (verdict `passed`, `/altguard
  verify`), `AltGuardModule._run_auto_role` finds no local Auto Role and
  publishes `altguard_gate_opened` on `minibots:moddy`.
  `bot._handle_altguard_gate_opened` re-checks the database, fetches the
  member and calls `apply_roles`.

### Limits
- Moddy's staff commands (`/mod altguard verify|unverify|refusal`) only act on
  gates Moddy runs. On a gate the AltGuard bot runs, they answer "not
  configured". `t.config` opens the panel for the team.
- A member who passes while Moddy's process is down never gets the auto roles
  (Pub/Sub has no replay). `/altguard verify` again re-sends the hand-off.

Setup checklist:
1. Developer Portal: create the application (name **AltGuard**, avatar,
   description) and enable the **Server Members** intent.
2. Railway, mini-bots service: `MINIBOT_ALTGUARD_TOKEN`, plus the same
   `ALTGUARD_BOT_TOKEN` (and `ALTGUARD_API_URL` if not the default) as Moddy.
   Without `ALTGUARD_BOT_TOKEN` the consent modal answers "unavailable".
3. Start: the emojis are copied, `/config`, `/about` and `/altguard` published,
   and the bot shows in `/dev minibots`.

## Accent colour

Each mini-bot has its own accent colour, `MiniBotSpec.accent` (default Moddy
blue `#3661FF`). The **Bump Reminder** mini-bot uses **`#E8604C`**, **AltGuard**
**`#2EB84A`**. It colours:

- the bot's own cards: `/about` and the welcome DM (`client_accent(client)` in
  `minibots/builtin.py`);
- the cards its module posts: the bump thank-you, the reminder and its test
  (`card_accent(client)` in `utils/bump_views.py`, Moddy blue when Moddy posts);
  for AltGuard, the verification panel and the link card
  (`panel_accent(client)` in `utils/altguard_views.py`, `#5865F2` under Moddy).
  The log cards and the member DMs keep their verdict colours.

`/config` panels keep their state colours (active / paused / simulation), and
the Moddy-side activation offer stays Moddy blue.
