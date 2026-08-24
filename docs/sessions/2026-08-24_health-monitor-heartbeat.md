# Session: Moddy Health Monitor heartbeat integration

**Date:** 2026-08-24
**Agent:** Claude Code

## Summary

Wired `moddy-bot` onto the `moddy-health-monitor` dead man's switch: the bot
now pushes its own state every 20s instead of the monitor ever polling it.
Isolated addition — nothing about existing bot behavior changes.

What shipped:

- `services/heartbeat.py` — `HeartbeatClient`: fire-and-forget background
  task (`asyncio.create_task`), 5s per-request timeout, 20s interval. A
  failed push only logs; it never blocks, never retries aggressively, never
  keeps the bot from starting or shutting down. Uses `aiohttp` (already a
  dependency) rather than `httpx`, to match the rest of the codebase
  (`services/altguard_client.py` and friends).
- `bot.py`: `self.heartbeat` constructed in `__init__`, version set once
  `fetch_version()` resolves in `setup_hook()`, started in `on_ready()`
  (after `run_startup_checks()` — a bot never ready has nothing to report),
  stopped in `close()`.
- `ModdyBot._build_heartbeat_checks()` — the bot decides its own status:
  `is_ready`, `discord_gateway` (latency, `None` while `bot.latency` is
  `nan`), `shards` (bot is not sharded here, so always `1/1` once ready).
  Not ready -> `down`; a shard down -> `degraded`; else `ok`.
- `config.py`: `HM_URL` / `HM_INGEST_TOKEN`, both optional — either missing
  disables the heartbeat with a `logger.warning`, never a crash.
- `docs/HEALTH_MONITOR.md` (new) documents the contract and the bot-side
  wiring; `docs/RAILWAY.md` gets the two new env vars; `CLAUDE.md` project
  structure + doc index updated.
- `tests/test_heartbeat.py`: payload defaults/overrides, start()/stop()
  lifecycle (no-op without config), and the down/degraded/ok decision table
  for `_build_heartbeat_checks` against a bare stand-in object (no live
  Discord gateway needed).

## Files modified

- `services/heartbeat.py` (new)
- `bot.py`
- `config.py`
- `docs/HEALTH_MONITOR.md` (new)
- `docs/RAILWAY.md`
- `CLAUDE.md`
- `tests/test_heartbeat.py` (new)

## Decisions made and why

- **aiohttp over httpx**: the brief's sample code used `httpx`, but this repo
  standardizes on `aiohttp` for every other outbound HTTP client
  (`services/altguard_client.py`, the gateway adapters). Matching that avoids
  adding a second HTTP dependency for the same job.
- **Start in `on_ready`, not `setup_hook`**: the brief calls this out
  explicitly for the bot ("a bot that has never been ready has nothing to
  report"), and it lines up with how `status_update`/other tasks already wait
  on `wait_until_ready()`.
- **No sharding branch beyond the "not sharded" default**: `ModdyBot` extends
  `commands.Bot`, not `AutoShardedClient` (confirmed in `moddy/commands.py`),
  so `shards` is always empty and the checks fall back to the single virtual
  shard the brief describes for an unsharded process.

## Known issues and follow-ups

- The bot's separate role as the monitor's **display channel** (Redis
  `moddy:hm:notify`/`:ack`/`:command`, the sticky status message, its
  persistent `Refresh` button) is explicitly out of scope per the brief and
  is not implemented — flagged as a separate follow-up in
  `docs/HEALTH_MONITOR.md`.
- `HM_URL` / `HM_INGEST_TOKEN` still need to be set in the Railway
  environment for the heartbeat to actually start; until then it stays
  silently disabled (by design).
