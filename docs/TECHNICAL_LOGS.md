# Technical Logs (internal staff)

Moddy emits **internal technical logs** for the staff team. These are distinct
from user-facing UI: they are dense, English-only, and meant to be scanned and
exploited quickly.

Key properties:

- **Webhook-based.** Logs are NOT sent by the bot through `channel.send()`. They
  are pushed through Discord **webhooks**, so they keep working even if the bot
  cannot post in a channel. Webhook URLs come from environment variables.
- **One channel per event type.** Each category has its own webhook URL, so each
  kind of event lands in its own channel.
- **Components V2.** Even though they go through webhooks, logs are rendered with
  Components V2 (`Container` + `TextDisplay`) and a coloured accent bar matching
  the event type. Booleans use the `done` / `undone` custom emojis.
- **Best-effort.** A logging failure never breaks a command or an event handler.

Implementation: [`utils/tech_logger.py`](../utils/tech_logger.py) — the
`TechLogger` class, attached to the bot as `bot.tech_logger`.

---

## Configuration (environment variables)

Each category reads its own webhook URL. Any category left unset falls back to
`LOG_WEBHOOK_DEFAULT`; if that is unset too, the category is silently muted.

| Category | Env var | What it logs |
|---|---|---|
| `guild_join` | `LOG_WEBHOOK_GUILD_JOIN` | Moddy added to a server |
| `guild_remove` | `LOG_WEBHOOK_GUILD_REMOVE` | Moddy removed from a server |
| `error` | `LOG_WEBHOOK_ERROR` | Errors (complements the existing error channel) |
| `lifecycle` | `LOG_WEBHOOK_LIFECYCLE` | Bot startup health report + shutdown |
| `staff_command` | `LOG_WEBHOOK_STAFF_COMMAND` | Staff command executions |
| `staff_action` | `LOG_WEBHOOK_STAFF_ACTION` | Staff actions (permission/role/attribute changes…) |
| `command` | `LOG_WEBHOOK_COMMAND` | Non-staff command usage (slash, context-menu, prefix) |
| `database` | `LOG_WEBHOOK_DATABASE` | Config changes / important DB writes |
| `security` | `LOG_WEBHOOK_SECURITY` | Sensitive events (blacklist blocks, blacklist attribute…) |
| _fallback_ | `LOG_WEBHOOK_DEFAULT` | Used for any category without a dedicated URL |

Mapping lives in [`config.py`](../config.py) (`LOG_WEBHOOK_ENV` → `LOG_WEBHOOKS`).

---

## Event coverage & wiring

| Event | Source | Method |
|---|---|---|
| Guild join | `bot.on_guild_join` | `log_guild_join` |
| Guild remove | `bot.on_guild_remove` | `log_guild_remove` |
| Blacklisted-owner join blocked | `bot.on_guild_join` | `log_security` |
| Startup health report | `bot.run_startup_checks` | `log_startup` |
| Shutdown | `bot.close` | `log_shutdown` |
| Errors (fatal + non-fatal) | `cogs/error_handler.send_error_log` | `log_error` |
| Staff commands | `staff/framework/cog._invoke` → `StaffLogger.log_command` | `log_staff_command` |
| Staff actions | `StaffLogger.log_action` | `log_staff_action` |
| Non-staff commands | `cogs/command_logger.py` | `log_command` |
| Attribute writes (blacklist, premium, official, verified…) | `db.set_attribute` hook | `log_attribute_change` |
| Config / module writes | `db._update_entity_data` hook | `log_data_change` |

### DB write hooks

`ModdyDatabase` exposes two optional async hooks, wired by the bot once the tech
logger is ready:

- `db.on_attribute_change(entity_type, entity_id, attribute, old, new, by, reason)`
- `db.on_data_change(table, entity_id, path, value)`

They are dispatched with `asyncio.create_task` so the webhook latency never
blocks the database write. `log_data_change` filters to guild `config` /
`modules` / `logging` paths to keep the feed signal-rich.

---

## Adding a new technical log

1. Add a method on `TechLogger` that builds a card via `self._card(...)` and
   calls `await self._dispatch("<category>", view)`.
2. If it is a new category, add it to `LOG_WEBHOOK_ENV` in `config.py`, plus an
   accent colour in `_ACCENTS` and a webhook name in `_USERNAMES`.
3. Call your method from the relevant event/command site, guarded by
   `getattr(self.bot, "tech_logger", None)` (or `bot.tech_logger`).
4. Keep it compact: bold labels, dynamic values in backticks, `done`/`undone`
   for booleans.
