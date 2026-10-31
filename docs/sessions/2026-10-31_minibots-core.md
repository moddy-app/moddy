# 2026-10-31 — Mini-bots core

## What was done
The core that runs and manages **mini-bots**: small single-feature Discord apps
(e.g. "Bump Reminder") that share Moddy's modules and database and funnel
servers to Moddy. No concrete mini-bot yet — `minibots/spec.py::SPECS` is empty.

- **Ownership in the shared config**: `guilds.data.modules.<id>.app` (absent =
  Moddy, no migration). `ModuleManager(bot, app=…)` is shared by Moddy and the
  mini-bots and ignores configs it does not own (load, defaults, save, delete,
  state, dashboard push). New status `external`.
- **Atomic JSON writes**: `_update_entity_data` now locks the row
  (`SELECT … FOR UPDATE`) — two processes write `guilds` now.
- **`app_guilds`** presence table, written by every app incl. Moddy.
- **Runtime** (`minibots/`): one process, N lean `discord.Client`s
  (`Intents.none()` + spec intents, no message/member cache, no chunking, lazy
  module instances), one small DB pool, supervision with backoff, Redis
  heartbeat, control channel, single periodic-job scheduler.
- **Manual transfer to Moddy** (`services/module_transfer.py`,
  `utils/minibot_transfer.py`): DM offer to the installer when Moddy joins a
  server using a mini-bot, banner + "Managed by X" screen in `/config`, one
  persistent button. The mini-bot takes its own panel down first (release +
  ack), Moddy re-posts it (`on_transfer_in`).
- **Staff**: `/dev minibots`, `/dev minibot restart|reload`, mini-bots section
  in `/team server`. **Logs**: category `minibots` (`LOG_WEBHOOK_MINIBOTS`),
  errors tagged with the app. Metric `minibots.transfer`.
- **No dashboard for mini-bots** except the functional pages
  (`WEB_SURFACES`: AltGuard verification, ticket transcripts).

## Decisions
- **Manual handoff** (Jules): a config stays with its owner until an admin
  clicks "Transfer to Moddy". Moddy proposes it on join.
- Release before the ownership write: a panel's buttons only reach the app that
  posted it, so the old owner must remove it; Moddy cleans up itself
  (best-effort) if the mini-bots process does not ack within 12 s.
- Ownership write = compare-and-swap on the `app` key only; the settings never
  move.
- Transfers move every module of one app in one server (mini-bots are
  single-feature).
- No staff transfer command: the transfer is the admin's decision.
- The runtime does not listen to `moddy:bot` (no dashboard for mini-bots).
- `config.py` no longer requires `DISCORD_TOKEN` when
  `MODDY_SERVICE=minibots`.
- Bump prospects: a server running our mini-bot is no longer a prospect.

## Files
New: `minibots/*`, `db/repositories/app_guilds.py`,
`services/module_transfer.py`, `utils/minibot_transfer.py`,
`staff/commands/dev/minibots.py`, `staff/commands/dev/minibot.py`,
`tests/test_minibots.py`, `docs/features/MINIBOTS.md`.
Modified: `modules/module_manager.py`, `db/base.py`, `bot.py`,
`cogs/config.py`, `modules/configs/_common.py`, `utils/persistent_views.py`,
`utils/tech_logger.py`, `config.py`, `stats/registry.py`,
`services/bump_prospect_service.py`, `staff/commands/team/server.py`,
`locales/*.json`, docs (CLAUDE.md, REDIS_COMMUNICATION, DATABASE, RAILWAY,
TECHNICAL_LOGS, MODULE_SYSTEM).

## Verification
- `tests/test_minibots.py`: 50 tests (ownership, manager, transfer incl.
  fallback/lock/idempotence, UI, runtime supervision, Redis contract, i18n,
  light imports).
- SQL checked on a local PostgreSQL 16: 20 concurrent writes with no lost
  update, ownership CAS, presence reconcile.
- `python -m minibots.main` without tokens starts ("0 mini-bot(s)") and stops
  cleanly on SIGINT.
- Full suite: all green except 2 failures that already fail on the base
  branch (`test_automod_images::test_safesearch_respects_pacing`,
  `test_translate_button::TestDefaultEnabled`).

## Follow-ups
- Build the first mini-bot (Bump Reminder): spec, its own commands, the
  services its module reads on `bot`.
- Tickets: override `on_transfer_in` to re-post the control messages of open
  tickets (their buttons stay with the mini-bot otherwise).
- Mini-bot side of the offer: tell the server when Moddy arrives.
- Backend/dashboard: refuse saves on modules whose `app` is not `moddy`, show
  "Managed by X"; route AltGuard verification / transcripts for mini-bot apps.
- Not tested against a live Discord gateway (needs a mini-bot token).
