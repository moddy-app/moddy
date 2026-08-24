# Health Monitor Heartbeat

The `moddy-health-monitor` service watches the whole Moddy ecosystem as a
**dead man's switch**: it never polls a service for its state — each service
pushes its own state every 20 seconds, and silence *is* the signal. Three
missed heartbeats (60s TTL) and the service is considered down.

This document covers the bot's side of that contract only. It is an isolated
addition and changes nothing about existing bot behavior.

## Contract

```
POST https://<monitor>/ingest/heartbeat
X-Health-Token: <HM_INGEST_TOKEN>
Content-Type: application/json
```

```json
{
  "service": "moddy-bot",
  "status": "ok",
  "version": "v1.4.2",
  "uptime_s": 84213,
  "checks": {
    "is_ready": { "ok": true },
    "discord_gateway": { "ok": true, "latency_ms": 42 },
    "shards": { "ok": true, "connected": 1, "total": 1 }
  },
  "meta": { "shards": "1/1", "guilds": 512 }
}
```

Response: `{"ok": true, "received_at": "...", "incident_active": false}`.
Error codes: `401` missing/invalid token, `503` monitor misconfigured, `422`
invalid body.

The monitor has no per-service logic: it never interprets `checks` keys, it
only renders them. The service alone decides what its own dependencies mean —
for the bot, an event loop that is alive but whose gateway connection is dead
must **never** report `ok`.

## Implementation

- `services/heartbeat.py` — `HeartbeatClient`: a fire-and-forget background
  task (`asyncio.create_task`), 5s timeout per request, 20s interval. A
  failure only logs; the loop never stops itself, never retries aggressively,
  and never blocks anything else. `start()` is a no-op (with a warning) when
  `HM_URL` or `HM_INGEST_TOKEN` is not configured.
- `bot.py`:
  - `ModdyBot.__init__` constructs `self.heartbeat = HeartbeatClient("moddy-bot", ...)`
    with `build=self._build_heartbeat_checks`.
  - `setup_hook()` sets `self.heartbeat.version` once `fetch_version()` resolves.
  - `on_ready()` calls `self.heartbeat.start()` **after** `run_startup_checks()`
    — a bot that has never been ready has nothing to report.
  - `close()` calls `await self.heartbeat.stop()` alongside the other shutdown
    steps.
  - `ModdyBot._build_heartbeat_checks()` builds the payload: `is_ready`,
    `discord_gateway` (latency, `None` while `bot.latency` is `nan`), and
    `shards` (the bot is not sharded, so this is always `1/1` when ready).
    Not ready → `down`; a shard down → `degraded`; otherwise `ok`.

## Environment variables

```env
HM_URL=https://<monitor>            # no trailing slash
HM_INGEST_TOKEN=<shared secret>     # identical across every monitored service
```

Either missing disables the heartbeat cleanly with a `logger.warning` — never
a crash, never a delayed startup. See [RAILWAY.md](RAILWAY.md).

## `incident_active`

Every heartbeat response sets `self.heartbeat.incident_active`. Nothing in
the bot currently branches on it — it is exposed for future use (e.g. cutting
non-critical notifications during an incident), matching the "optional to
consume" wording of the monitor's integration contract.

## Out of scope here

The bot's role as the Health Monitor's **display channel** (posting/editing
incident messages via `moddy:hm:notify` / `moddy:hm:notify:ack`, publishing
`moddy:hm:command`, the sticky status message and its persistent `Refresh`
button) is a separate, larger piece of work and is not implemented by this
heartbeat integration.
