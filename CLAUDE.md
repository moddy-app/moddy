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
| i18n | JSON-based (French + English) |
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
│   ├── webhook.py             #   Webhook management
│   ├── youtube_websub.py      #   YouTube notifications
│   ├── interserver_commands.py #  Inter-server commands
│   ├── ping.py, user.py, avatar.py, banner.py, roll.py, moddy.py
│   ├── subscription.py        #   Premium features
│   ├── preferences.py         #   User preferences
│   ├── blacklist_check.py     #   Blacklist validation
│   ├── cases_user.py          #   User moderation cases view
│   ├── auto_restore_roles_commands.py
│   ├── cog_manager.py         #   Hot-reload / disable cogs
│   ├── console_logger.py      #   Console logging
│   ├── dev_logger.py          #   Dev logging
│   └── dev_tools.py           #   Developer tools
│
├── modules/                   # Server-level configurable features
│   ├── module_manager.py      #   ModuleManager + ModuleBase class
│   ├── welcome_channel.py     #   Welcome message in channel
│   ├── welcome_dm.py          #   Welcome DM to new members
│   ├── auto_role.py           #   Auto role assignment
│   ├── auto_restore_roles.py  #   Role restoration on rejoin
│   ├── starboard.py           #   Reaction-based starboard
│   ├── interserver.py         #   Inter-server message relay
│   ├── youtube_notifications.py
│   └── configs/               #   Components V2 config UIs per module
│
├── staff/                     # Staff/dev command system (prefix-based)
│   ├── base.py                #   Base classes for staff commands
│   ├── staff_manager.py       #   m. commands (rank, unrank, etc.)
│   ├── dev_commands.py        #   d. commands (reload, sql, stats, etc.)
│   ├── team_commands.py       #   t. commands (common staff)
│   ├── moderator_commands.py  #   mod. commands (blacklist, etc.)
│   ├── support_commands.py    #   sup. commands
│   ├── communication_commands.py  # com. commands
│   └── case_commands.py       #   Case management
│
├── db/                        # Database layer (repository pattern)
│   ├── base.py                #   ModdyDatabase core class
│   └── repositories/          #   Specialized repos
│       ├── users.py, guilds.py, staff.py, errors.py
│       ├── reminders.py, saved_messages.py, saved_roles.py
│       ├── moderation.py, interserver.py, attributes.py
│       └── _utils.py
│
├── utils/                     # Utility modules
│   ├── i18n.py                #   Internationalization system
│   ├── emojis.py              #   Emoji constants
│   ├── components_v2.py       #   V2 helper functions (create_error_message, etc.)
│   ├── staff_permissions.py   #   Permission system
│   ├── staff_logger.py        #   Staff action logging
│   ├── staff_role_permissions.py
│   ├── staff_help_view.py
│   ├── case_management_views.py
│   ├── moderation_cases.py
│   ├── embeds.py
│   ├── announcement_setup.py
│   └── incognito.py
│
├── services/                  # External service clients
│   ├── backend_client.py      #   Backend HTTP client
│   └── railway_diagnostic.py  #   Railway diagnostics
│
├── internal_api/              # FastAPI internal API
│   ├── server.py              #   FastAPI app + /health endpoint
│   ├── routes/                #   API route handlers
│   └── middleware/             #   Auth middleware
│
├── schemas/                   # Data schemas
├── locales/                   # i18n translation files
│   ├── fr.json                #   French (primary)
│   └── en-US.json             #   English
│
├── docs/                      # Documentation (see below)
└── tests/                     # Test files
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
- Translation files: `/locales/fr.json` and `/locales/en-US.json`

### 5. Title Format
- Titles in Components V2 must use: `### <:emoji:id> Title Text`
- Example: `### <:settings:1398729549323440208> Configuration`

### 6. Dynamic Values in Backticks
- Wrap all dynamic/user-specific data in backticks: `` f"**User:** `{user.id}`" ``

### 7. Error Handling
- For "unexpected" errors in cogs/modules: let the global error handler manage them
- For expected errors: use `create_error_message()` / `create_success_message()` from `utils/components_v2.py`

### 8. Persistent Views
- `BaseView` defaults to `timeout=None` — views never expire in memory
- To make a view survive a **bot restart**:
  1. Set `__persistent__ = True` on the class
  2. Give every interactive child a stable, namespaced `custom_id` (`moddy:<cog>:<view>:<action>`)
  3. Make `__init__` safely accept `bot=None` / default args so a "shell" can be instantiated
  4. Implement `register_persistent(cls, bot)` (usually `bot.add_view(cls())`)
  5. Add the class to `utils/persistent_views.py::_collect_persistent_view_classes()`
- Callbacks on persistent views must re-derive state from `interaction` (not `self`) — after a restart, `self` is the shell
- See → [docs/PERSISTENT_VIEWS.md](docs/PERSISTENT_VIEWS.md)

### 9. Language
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
| [docs/PERSISTENT_VIEWS.md](docs/PERSISTENT_VIEWS.md) | Any view that should survive a bot restart (most views) |
| [docs/ERROR_HANDLING.md](docs/ERROR_HANDLING.md) | Any code with Views, Modals, or error handling |
| [docs/EMOJIS.md](docs/EMOJIS.md) | When you need to use an emoji/icon |

### Feature-Specific
| Document | When to Read |
|---|---|
| [docs/COMMANDS.md](docs/COMMANDS.md) | Creating or modifying slash commands |
| [docs/MODULE_SYSTEM.md](docs/MODULE_SYSTEM.md) | Creating or modifying server modules |
| [docs/STAFF_SYSTEM.md](docs/STAFF_SYSTEM.md) | Staff/dev commands, permissions, roles |
| [docs/DATABASE.md](docs/DATABASE.md) | Database schema, queries, repository pattern |

### Infrastructure
| Document | When to Read |
|---|---|
| [docs/BACKEND-INTEGRATION.md](docs/BACKEND-INTEGRATION.md) | Bot ↔ Backend integration (Redis, Pub/Sub, Streams, `/status`) |
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

## Session Logs

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
