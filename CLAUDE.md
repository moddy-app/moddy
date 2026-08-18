# CLAUDE.md — Moddy Bot AI Agent Guide

> This file is the primary entry point for AI agents (Claude Code, Copilot, etc.) working on the Moddy project.
> It provides a complete overview of the project architecture, conventions, and pointers to detailed documentation.

--- 

## Project Overview 
 
**Moddy** is a production-grade **Discord bot** for community moderation and utilities, built in Python and hosted on **Railway**.

| Key Info | Value |
|---|---|
| Language | Python 3.11+ |
| Framework | discord.py 2.6+ (with Components V2) |
| Database | PostgreSQL (asyncpg) |
| HTTP Server | FastAPI + Uvicorn (health checks + internal API) |
| Hosting | Railway (containerized) |
| i18n | JSON-based (French, English, Spanish, Portuguese, German) |
| Error Tracking | Centralized handler + database logging |
| License | CC BY-NC-SA 4.0 |

---

## Project Structure

```
moddy/
├── main.py                    # Entry point — launches bot + FastAPI server
├── bot.py                     # ModdyBot class — core bot logic and events
├── config.py                  # Configuration from Railway env vars
├── database.py                # Database backward-compat shim
│
├── cogs/                      # Discord slash commands and event handlers
│   ├── error_handler.py       #   Centralized error handler (38KB)
│   ├── config.py              #   /config command (module configuration)
│   ├── module_events.py       #   Event handlers for modules
│   ├── invite.py              #   /invite command
│   ├── emoji.py               #   Emoji management
│   ├── reminder.py            #   /reminder command
│   ├── saved_messages.py      #   Message bookmarking
│   ├── translate.py           #   /translate (DeepL)
│   ├── text_tools.py          #   /fix, /rephrase, /summarize (OpenAI, Modal V2 + context menus)
│   ├── voice_transcription.py #   "Transcribe" context menu (Groq Whisper)
│   ├── webhook.py             #   Webhook management
│   ├── social_notifications.py #  Social notifications dispatch + feeds service wiring
│   ├── altguard.py            #   AltGuard verdicts, membership events, /altguard verify|unverify
│   ├── interserver_commands.py #  Inter-server commands
│   ├── ping.py, user.py, avatar.py, banner.py, roll.py, moddy.py
│   ├── subscription.py        #   Premium features
│   ├── preferences.py         #   User preferences
│   ├── blacklist_check.py     #   Global suspension gate for prefix commands (cases system)
│   ├── cases_user.py          #   Personal cases browser (/mycases — all servers, filters, read-only)
│   ├── cases_server.py        #   Server cases browser (/cases — guild scope, mod actions, perms)
│   ├── case_sync.py           #   Auto-records guild sanctions as cases (audit log)
│   ├── auto_restore_roles_commands.py
│   ├── cog_manager.py         #   Hot-reload / disable cogs
│   ├── console_logger.py      #   Console logging
│   ├── command_logger.py      #   Non-staff command usage → technical webhook logs
│   ├── dev_logger.py          #   Dev logging
│   ├── dev_tools.py           #   Developer tools
│   └── subscription.py        #   /subscription command (user subscription status)
│
├── modules/                   # Server-level configurable features
│   ├── module_manager.py      #   ModuleManager + ModuleBase class
│   ├── welcome_channel.py     #   Welcome messages in channels (up to 5, Components V2)
│   ├── welcome_dm.py          #   Welcome DM to new members
│   ├── auto_role.py           #   Auto role assignment
│   ├── auto_restore_roles.py  #   Role restoration on rejoin
│   ├── starboard.py           #   Reaction-based starboard
│   ├── adaptive_slowmode.py   #   Adaptive slowmode (EWMA + hysteresis)
│   ├── interserver.py         #   Inter-server message relay
│   ├── social_notifications.py #  Social notifications (via moddy-feeds service)
│   ├── altguard.py            #   AltGuard anti multi-account verification gate
│   ├── automod_ai.py          #   Automod AI (applies decisions, cases+evidence, scalable features)
│   ├── bot_customization.py   #   Bot identity per guild (nick/avatar/banner/bio + name style)
│   ├── voice_transcription.py #   Voice message transcription (button or automatic)
│   └── configs/               #   Components V2 config UIs per module
│       ├── adaptive_slowmode_config.py
│       ├── altguard_config.py             # AltGuard gate (channel, roles, logs, language)
│       ├── social_notifications_config.py
│       ├── automod_ai_config.py
│       ├── automod_ai_precedents_view.py  # Learned-precedents browser (S7)
│       ├── welcome_channel_config.py      # Welcome messages list + add/manage (Modal V2)
│       ├── voice_transcription_config.py  # Voice transcription (status, mode, channels)
│       ├── bot_customization_config.py    # Bot customization (identity Modal V2 + name style)
│
├── automod/                   # Automod AI DETECTION pipeline (decides only; no side effects)
│   ├── engine.py              #   Shared per-bot orchestrator (funnel entry)
│   ├── relations.py / routing.py / precedents.py / bareme.py
│   ├── prefiltre.py / triviaux.py / blocklist.py / embeddings.py / nano.py
│   ├── relations.py           #   Relationship graph + target-reaction signals (familiarity)
│   ├── routing.py             #   Difficulty router (nano→mini) + heavy-sanction confirm (S6)
│   ├── precedents.py          #   Server precedents matcher (jurisprudence RAG, pure) (S7)
│   ├── normalize.py / injection.py / rules_check.py / schemas.py / constants.py
│   ├── bareme.py              #   Deterministic sanction scale (cran ladder + recidivism)
│   ├── cache.py               #   LRU+TTL score cache (embedding de-duplication)
│   ├── eval/                  #   Regression harness (golden set, offline runner, shadow annotations)
│   │   ├── golden.jsonl / fixtures.json / golden_baseline.json
│   │   ├── run.py             #     Offline runner (--replay/--live, CI gate on faux_positif_reel)
│   │   └── import_candidates.py #   annotated candidates → golden JSONL (make eval-import)
│   └── data/references.json   #   Embedding reference phrases (FR + EN)
│
├── staff/                     # Staff/dev command system (message + slash)
│   ├── base.py                #   StaffCommandsCog (auto-delete tracking)
│   ├── staff_commands.py      #   Entrypoint extension for the new framework
│   ├── framework/             #   Scalable dual message+slash engine (see docs)
│   │   ├── cog.py             #     Dispatcher: routing, perms, logging, incognito
│   │   ├── command.py         #     StaffCommand base + SlashOption + registry
│   │   ├── context.py         #     StaffContext (unifies message + slash)
│   │   ├── registry.py        #     Discovery + dynamic /dev,/team… group build
│   │   ├── design.py          #     Standardized Components V2 panels (accents)
│   │   └── parsing.py         #     Arg helpers (user/guild id)
│   ├── commands/dev/          #   /dev commands (one file each)
│   ├── commands/team/         #   /team commands (incl. help)
│   ├── commands/mod/          #   /mod commands + case/, global/ and altguard/ sub-groups
│   ├── commands/manage/       #   /manage commands (staff panel, badge, redirect/, banner/…)
│   ├── support_commands.py    #   sup. commands — legacy (not yet migrated)
│   └── communication_commands.py  # com. commands — legacy (not yet migrated)
│
├── db/                        # Database layer (repository pattern)
│   ├── base.py                #   ModdyDatabase core class
│   └── repositories/          #   Specialized repos
│       ├── users.py, guilds.py, staff.py, errors.py
│       ├── reminders.py, saved_messages.py, saved_roles.py
│       ├── moderation.py, interserver.py, attributes.py
│       ├── altguard.py          #   AltGuard verifications + gate state (altguard_*)
│       ├── appeals.py           #   Automod sanction appeals (case_appeals)
│       ├── enforcements.py      #   Global sanction appeal countdowns (case_enforcements)
│       ├── eval_candidates.py   #   Automod eval/annotation corpus (automod_eval_candidates)
│       ├── precedents.py        #   Automod server precedents (automod_precedents, RAG)
│       ├── token_alerts.py, token_secrets.py
│       ├── subscription.py    #   Subscription read-only queries (incl. is_guild_premium)
│       ├── social.py          #   Social notifications subscriptions
│       └── _utils.py
│
├── utils/                     # Utility modules
│   ├── i18n.py                #   Internationalization system
│   ├── command_translator.py  #   Slash command name/description localization (32 locales)
│   ├── emojis.py              #   Emoji constants
│   ├── components_v2.py       #   V2 helper functions (create_error_message, etc.)
│   ├── staff_permissions.py   #   Permission system
│   ├── subscription.py        #   Subscription helper (is_subscribed, is_guild_premium…)
│   ├── staff_logger.py        #   Staff action logging (also feeds technical webhook logs)
│   ├── tech_logger.py         #   Technical staff logs via webhooks (Components V2, per-event channels)
│   ├── staff_role_permissions.py
│   ├── staff_help_view.py
│   ├── case_management_views.py #  Cases Views/Modals (create, sanction, comment…) — staff
│   ├── cases_views.py         #   /cases & /mycases browser (CasesBrowserView, persistent)
│   ├── altguard_views.py      #   AltGuard panel (persistent), consent Modal V2, link + log cards
│   ├── automod_shadow_views.py #  Automod shadow-mode (dry_run) SIMULATION card + annotation buttons (persistent)
│   ├── automod_render.py      #   Shared automod card helpers (barème breakdown, sanction name/accent)
│   ├── transcription_views.py #   Voice transcription cards + persistent Transcribe button
│   ├── appeal_views.py        #   Automod appeal UI (DM buttons + reviewer panels, persistent)
│   ├── moderation_cases.py    #   Cases domain model + enums + reference gen
│   ├── global_sanctions.py    #   Global (Moddy-team) sanction levels: warn/limited/suspended
│   ├── global_sanction_views.py #  Global sanction UI (notice DMs, staff panels, Modals V2)
│   ├── embeds.py
│   ├── announcement_setup.py
│   └── incognito.py
│
├── gateway/                   # Centralized API gateway (ALL external API calls go here)
│   ├── __init__.py            #   Gateway class (bot.gateway) — public surface
│   ├── config.py              #   GatewayConfig (from env vars)
│   ├── errors.py              #   Typed error hierarchy
│   ├── spec.py                #   CallSpec, QuotaTarget, QuotaScope
│   ├── quota.py               #   QuotaManager (Redis daily counters + PG limits)
│   ├── ratelimit.py           #   RateLimiter (provider-account windows: rpm, audio sec/h…)
│   ├── resilience.py          #   CircuitBreaker + retry/backoff
│   ├── logger.py              #   Buffered PG logging + staff webhook per call
│   ├── executor.py            #   GatewayExecutor (single execution path)
│   ├── adapters/              #   Provider adapters
│   │   ├── openai.py          #     embed + chat
│   │   ├── deepl.py           #     translate
│   │   └── groq.py            #     transcribe (whisper-large-v3-turbo)
│   └── clients/               #   High-level clients
│       ├── ai.py              #     bot.gateway.ai
│       ├── translation.py     #     bot.gateway.translation
│       └── transcription.py   #     bot.gateway.transcription
│
├── services/                  # External service clients
│   ├── altguard_client.py     #   AltGuard service client (HTTP + altguard:* Pub/Sub)
│   ├── backend_client.py      #   Backend HTTP client
│   ├── feeds_client.py        #   moddy-feeds Redis client (social notifications)
│   ├── case_service.py        #   Scalable sanction→case entry point (source registry)
│   ├── global_sanction_service.py # Global sanctions: grouped cases, notice DM, 48h countdown, Redis
│   ├── appeal_service.py      #   Automod sanction appeals (server / Moddy team, binding)
│   ├── precedent_service.py   #   Automod server precedents (record + serve, RAG)
│   ├── transcription_service.py #  Voice/audio speech-to-text (shared by cog + module)
│   └── railway_diagnostic.py  #   Railway diagnostics
│
├── internal_api/              # FastAPI internal API
│   ├── server.py              #   FastAPI app + /health, /ping, /status + router wiring
│   ├── routes/                #   API route handlers
│   │   └── automod.py         #     POST /automod/rules_check (indications safety check)
│   └── middleware/             #   Auth middleware
│
├── schemas/                   # Data schemas
├── locales/                   # i18n translation files
│   ├── fr.json                #   French (primary)
│   ├── en-US.json             #   English
│   └── commands/              #   Slash command names + descriptions, one file per
│                              #   Discord locale (fr.json, de.json, ja.json… 32 total)
│
├── docs/                      # Documentation (see below)
└── tests/                     # Test files
    ├── automod/               #   pytest suite for the pure-Python detection core
    │                          #   (`pip install -r requirements-dev.txt && pytest`)
    ├── internal_api/          #   pytest suite for the internal API routes
    │                          #   (FastAPI TestClient, bot + gateway stubbed)
    ├── gateway/               #   Provider rate limits + executor reservation lifecycle
    ├── test_altguard.py       #   AltGuard verdicts, gate roles, auto_role hold-back
    ├── test_global_sanctions.py   # Global sanction levels, cache TTL, user/guild context
    ├── test_global_sanction_flow.py # Groups, notices, countdown, Redis events, allowlists
    ├── test_bot_customization.py  # Bot customization validation (bio budget, styles)
    └── test_transcription.py  #   Voice transcription helpers, guard rails, cards
```

---

## Startup Flow

1. `main.py` → `asyncio.run(main())`
2. Sets up logging, starts `ServiceManager`
3. Creates `ModdyBot()` instance
4. Launches **FastAPI server** and **Discord bot** concurrently via `asyncio.gather()`
5. `bot.setup_hook()` → connects DB, loads cogs, syncs global commands, loads modules
6. `bot.on_ready()` → syncs guild-only commands per server, starts scheduled tasks

---

## Mandatory Rules for Writing Code

### 1. Components V2 Only
- **ALWAYS** use `ui.Container()` + `ui.TextDisplay()` — **NEVER** use `discord.Embed()`
- Use `ui.LayoutView` or `BaseView` (which extends it)
- See → [docs/COMPONENTS_V2.md](docs/COMPONENTS_V2.md)

### 2. BaseView / BaseModal Required
- **ALL** Views must inherit from `BaseView`
- **ALL** Modals must inherit from `BaseModal`
- These ensure errors are caught and routed to the centralized error handler
- See → [docs/ERROR_HANDLING.md](docs/ERROR_HANDLING.md)

### 3. Custom Emojis Only
- **NEVER** use default Unicode emojis (except country flags)
- Use custom emojis from `/utils/emojis.py`
- Full list → [docs/EMOJIS.md](docs/EMOJIS.md)

### 4. Internationalization (i18n)
- **ALL** user-facing text must use the i18n system
- `from utils.i18n import t` → `t('key.path', locale=locale)`
- Translation files: `/locales/fr.json`, `/locales/en-US.json`, `/locales/es-ES.json`,
  `/locales/pt-BR.json`, `/locales/de.json`
- **Command names and descriptions** are localized separately (Discord shows them
  in the user's own language): declare the command in English in the cog, then add
  its key to every `/locales/commands/<locale>.json`
- See → [docs/COMMAND_LOCALIZATION.md](docs/COMMAND_LOCALIZATION.md)

### 5. Title Format
- Titles in Components V2 must use: `### <:emoji:id> Title Text`
- Example: `### <:settings:1519800032499339354> Configuration`

### 6. Dynamic Values in Backticks
- Wrap all dynamic/user-specific data in backticks: `` f"**User:** `{user.id}`" ``

### 7. Verification Badge on Usernames
- **Whenever a command displays a username or display name (outside of mentions), it must show the verification badge** using `get_user_verification_badge()` from `utils/emojis.py`.
- Three tiers (priority order):
  1. `VERIFIED_ORG` attribute → `<:verified_org:...>` badge
  2. Discord staff flag / `TEAM` attribute / `VERIFIED_ORG_MEMBER` attribute → `<:verified:...>` badge + `-# affiliation notice`
  3. `VERIFIED` attribute → `<:verified:...>` badge
- The badge is wrapped as a **hyperlink** using `format_verification_badge(badge)` from `utils/emojis.py`, which produces `[<:verified:...>](https://docs.moddy.app/articles/verified-badges)`.
- The formatted badge is appended **after the bold name** — no space between name and badge: `**{name}**{badge}`.
- Pass `name=` and `badge=` as **separate** i18n parameters (not combined into one).
- Use `global_name` (display name) instead of `username` wherever possible.
- Fetch `moddy_attributes` from `bot.db.get_user()` before building the view.
- Do **not** show any badge if the user has none — empty string.
- `get_user_verification_badge()` now returns a 3-tuple `(badge_emoji, org_names, tier)`. Unpack all three.
- See `utils/emojis.py::get_user_verification_badge()` and `format_verification_badge()` for the implementation.

### 8. Error Handling
- For "unexpected" errors in cogs/modules: let the global error handler manage them
- For expected errors: use `create_error_message()` / `create_success_message()` from `utils/components_v2.py`

### 8. Persistent Views — **MANDATORY, NO EXCEPTIONS**
- **You MUST use persistent views for EVERY interactive Discord component,
  in every cog, module, and staff command — always, without exception.**
  Every button, select, and `DynamicItem` MUST have a stable, namespaced
  `custom_id` and live on a `timeout=None` view so it never dies — neither
  after a timeout nor after a bot restart. This is not a per-feature
  judgment call: **writing a new interactive view that is not persistent is
  a bug**, exactly like using `discord.Embed()` instead of Components V2 or
  hardcoding a user-facing string instead of using i18n. Shipping a view
  whose buttons stop working after a restart (or after a few minutes of
  inactivity) is **not acceptable** and will be rejected in review.
- The **only** acceptable exceptions are the ones explicitly listed and
  justified in [docs/PERSISTENT_VIEWS.md → "Deliberate exclusions"](docs/PERSISTENT_VIEWS.md)
  (modals, error-recovery views, secret-displaying views, in-memory-callback
  confirm dialogs, and similar). If you believe a new view needs to be
  excluded, add it to that section with the same level of concrete
  justification as the existing entries — do not silently skip persistence.
- `BaseView` defaults to `timeout=None` — views never expire in memory.
- To make a view survive a **bot restart**:
  1. Set `__persistent__ = True` on the class
  2. Give every interactive child a stable, namespaced `custom_id` (`moddy:<cog>:<view>:<action>`)
  3. Make `__init__` safely accept `bot=None` / default args so a "shell" can be instantiated
  4. For owner-scoped, guild-mismatched, or entity-scoped state (a `user_id`,
     a `case_id`, a page number, …), use a `DynamicItem` subclass with a
     `template=` regex — a static custom_id alone is only enough when
     `interaction.guild_id`/`interaction.user.id` already provide the auth
     context (typical guild config panels)
  5. Implement `register_persistent(cls, bot)` (`bot.add_view(cls())` for a
     regular view, `bot.add_dynamic_items(ItemClass)` for a `DynamicItem`)
  6. Add the class to `utils/persistent_views.py::_collect_persistent_view_classes()`
- Callbacks on persistent views must re-derive state from `interaction` (not
  `self`) — after a restart, `self` is the shell, and a `DynamicItem`
  reconstructs from scratch on **every single click**, not just after a
  restart.
- Before opening a PR that adds or touches a view, run
  `python3 -m pytest tests/test_persistent_views.py -q` — it asserts every
  registered view is actually persistent and that no two views collide on a
  `custom_id`. A view is not done until this suite covers it.
- See → [docs/PERSISTENT_VIEWS.md](docs/PERSISTENT_VIEWS.md) for the full
  contract, the custom_id convention, auth models, and worked examples.

### 9. "Current value" only when nothing else shows it
- Do **not** print a `Valeur actuelle : …` / `Current value: …` line under a
  setting whose control already displays its own state. A `ChannelSelect`,
  `RoleSelect`, `UserSelect`, `MentionableSelect` or a `Select` with
  `default=True` on the chosen option all render the current selection
  themselves — repeating it as text is duplicated, and the two can drift.
- **Do** show the current value when the setting is edited through something
  that displays nothing: a Modal (custom message, colour, thresholds…), a
  toggle button, or a value stored but not represented by any component on
  the panel.
- Rule of thumb: if the user can read the answer off the control, the text
  line is noise; if the control is a button that opens a Modal, the text line
  is the only way to know what is stored.

### 10. Language
- Code comments, commits, PRs: **English only**
- User-facing strings: via i18n (French + English)

---

## Documentation Index

All documentation is in [docs/](docs/). Read the relevant file **before** working on a feature.

### Core References (read these first when relevant)
| Document | When to Read |
|---|---|
| [docs/DESIGN.md](docs/DESIGN.md) | Any UI/UX work, configuration panels, interactive components |
| [docs/COMPONENTS_V2.md](docs/COMPONENTS_V2.md) | Any code that creates Discord UI elements |
| [docs/MODALS_V2.md](docs/MODALS_V2.md) | Any code that builds a Modal (Label / TextDisplay / Select in modals) |
| [docs/PERSISTENT_VIEWS.md](docs/PERSISTENT_VIEWS.md) | Any view that should survive a bot restart (most views) |
| [docs/ERROR_HANDLING.md](docs/ERROR_HANDLING.md) | Any code with Views, Modals, or error handling |
| [docs/EMOJIS.md](docs/EMOJIS.md) | When you need to use an emoji/icon |

### Feature-Specific
| Document | When to Read |
|---|---|
| [docs/COMMANDS.md](docs/COMMANDS.md) | Creating or modifying slash commands |
| [docs/COMMAND_LOCALIZATION.md](docs/COMMAND_LOCALIZATION.md) | Translating slash command names/descriptions (32 Discord locales) |
| [docs/TEXT_TOOLS.md](docs/TEXT_TOOLS.md) | AI text tools — `/fix`, `/rephrase`, `/summarize` (models, presets, mention stripping) |
| [docs/VOICE_TRANSCRIPTION.md](docs/VOICE_TRANSCRIPTION.md) | Voice transcription — context menu, module, Groq Whisper, cost control |
| [docs/MODULE_SYSTEM.md](docs/MODULE_SYSTEM.md) | Creating or modifying server modules |
| [docs/WELCOME_MESSAGES.md](docs/WELCOME_MESSAGES.md) | Welcome messages module — config schema, placeholders, backend/dashboard contract |
| [docs/ALTGUARD.md](docs/ALTGUARD.md) | **AltGuard** — anti multi-account verification gate, consent, service contract, staff commands |
| [docs/ALTGUARD_INTEGRATION.md](docs/ALTGUARD_INTEGRATION.md) | AltGuard ↔ bot exact wire contract — payload types, error codes, debugging |
| [docs/AUTOMOD_AI.md](docs/AUTOMOD_AI.md) | Automod AI — detection pipeline, nano decider, scalable features, rules safety check |
| [docs/AUTOMOD_AI_CONFIG.md](docs/AUTOMOD_AI_CONFIG.md) | Automod AI configuration schema in DB (backend / dashboard integration) |
| [docs/BOT_CUSTOMIZATION.md](docs/BOT_CUSTOMIZATION.md) | Bot Customization — per-guild nickname/avatar/banner/bio + name styles, Redis dashboard contract |
| [docs/PREMIUM.md](docs/PREMIUM.md) | **Premium gating** — how to check whether a server (or a user) is premium |
| [docs/STAFF_SYSTEM.md](docs/STAFF_SYSTEM.md) | Staff/dev commands, permissions, roles |
| [docs/MODERATION_CASES.md](docs/MODERATION_CASES.md) | Moderation cases/sanctions, the case service & sources, auto-sync |
| [docs/GLOBAL_SANCTIONS.md](docs/GLOBAL_SANCTIONS.md) | **Global sanctions** — Moddy-team warn / limited / suspended, on users *and* servers |
| [docs/TECHNICAL_LOGS.md](docs/TECHNICAL_LOGS.md) | Internal technical staff logs (webhook-based, per-event channels) |
| [docs/DATABASE.md](docs/DATABASE.md) | Database schema, queries, repository pattern |

### Infrastructure
| Document | When to Read |
|---|---|
| [docs/API_GATEWAY.md](docs/API_GATEWAY.md) | API Gateway — all external API calls (OpenAI, DeepL, Groq), quotas, provider rate limits, resilience, logging |
| [docs/BACKEND-INTEGRATION.md](docs/BACKEND-INTEGRATION.md) | Bot ↔ Backend integration (Redis, Pub/Sub, Streams, `/status`) |
| [docs/REDIS_COMMUNICATION.md](docs/REDIS_COMMUNICATION.md) | **Redis inter-service communication** — Pub/Sub vs Streams vs plain keys, current channel/stream inventory, checklist for wiring up a new Redis-based service |
| [docs/SOCIAL_NOTIFICATIONS.md](docs/SOCIAL_NOTIFICATIONS.md) | Social Notifications module + `moddy-feeds` Redis contract (what the backend must mirror) |
| [docs/SOCIAL_NOTIFICATIONS_CHANGES_2026-06-14.md](docs/SOCIAL_NOTIFICATIONS_CHANGES_2026-06-14.md) | Backend/dashboard change spec: customizable message columns, quota, error codes, task fields |
| [docs/SUBSCRIPTION_SCHEMA.md](docs/SUBSCRIPTION_SCHEMA.md) | Subscription DB schema, Redis cache contract, Pub/Sub events |
| [docs/RAILWAY.md](docs/RAILWAY.md) | Environment variables, deployment, troubleshooting |

### Other
| Document | When to Read |
|---|---|
| [docs/AGENTS.md](docs/AGENTS.md) | Agent system documentation |

### Session Logs
| Directory | Purpose |
|---|---|
| [docs/sessions/](docs/sessions/) | AI agent session summaries — after each work session, write a summary here |

---

## Key Design Patterns

### Repository Pattern (Database)
Database access goes through specialized repositories in `db/repositories/`:
```python
# Access via bot.db.<repository>.<method>()
await bot.db.users.get_user(user_id)
await bot.db.guilds.get_guild(guild_id)
await bot.db.staff.get_permissions(user_id)
```

### Module System
Server features are implemented as modules in `modules/`:
- Each module extends `ModuleBase`
- Config is stored as JSONB in `guilds.data.modules.<module_id>`
- Config UI lives in `modules/configs/`
- See → [docs/MODULE_SYSTEM.md](docs/MODULE_SYSTEM.md)

### Staff Command System
Prefix-based commands triggered by mentioning the bot:
- `@Moddy t.help` → team commands
- `@Moddy d.reload cog_name` → dev commands
- `@Moddy m.rank @user Manager` → management commands
- `@Moddy mod.blacklist @user reason` → moderation commands

### Command Sync (Global vs Guild-Only)
- **Global commands** (e.g., `/ping`, `/user`): available everywhere including DMs
  - Must have `@allowed_installs(guilds=True, users=True)` and `@allowed_contexts(guilds=True, dms=True, private_channels=True)`
- **Guild-only commands** (e.g., `/config`): only in servers where Moddy is installed
  - Must have `@app_commands.guild_only()`
- Sync happens in 2 phases: `setup_hook()` (global) → `on_ready()` (per-guild)

### Colors
```python
from config import COLORS
# COLORS["primary"]   = 0x5865F2 (Discord Blue)
# COLORS["success"]   = 0x57F287 (Green)
# COLORS["warning"]   = 0xFEE75C (Yellow)
# COLORS["error"]     = 0xED4245 (Red)
# COLORS["info"]      = 0x5865F2 (Blue)
# COLORS["neutral"]   = 0x99AAB5 (Gray)
# COLORS["developer"] = 0x9B59B6 (Purple)
```

### Environment Modes
```python
from config import IS_DEV, IS_PROD, IS_MAINTENANCE, ENV_MODE
# "production" — normal operation
# "development" — restricted to DEV_ALLOWED_IDS
# "maintenance" — bot in maintenance mode
```

---

## Quick Reference: Creating a New Feature

### New Slash Command
1. Read [docs/COMMANDS.md](docs/COMMANDS.md)
2. Create cog in `cogs/`
3. Use `BaseView` for any UI
4. Use i18n for all text
5. Use custom emojis only
6. Add the command name/description to `/locales/commands/*.json`
   (see [docs/COMMAND_LOCALIZATION.md](docs/COMMAND_LOCALIZATION.md))

### New Server Module
1. Read [docs/MODULE_SYSTEM.md](docs/MODULE_SYSTEM.md)
2. Create module in `modules/` extending `ModuleBase`
3. Create config UI in `modules/configs/`
4. Add i18n keys in `locales/`
5. Register in module manager

### New Staff Command
1. Read [docs/STAFF_SYSTEM.md](docs/STAFF_SYSTEM.md)
2. Add command in the appropriate `staff/` file
3. Use the permission decorators

---

## Keeping Documentation Up to Date

**This is a standing rule — do it proactively, without being asked.**

Whenever you make changes to the codebase, check whether the documentation needs updating:

- **This file (`CLAUDE.md`)**: If you add/remove/rename cogs, modules, directories, or change architecture, update the project structure and relevant sections here.
- **Feature docs** (`docs/*.md`): If you modify a feature covered by a doc (e.g., adding a new module, changing the command sync system, adding a DB table), update the corresponding doc.
- **Emojis** (`docs/EMOJIS.md` and `utils/emojis.py`): If new custom emojis are added, update the list.
- **Session logs** (`docs/sessions/`): After each work session, create a summary (see below).

If you create a new system or feature that doesn't fit any existing doc, create a new doc file in `docs/` and add it to the Documentation Index above.

**Do not wait for the user to ask.** Outdated documentation is worse than no documentation.

---

## Session Logs :

After each work session, create a summary in `docs/sessions/` with the format:
```
docs/sessions/YYYY-MM-DD_short-description.md
```

Include:
- What was done
- Files modified
- Decisions made and why
- Any known issues or follow-ups

See → [docs/sessions/README.md](docs/sessions/README.md)
