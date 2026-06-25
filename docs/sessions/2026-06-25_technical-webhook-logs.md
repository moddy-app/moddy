# Session — Internal technical logs (webhook-based)

**Date:** 2026-06-25

## What was done

Added a complete **internal technical logging system** for the staff team,
sent through Discord **webhooks** (one channel per event type), rendered with
Components V2 and coloured accent bars. Logs are English-only, compact and
information-dense; booleans use the `done` / `undone` emojis.

### New central logger
- `utils/tech_logger.py` — `TechLogger` (attached as `bot.tech_logger`):
  - Webhook dispatch with a shared `aiohttp` session, per-category URL +
    `LOG_WEBHOOK_DEFAULT` fallback, best-effort (never raises).
  - Standardized `_card()` builder (Container + accent + compact body + footer
    with timestamp / env / shard).
  - Methods: `log_guild_join`, `log_guild_remove`, `log_startup`,
    `log_shutdown`, `log_error`, `log_staff_command`, `log_staff_action`,
    `log_command`, `log_attribute_change`, `log_data_change`, `log_security`.

### Configuration
- `config.py` — `LOG_WEBHOOK_ENV` (category → env var), resolved `LOG_WEBHOOKS`,
  and `LOG_WEBHOOK_DEFAULT`.

### Wiring
- `bot.py`: init in `setup_hook`; wires `db.on_attribute_change` /
  `db.on_data_change`; logs guild join/remove, blacklisted-owner join block
  (security), startup health report (`run_startup_checks`), and shutdown
  (`close`).
- `cogs/error_handler.py`: `send_error_log` also pushes to the error webhook.
- `utils/staff_logger.py`: `log_command` / `log_action` also push to the
  staff_command / staff_action webhooks.
- `staff/framework/cog.py`: pass `target_server=ctx.guild` for richer logs.
- `cogs/command_logger.py` (new): logs **non-staff** command usage
  (`on_app_command_completion` + `on_command_completion`), skipping staff roots.
- `db/base.py` + `db/repositories/attributes.py`: optional async write hooks
  dispatched via `asyncio.create_task` so webhook latency never blocks writes.

### Docs
- `docs/TECHNICAL_LOGS.md` (new) + `CLAUDE.md` (structure + index).

## Decisions
- **Webhooks over channel.send**: per the request; resilient to channel perms.
- **Central DB hooks** rather than touching every call site — attribute changes
  cover the highest-signal "important writes" (blacklist/premium/official/…),
  and `_update_entity_data` covers config/module writes (filtered).
- **Staff logs routed through existing `StaffLogger`** so all staff commands and
  actions are captured in one place without duplicating call sites.
- Sensitive attributes (`BLACKLISTED`) routed to the `security` feed with a red
  accent.

## Follow-ups / notes
- Webhook URLs must be set on Railway (`LOG_WEBHOOK_*`); unset categories fall
  back to `LOG_WEBHOOK_DEFAULT` or stay muted.
- `log_command` only fires on success (app-command failures are already covered
  by the error feed via `ErrorTracker`).
