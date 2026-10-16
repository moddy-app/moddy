# Health Monitor Heartbeats

The bot pushes two independent heartbeats. Neither one polls anything —
each is the bot proactively saying "I'm alive" on its own schedule, and
silence is what triggers an alert on the receiving end.

| | Moddy Health Monitor | Better Stack |
|---|---|---|
| Purpose | Internal ecosystem dashboard (`moddy-health-monitor`) | External uptime/incident alerting |
| Interval | 20s | 3 minutes |
| Transport | `POST .../ingest/heartbeat`, JSON body, `X-Health-Token` | plain `GET` on a secret URL |
| Client | `services/heartbeat.py` (`HeartbeatClient`) | `services/betterstack_heartbeat.py` (`BetterStackHeartbeat`) |
| Env vars | `HM_URL`, `HM_INGEST_TOKEN` | `BETTERSTACK_HEARTBEAT_URL` |

Both are isolated additions: fire-and-forget background tasks that never
block the bot's own startup, shutdown, or request handling, and that disable
themselves cleanly (with a `logger.warning`) when their env var is missing.

## Moddy Health Monitor

Watches the whole Moddy ecosystem as a **dead man's switch**: it never polls
a service for its state — each service pushes its own state every 20
seconds. Three missed heartbeats (60s TTL) and the service is considered
down.

### Contract

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

### Implementation

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

### `incident_active`

Every heartbeat response sets `self.heartbeat.incident_active`. Nothing in
the bot currently branches on it — it is exposed for future use (e.g. cutting
non-critical notifications during an incident), matching the "optional to
consume" wording of the monitor's integration contract.

## Better Stack heartbeat monitor

A separate, external cron/heartbeat monitor: Better Stack expects a request
to a secret URL at least once per configured frequency + grace period, or it
raises an incident to the on-call team. No JSON body, no token header — the
URL itself is the secret.

### Contract

```
GET https://uptime.betterstack.com/api/v1/heartbeat/<TOKEN>        # healthy
GET https://uptime.betterstack.com/api/v1/heartbeat/<TOKEN>/fail   # explicit failure
```

### Implementation

- `services/betterstack_heartbeat.py` — `BetterStackHeartbeat`: same
  fire-and-forget shape as `HeartbeatClient` (background task, 5s timeout,
  never blocks, never retries aggressively). Every 3 minutes it calls an
  optional `healthy` coroutine and pings the plain URL when it returns
  `True`, `/fail` otherwise. With no `healthy` callback every ping reports
  success.
- `bot.py`:
  - `ModdyBot.__init__` constructs `self.betterstack_heartbeat =
    BetterStackHeartbeat(url=BETTERSTACK_HEARTBEAT_URL, healthy=self._is_bot_healthy)`.
  - `ModdyBot._is_bot_healthy()` reuses `_build_heartbeat_checks()` and
    reports healthy iff its `status` is `"ok"` — the two heartbeats never
    disagree about whether the bot is fine.
  - Started in `on_ready()` right after `self.heartbeat.start()`, stopped in
    `close()` right after `self.heartbeat.stop()`.

## Environment variables

```env
HM_URL=https://<monitor>                                        # no trailing slash
HM_INGEST_TOKEN=<shared secret>                                 # identical across every monitored service
BETTERSTACK_HEARTBEAT_URL=https://uptime.betterstack.com/api/v1/heartbeat/<TOKEN>
```

Any of them missing disables the corresponding heartbeat cleanly with a
`logger.warning` — never a crash, never a delayed startup. See
[RAILWAY.md](RAILWAY.md).
