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

## 6. Backend integration

There are two ways for the backend to create/edit subscriptions. **Option A
(delegation) is recommended** — it avoids duplicating any logic.

### Option A — Delegate to the bot via `moddy:tasks` (recommended)

The backend pushes a task on the existing `moddy:tasks` stream and the bot does
everything (resolve via the service, write the DB row, reconcile on remove). The
backend never touches Redis `feeds:*` nor the `social_subscriptions` table.

Task message (`XADD moddy:tasks type=<...> guild_id=<id> payload=<json>`):

| `type` | `payload` |
|---|---|
| `social_subscribe` | `{request_id?, platform, identifier, channel_id, role_ids?, message?, created_by?}` |
| `social_unsubscribe` / `social_remove` | `{request_id?, platform, target_id}` |
| `social_update` | `{request_id?, platform, target_id, channel_id?, message?, mention_role_ids?, enabled?}` (DB-only) |

The bot publishes the **result** back on the `moddy:dashboard` Pub/Sub channel,
correlated by `request_id`:

```json
{ "type": "social_subscribe_result", "request_id": "…", "guild_id": 123,
  "ok": true, "platform": "youtube", "target_id": "UC…",
  "display_name": "MrBeast", "avatar_url": "https://…" }
```
On failure: `{ "ok": false, "error": "<code>" }` (service error codes from §2, plus
`guild_not_found`, `missing_fields`, `module_unavailable`, `unknown_action`,
`internal_error`). Handled in `bot.py::_process_social_task` →
`cogs/social_notifications.py::SocialNotifications.handle_backend_task`.

> With Option A the backend has **zero** duplicated logic: poll-interval policy,
> canonical resolution and reconcile all stay in the bot.

### Option B — Write the table directly

Only if you deliberately want the backend to own the write. Then it must mirror
everything below. The `social_subscriptions` table is shared bot ⇄ backend.
**Whoever writes a row is responsible for issuing the matching Redis command** —
the bot does NOT reconcile rows written by the backend. The bot does **not
cache** this table (it re-reads on every event and every `/config` open), so
backend writes are picked up immediately for dispatch; no Pub/Sub invalidation
is needed for the table itself.

### Column semantics (writer contract)

| Column | Rule |
|---|---|
| `platform` | one of `youtube`/`twitch`/`bluesky`/`rss`/`instagram` (instagram → service returns `platform_disabled`) |
| `target_id` | ALWAYS the **canonical** id from the service reply — never the raw user input |
| `identifier` | raw user input (display / fallback only) |
| `display_name`, `avatar_url` | from the service reply (optional) |
| `channel_id` | target Discord text/announcement channel (required) |
| `message` | custom template ≤ 1500 chars, `NULL` = localized default. Placeholders: `{author}` `{title}` `{url}`/`{link}` `{platform}` |
| `mention_role_ids` | `BIGINT[]` roles to ping (`{}` = none) |
| `poll_interval` | the interval **this guild requested** (premium vs free, §3); `NULL` for realtime/default. Used by `MIN()` on unsubscribe |
| `enabled` | `false` = paused (dispatch skips it, target stays subscribed) |

Uniqueness `(guild_id, platform, target_id)`: re-inserting updates the row and
resets `enabled = TRUE`.

### A. Add / update — resolve via the service FIRST

1. `poll = premium ? POLL_INTERVALS[platform].premium : .free` (`NULL` for bluesky).
2. `XADD feeds:commands {action:"subscribe", platform, identifier, poll_interval:poll?}`, await reply (`request_id`, 10 s).
3. `ok=false` ⇒ show the error, write nothing.
4. `ok=true` ⇒ upsert with the canonical `target_id`:

```sql
INSERT INTO social_subscriptions
  (guild_id, platform, target_id, identifier, display_name, avatar_url,
   channel_id, message, mention_role_ids, poll_interval, enabled, created_by,
   created_at, updated_at)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::bigint[],$10,TRUE,$11,NOW(),NOW())
ON CONFLICT (guild_id, platform, target_id) DO UPDATE SET
  identifier=EXCLUDED.identifier, display_name=EXCLUDED.display_name,
  avatar_url=EXCLUDED.avatar_url, channel_id=EXCLUDED.channel_id,
  message=EXCLUDED.message, mention_role_ids=EXCLUDED.mention_role_ids,
  poll_interval=EXCLUDED.poll_interval, enabled=TRUE, updated_at=NOW();
```

> Store the **requested** interval (not the clamped value echoed by the service),
> so the cross-guild `MIN()` stays correct.

### B. Edit channel / roles / message / pause — DB only

No Redis command (target stays subscribed). `enabled=false` to pause, `true` to resume.

### C. Remove — DB then reconcile with the service

```sql
DELETE FROM social_subscriptions WHERE guild_id=$1 AND platform=$2 AND target_id=$3;

SELECT COUNT(*)        FROM social_subscriptions WHERE platform=$1 AND target_id=$2;                       -- remaining
SELECT MIN(poll_interval) FROM social_subscriptions WHERE platform=$1 AND target_id=$2
       AND poll_interval IS NOT NULL;                                                                       -- min_interval
```
- `remaining = 0` ⇒ `unsubscribe` **without** `poll_interval` (service removes the target).
- `remaining > 0` ⇒ `unsubscribe` **with** `poll_interval = min_interval` (or sentinel `60` if `NULL`). **Never omit it here**, or the target is removed for every other guild.

### D. Bot/guild removal

The bot already runs flow C per target on `on_guild_remove`. If the backend
deletes a guild's data, it must run flow C for each `(platform, target_id)` too.

### Pitfalls

- ❌ Never store raw input in `target_id`. ❌ Never write the table without the
  matching Redis command (except B). ❌ Never omit `poll_interval` on a *partial*
  unsubscribe. ✅ `enabled=false` to pause without touching the service.
  ✅ Discord ids as `BIGINT`, timestamps UTC.

---

## 7. Custom message placeholders

A subscription's optional custom message supports: `{author}`, `{title}`,
`{url}` / `{link}`, `{platform}`. Empty ⇒ a localized default caption is used.
