# Redis — Inter-Service Communication

> How the bot talks to the backend and to external services (e.g. `moddy-feeds`)
> over the **shared Redis** instance, and how to wire up a **new** service the
> same way.

---

## 1. The shared Redis instance

The bot, the backend, and external services (`moddy-feeds`, …) all point at
the **same Redis** (`REDIS_URL` / `REDIS_PASSWORD`, Railway env vars —
`config.py`). There is no per-service Redis: everyone reads/writes the same
keyspace, so **namespacing prevents collisions** (see §5).

The bot's connection is created once in `bot.py::_setup_redis()` and exposed
as `bot.redis` (a `redis.asyncio` client, `decode_responses=True`):

```python
# bot.py
self.redis = aioredis.from_url(REDIS_URL, password=REDIS_PASSWORD, decode_responses=True)
await self.redis.ping()
asyncio.create_task(self._listen_pubsub())
asyncio.create_task(self._consume_task_stream())
```

If `REDIS_URL` is unset or the connection fails, `bot.redis` stays `None` —
**every** piece of code that touches Redis must guard for that (`if not
self.redis: ...`) instead of assuming it's connected. `FeedsClient` and
`GlobalSanctionService` both do this.

---

## 2. The three communication patterns

Redis is used for three distinct jobs. Pick the right one — don't reuse
Pub/Sub for something that must never be lost, and don't use a Stream for a
plain cache value.

### a) Pub/Sub — fire-and-forget notifications

For events the bot can safely miss if it's momentarily offline: config
reload, cache invalidation, "something changed, go re-read the DB".
No persistence, no replay, no acknowledgement.

- The bot subscribes once in `bot.py::_listen_pubsub()` to a fixed set of
  channels and dispatches by channel name / `type` field.
- Publishing is a single `redis.publish(channel, json.dumps(payload))` call.

### b) Streams (+ consumer groups) — guaranteed, ordered delivery

For anything the bot **must** eventually process, even if it was down when
the event was produced: task queues, command/reply RPC, notification feeds.

- A **plain stream + last-processed-id key** (`XADD` / `XREAD`) is enough for
  a single consumer that must resume where it left off (`moddy:tasks`).
- A **consumer group** (`XGROUP CREATE` + `XREADGROUP` + `XACK`) is needed
  when you want at-least-once delivery with explicit acknowledgement, or
  multiple bot processes sharing the load (`notifications:queue`).
- **Request/reply over a stream** (used for the feeds service commands) is a
  stream both sides can produce/consume, correlated by a `request_id` UUID:
  the caller `XADD`s a command with a `request_id`, then awaits a future
  that gets resolved when a reply carrying the same `request_id` shows up on
  the replies stream (with a timeout — never wait forever).

### c) Plain keys — cache, counters, health

- **Cache with TTL**: `guild:{id}:config`, `discord:guild:{id}:channels`, …
  — owned by whoever writes it, read-only for the other side.
- **Counters**: `gateway/quota.py` uses `quota:{scope}:{key}:{type}:{date}`
  daily counters (`INCR` + `EXPIRE`) for AI/API quota tracking.
- **Heartbeat / health**: a service sets a short-TTL key it refreshes
  periodically; other side checks `EXISTS` to know if it's alive
  (`feeds:heartbeat`, TTL ~90s, checked by `FeedsClient.is_service_alive()`).

---

## 3. Current inventory

### Pub/Sub channels

| Channel | Direction | Purpose |
|---|---|---|
| `moddy:bot` | Backend → Bot | Generic events: `config_updated`, `module_updated`, `premium_activated`, `payment_failed`, … (`bot.py::_handle_bot_event`) |
| `moddy:subscription:updates` | Backend → Bot | Premium subscription cache invalidation + DM triggers: `refresh`, `notify_payment_late`, `notify_subscription_started/renewed/cancelled/updated/upgraded/downgraded` (`bot.py::_handle_subscription_event`) |
| `moddy:blacklist:updates` | Backend/Bot → Bot | Global-sanction cache invalidation (`bot.py::_handle_blacklist_event`) |
| `moddy:dashboard` | Bot → Backend | Bot-originated task results, keyed by `request_id` — `{type}_result` for every `moddy:tasks` task type (`social_subscribe_result`, `bot_customization_update_result`, …) — `bot.py::_process_*_task` |
| `moddy:sanctions` | Bot → Backend | Global sanction lifecycle events (`global_sanction_applied/lifted`, `enforcement_halted/resumed/executed`), each with an ISO `ts` — `services/global_sanction_service.py::SANCTION_CHANNEL` |

### Streams

| Stream | Producer | Consumer | Notes |
|---|---|---|---|
| `moddy:tasks` | Backend | Bot | Critical guaranteed tasks (`update_panel`, `send_announcement`, `social_subscribe/unsubscribe/remove/update`, `bot_customization_update`, `case_add_sanction/revoke_sanction`, …). Plain `XREAD` + `moddy:tasks:last_id` key to resume (`bot.py::_consume_task_stream` / `_process_task`) |
| `feeds:commands` | Bot | `moddy-feeds` service | `subscribe` / `unsubscribe` commands, correlated by `request_id` |
| `feeds:replies` | `moddy-feeds` service | Bot | Replies to `feeds:commands`, correlated by `request_id`, 10s timeout (`services/feeds_client.py`) |
| `notifications:queue` | `moddy-feeds` service | Bot (consumer group `discord-bot`) | Normalized notification events, `XACK`ed unconditionally (service dedups) |

### Plain keys

| Key | Owner | Purpose |
|---|---|---|
| `session:{token}` | Backend | User sessions (bot: read-only) |
| `guild:{id}:config`, `discord:guild:{id}:{info,channels,roles,emojis}` | Backend | Discord/DB cache, short TTL |
| `moddy:bot_guilds` | Backend (invalidated by Bot) | Guild list cache — bot deletes it on `on_guild_join`/`on_guild_remove` |
| `moddy:tasks:last_id` | Bot | Resume point for `moddy:tasks` |
| `feeds:heartbeat` | `moddy-feeds` service | Health check, TTL ~90s |
| `sub:user:{user_id}` | Backend (bot re-writes opportunistically) | Subscription cache — `{tier, expires_at, stripe_customer_id}` JSON, TTL from `expires_at` (`utils/subscription.py`, `docs/SUBSCRIPTION_SCHEMA.md`) |
| `sub:guild:{guild_id}` | Bot | `is_guild_premium` cache, fixed 300s TTL (`utils/subscription.py`) |
| `quota:{scope}:{key}:{type}:{date}` | Bot (`gateway/quota.py`) | Daily API quota counters, TTL 48h |
| `gateway:log_buffer` (LIST) | Bot (`gateway/logger.py`) | Buffered gateway call logs, flushed to PostgreSQL periodically |
| `automod:budget:{guild_id}:{date}` | Bot (`automod/engine.py`) | Automod AI daily spend budget counter |
| `automod:agg:buf:{guild}:{channel}:{author}` (LIST) | Bot (`automod/engine.py`) | Message-aggregation buffer for automod, window-TTL'd |
| `automod:agg:judged:{guild}:{channel}:{author}` (SET) | Bot (`automod/engine.py`) | Dedup of already-judged messages in the aggregation window |
| `rel:{guild}:{min(a,b)}:{max(a,b)}` (HASH) | Bot (`automod/relations.py`) | Relationship/familiarity graph between two users, 60-day TTL refreshed on write |

---

## 4. Adding a new Redis-based service

`services/feeds_client.py` is the reference implementation — copy its shape
for a new service rather than inventing a new transport style. Checklist:

1. **Own your namespace.** Prefix every stream/channel/key with the
   service's name (e.g. `myservice:commands`, `myservice:events`,
   `myservice:heartbeat`) — never reuse `moddy:*` (backend-owned) or
   `feeds:*` (feeds-owned).
2. **One client class**, constructed with `self.bot = bot` and a `redis`
   property that reads `getattr(self.bot, "redis", None)` (never assume the
   bot has connected — guard every call).
3. **Commands you send**: `XADD <service>:commands {"data": json.dumps({...,
   "request_id": uuid4()})}`, then await a future resolved by a background
   reader on `<service>:replies` matching `request_id`, with a timeout
   (`asyncio.wait_for`, return an `{"ok": False, "error": "timeout"}`-style
   dict rather than raising).
4. **Events you receive**: if delivery must never be missed, use a stream +
   consumer group (`XGROUP CREATE ... mkstream=True`, ignore `BUSYGROUP`),
   `XREADGROUP` in a loop, and always `XACK` after handling — even on
   failure — unless you have real retry/DLQ logic (the feeds queue design
   deliberately trades "never gets stuck" for "the service already dedups").
5. **Start/stop**: a `start(handler)` that spawns the reader/consumer as
   `asyncio.create_task`s, guarded by a `self._started` flag, and a `stop()`
   that cancels them — called from the bot's setup/shutdown, not from
   arbitrary cog code.
6. **Health**: publish a heartbeat key with a short TTL if other
   services/the backend need to know you're alive.
7. **Document the contract**: JSON payload shapes for every command/reply/
   event, add the streams/channels to the inventory tables above, and if the
   backend needs to interoperate, add a `docs/<FEATURE>.md` like
   `docs/SOCIAL_NOTIFICATIONS.md` §2 (bot ⇄ service contract) and §6
   (backend integration options).
8. Wire the client into the bot once (e.g. in `cogs/<feature>.py`, mirroring
   `cogs/social_notifications.py` owning `FeedsClient`) — don't instantiate
   ad-hoc Redis clients scattered across cogs/modules.

---

## 5. Naming conventions

- `moddy:*` — backend-owned, cross-cutting (Pub/Sub events, `moddy:tasks`,
  guild/session cache).
- `<service>:*` — a specific external service's own namespace (`feeds:*`,
  and whatever a new service picks).
- `notifications:queue` — the one exception grandfathered in from the feeds
  integration (not `feeds:notifications`); don't reuse it for anything else.
- `automod:*`, `rel:*`, `gateway:*` — bot-internal namespaces, each scoped to
  one subsystem (automod runtime state, gateway log buffer). Follow this
  pattern for new bot-only state instead of inventing a flat key name.
- `bot:*` — reserved by `docs/BACKEND-INTEGRATION.md` §9 as the generic
  bot-only prefix, but not actually used in the current codebase (bot-only
  state uses subsystem prefixes like `automod:`/`rel:`/`gateway:` instead).
  A new service can still use it, or a subsystem-specific prefix as above —
  either way, don't reuse someone else's prefix.
- JSON payloads on Pub/Sub and Streams commonly carry a `type`/`action` field
  to dispatch on, and a `request_id` when a reply is expected. Discord IDs
  travel as strings in stream fields (`XADD` only accepts strings) — always
  `int()` them back on the read side.

---

## 6. See also

- [docs/BACKEND-INTEGRATION.md](BACKEND-INTEGRATION.md) — full bot ⇄ backend
  contract (Pub/Sub event types, `moddy:tasks` task types, cache key TTLs).
- [docs/SOCIAL_NOTIFICATIONS.md](SOCIAL_NOTIFICATIONS.md) §2, §6 — the
  concrete worked example of a service integration end to end (commands,
  replies, queue, backend delegation options).
- [docs/GLOBAL_SANCTIONS.md](GLOBAL_SANCTIONS.md) — `moddy:sanctions`
  Pub/Sub payloads.
- `gateway/quota.py` — plain-key counter pattern for rate/quota tracking.
