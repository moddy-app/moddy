# Premium — how Moddy decides a server (or a user) is premium

There are **two different questions** and they have two different answers.
Mixing them up is the most common bug in this area.

| Question | Helper | Source of truth |
|---|---|---|
| Does **this user** pay? | `utils.subscription.is_subscribed(bot, user_id)` | `users.subscription_tier` / `subscription_expires_at` |
| Is **this server** premium? | `utils.subscription.is_guild_premium(bot, guild_id)` | `subscription_servers` ⨝ an active subscription |

> ⚠️ There is **no `PREMIUM` guild attribute**. `db.has_attribute('guild', …, 'PREMIUM')`
> is not the premium system — do not use it to gate a server feature.
> (A `PREMIUM` *user* attribute exists for legacy staff tooling only.)

---

## The model

A subscriber (`users.subscription_tier` set, `subscription_expires_at` in the
future or `NULL`) picks a limited number of servers on the dashboard. Each pick
is a row in `subscription_servers`. A guild is premium **while at least one of
the users who selected it still has an active subscription** — so premium
follows the payer, not the server: if the subscription lapses, every server they
had selected silently stops being premium.

```
users (subscription_tier, subscription_expires_at)
   │ 1
   │
   │ N
subscription_servers (user_id, server_id, added_at)
```

Note the column types: `subscription_servers.user_id` and `.server_id` are
**text**, `users.user_id` is **bigint**. The join casts (`ss.user_id::bigint`),
and every call passes the id as `str(...)`. See
[`db/repositories/subscription.py`](../db/repositories/subscription.py).

---

## Reading it from the bot

```python
from utils.subscription import is_guild_premium

if not await is_guild_premium(bot, guild_id):
    # show the locked state / refuse the premium action
    ...
```

- **Never** call `bot.db.is_guild_premium()` directly on a hot path — the
  helper in `utils/subscription.py` adds the Redis cache described below. The
  repository method is the uncached fallback the helper builds on.
- The bot **never writes** subscription data. Only the backend does.
- A **global sanction** outranks the subscription: a user or a server that is
  globally *limited* or *suspended* is never premium, whatever the billing
  state says. Both helpers check it first, outside the Redis cache, so lifting
  the sanction restores premium immediately — see
  [GLOBAL_SANCTIONS.md](GLOBAL_SANCTIONS.md).

### Redis cache

| Key | Value | TTL |
|---|---|---|
| `sub:user:{user_id}` | JSON `{tier, expires_at, stripe_customer_id}` | until expiry |
| `sub:guild:{guild_id}` | `"1"` / `"0"` | 300 s |

The guild key is written by the bot as a read-through cache and read by
`is_guild_premium`. The short TTL is a self-healing safety net, not the primary
invalidation path.

### Invalidation (backend → bot)

The backend publishes on `moddy:bot`:

```json
{ "type": "premium_activated",   "guild_id": 123456789 }
{ "type": "premium_deactivated", "guild_id": 123456789 }
```

`ModdyBot._handle_bot_event` drops `sub:guild:{guild_id}` on either event, so
the next check re-reads from PostgreSQL. Publish one event **per affected
guild** when a subscription starts, renews, lapses or when the subscriber edits
their server selection.

User-scoped changes go on `moddy:subscription:updates` instead — see
[SUBSCRIPTION_SCHEMA.md](SUBSCRIPTION_SCHEMA.md).

---

## Gating a feature

1. Check premium **at the moment of the action**, not only when rendering the
   UI — a panel can sit open for hours, and a stale render must never be an
   entitlement.
2. Check it again on anything the **dashboard** delegates to the bot: the
   backend's own check is a UX filter, not a trust boundary.
3. Prefer *degrading* over *hiding*: show the section with a locked state and a
   link to `https://dashboard.moddy.app/select-premium-servers`, so servers
   discover what they would get.

[`modules/bot_customization.py`](../modules/bot_customization.py) +
[`modules/configs/bot_customization_config.py`](../modules/configs/bot_customization_config.py)
implement all three (premium identity, free name style), and
[`modules/social_notifications.py`](../modules/social_notifications.py) shows
the quota-style variant (premium raises a limit instead of unlocking a
feature).

---

## What premium currently changes

| Feature | Free | Premium |
|---|---|---|
| Bot Customization — nickname / avatar / banner / bio | ❌ | ✅ |
| Bot Customization — name style (font, effect, colours) | ✅ | ✅ |
| Social Notifications — accounts per platform | `FREE_PER_PLATFORM_LIMIT` | `PREMIUM_PER_PLATFORM_LIMIT` |
| Social Notifications — poll interval | slow tier | fast tier |
