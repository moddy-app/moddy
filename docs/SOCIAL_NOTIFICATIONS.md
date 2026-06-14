# Social Notifications Module

Posts a Discord notification whenever a followed social account publishes new
content (YouTube video, Twitch live, Bluesky post, RSS article, …).

The bot owns **no** polling logic. Detection is delegated to the external
**`moddy-feeds`** service; the bot only talks to it through **shared Redis
streams**. The bot never reads/writes the service's database.

---

## 1. Components

| File | Role |
|---|---|
| `modules/social_notifications.py` | Module registration (`/config` entry), platform catalogue, poll-interval policy, Components V2 notification renderer |
| `modules/configs/social_notifications_config.py` | `/config` panel (list / add / manage / remove) |
| `cogs/social_notifications.py` | Owns the feeds client, dispatches events to guilds, centralises subscribe/unsubscribe logic, guild cleanup |
| `services/feeds_client.py` | Redis transport: send commands, await correlated replies, consume the notification queue |
| `db/repositories/social.py` + `social_subscriptions` table | Source of truth for "who follows what" |

### Why a dedicated table instead of `guilds.data.modules` JSONB?

A single target (e.g. one YouTube channel) is shared by many guilds, and each
incoming event must fan out to every follower. That needs a fast reverse lookup
`(platform, target_id) -> guilds`, which an indexed table provides and JSONB
does not. The module still appears in `/config` via a thin `ModuleBase` shell.

### `social_subscriptions` schema

```
id, guild_id, platform, target_id (CANONICAL), identifier (raw input),
display_name, avatar_url, channel_id, message (custom template, nullable),
mention_role_ids BIGINT[], poll_interval, enabled, created_by, timestamps
UNIQUE (guild_id, platform, target_id)
INDEX (platform, target_id)   -- dispatch
INDEX (guild_id)              -- config panel
```

We always store the **canonical** `target_id` returned by the service, never
the raw user input.

---

## 2. Redis contract (shared with `moddy-feeds`)

| Stream | Direction | Producer | Consumer |
|---|---|---|---|
| `feeds:commands` | bot → service | bot | service (group `moddy-feeds`) |
| `feeds:replies` | service → bot | service | bot (correlated by `request_id`) |
| `notifications:queue` | service → bot | service | bot (group `discord-bot`) |
| `feeds:heartbeat` | key | service | bot (health, TTL ~90 s) |

This is the **same Redis** used for backend Pub/Sub and the `moddy:tasks`
stream. Both the bot and the backend may produce `feeds:commands` / consume the
queue if needed; correlation is by `request_id` so producers don't collide.

- **subscribe / unsubscribe** → `XADD feeds:commands {data: <json>}`, then await
  the matching `request_id` on `feeds:replies` (10 s timeout).
- **notifications** → consumed via consumer group `discord-bot` with `XACK`
  after each event. The service guarantees dedup (7-day window), so the bot
  always acks (never re-queues a poison event).

See the integration spec for the full command/reply/event JSON shapes.

---

## 3. Poll-interval policy (premium vs free)

The service clamps any requested `poll_interval` to each platform's bounds and
keeps the **minimum** across all guilds following a target. We deliberately
request a **fast** interval for premium guilds and a **slower-but-reasonable**
one for free guilds.

> ⚠️ **These values MUST be mirrored in the backend** (if the backend ever issues
> `feeds:commands` itself) so both sides agree. Defined in
> `modules/social_notifications.py::POLL_INTERVALS`.

| Platform | Premium (fast) | Free (reasonable) | Service min / max / default |
|---|---|---|---|
| youtube | **60 s** | **300 s** | 60 / 3600 / 300 |
| twitch | **30 s** | **120 s** | 30 / 600 / 60 |
| rss | **120 s** | **600 s** | 120 / 3600 / 300 |
| instagram (future) | **600 s** | **1800 s** | 600 / 86400 / 1800 |
| bluesky | realtime | realtime | interval ignored |

- Premium status = guild attribute `PREMIUM`.
- Bluesky is realtime: the bot omits `poll_interval` on subscribe.
- **Shared target:** on partial unsubscribe (one guild drops a still-followed
  target), the bot sends the smallest remaining `poll_interval` so the target is
  kept (an omitted interval means "remove the target"). For realtime platforms a
  sentinel interval (`REALTIME_KEEP_INTERVAL = 60`) is sent purely as the
  "keep alive" signal (the value is ignored by the service).

---

## 4. Lifecycle

**Add** (`/config` → Social Notifications → Add):
1. user picks platform + channel + roles + identifier (+ optional message),
2. bot computes `poll_interval` from the guild's premium status,
3. `subscribe` → service resolves the canonical `target_id`/`display_name`/`avatar_url`,
4. on success, the row is persisted. Subscribe is the **final** committing step
   so there are no orphan targets if the user cancels earlier.

**Remove / guild leave:**
1. delete the row(s),
2. if no guild follows the target anymore → `unsubscribe` (full removal),
3. otherwise → `unsubscribe` with the smallest remaining interval (keep alive).

**Dispatch:** the `discord-bot` consumer reads `notifications:queue`, looks up
every enabled follower of `(platform, target_id)`, and posts a Components V2
message (with optional role pings) in each guild's configured channel.

---

## 5. What the backend needs (if anything)

The bot is fully self-contained for this feature — the **only** dependency is a
running `moddy-feeds` service on the shared Redis. The backend does **not** need
to do anything for the bot to work. Optional backend integrations:

- **Health alerting:** watch `EXISTS feeds:heartbeat` (0 ⇒ service down) and
  surface it on the status page / dashboard.
- **Dashboard parity:** if the dashboard ever lets users manage social
  notifications, it must (a) write the `social_subscriptions` table with the
  **same** schema, (b) issue `feeds:commands` using the **same** poll-interval
  policy as the table above, and (c) emit a Pub/Sub `module_updated` on
  `moddy:bot` so the bot can refresh. Until then, no backend change is required.

---

## 6. Custom message placeholders

A subscription's optional custom message supports: `{author}`, `{title}`,
`{url}` / `{link}`, `{platform}`. Empty ⇒ a localized default caption is used.
