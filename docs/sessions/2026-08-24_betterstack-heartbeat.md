# Session: Better Stack heartbeat integration

**Date:** 2026-08-24
**Agent:** Claude Code

## Summary

Added a second, independent heartbeat next to the Moddy Health Monitor one
(#357): a plain ping to a Better Stack cron/heartbeat monitor every 3
minutes. Isolated addition, no behavior change to existing features.

What shipped:

- `services/betterstack_heartbeat.py` — `BetterStackHeartbeat`: same
  fire-and-forget shape as `HeartbeatClient` (background task, 5s timeout,
  never blocks). Every 3 minutes it calls an optional `healthy` coroutine and
  GETs the plain heartbeat URL when healthy, `<url>/fail` otherwise — Better
  Stack's contract has no JSON body and no token header, the URL is the
  secret.
- `bot.py`: `self.betterstack_heartbeat` constructed in `__init__` with
  `healthy=self._is_bot_healthy`; started in `on_ready()` right after the
  Moddy Health Monitor heartbeat, stopped in `close()` right after it.
  `ModdyBot._is_bot_healthy()` reuses `_build_heartbeat_checks()` — healthy
  iff `status == "ok"` — so the two heartbeats never disagree about whether
  the bot is fine.
- `config.py`: `BETTERSTACK_HEARTBEAT_URL`, optional — missing disables the
  ping with a `logger.warning`, never a crash.
- `docs/HEALTH_MONITOR.md` restructured into two sections (Moddy Health
  Monitor / Better Stack) with a comparison table up top; `docs/RAILWAY.md`
  gets the new env var; `CLAUDE.md` project structure updated.
- `tests/test_heartbeat.py` extended: `BetterStackHeartbeat` start()/stop()
  lifecycle, plain-vs-`/fail` URL selection, and `_is_bot_healthy` against
  the same down/degraded/ok stand-in bot used for `_build_heartbeat_checks`.

## Files modified

- `services/betterstack_heartbeat.py` (new)
- `bot.py`
- `config.py`
- `docs/HEALTH_MONITOR.md`
- `docs/RAILWAY.md`
- `CLAUDE.md`
- `tests/test_heartbeat.py`

## Decisions made and why

- **Separate client file, not folded into `HeartbeatClient`**: the two
  protocols don't share a body shape (JSON+token vs. bare GET) or a status
  vocabulary (`ok`/`degraded`/`down` vs. success/`/fail`) — forcing one class
  to cover both would need more branching than just having two small,
  single-purpose clients.
- **`_is_bot_healthy` reuses `_build_heartbeat_checks`** rather than
  recomputing its own notion of "healthy": one status decision, consumed by
  both heartbeats, so they can't drift apart on what "fine" means.
- **Started/stopped alongside the existing heartbeat** in `on_ready()`/
  `close()` rather than on a separate lifecycle hook — same reasoning as
  #357 (nothing worth reporting before the bot is ready), and keeps both
  heartbeats' lifecycle in one obvious place.

## Known issues and follow-ups

- `BETTERSTACK_HEARTBEAT_URL` still needs to be set in the Railway
  environment (the actual heartbeat is created on the Better Stack side
  first, per their step-by-step setup) — until then this heartbeat stays
  silently disabled (by design).
