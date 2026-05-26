# Subscription System — Schema & Contracts

> This document covers the DB schema, Redis contract, Pub/Sub contract,
> and what the **backend** must do for each subscription lifecycle action.
> The bot is **read-only** on all subscription data.

---

## 1. Database Schema

### Table `users` — added columns

| Column | Type | Nullable | Description |
|---|---|---|---|
| `subscription_expires_at` | `TIMESTAMPTZ` | YES | UTC timestamp when the subscription expires; `NULL` = no expiry (lifetime) |
| `subscription_tier` | `TEXT` | YES | Plan identifier: `'free_trial'`, `'monthly'`, `'yearly'` (or `NULL` = no sub) |
| `stripe_customer_id` | `VARCHAR(50)` | YES | Stripe customer ID, e.g. `cus_UAf6a2WKTw6yCI` (pre-existing column) |

**Active subscription rule:**
```
is_active = subscription_tier IS NOT NULL
        AND (subscription_expires_at IS NULL OR subscription_expires_at > NOW())
```

---

### Table `subscription_plans`

Catalogue of available plans. The bot reads this table for display names.

| Column | Type | Default | Description |
|---|---|---|---|
| `id` | `TEXT` | — | Plan identifier (PRIMARY KEY), e.g. `'max'` |
| `name` | `TEXT` | — | Human-readable name, e.g. `'Moddy Max'` |
| `is_active` | `BOOLEAN` | `true` | Whether new subscriptions can be created for this plan |

**Seed row:** `INSERT INTO subscription_plans (id, name) VALUES ('max', 'Moddy Max');`

---

### Table `subscription_servers`

Servers linked to a user's subscription (max 5 per user, enforced by the backend).

| Column | Type | Default | Description |
|---|---|---|---|
| `user_id` | `TEXT` | — | Discord user ID (FK → `users.id`) |
| `server_id` | `TEXT` | — | Discord guild ID (FK → `servers.id`) |
| `added_at` | `TIMESTAMPTZ` | `NOW()` | When the server was linked |

**Primary key:** `(user_id, server_id)`

---

## 2. Redis Cache Contract

### Key format

```
sub:user:{user_id}
```

Example: `sub:user:123456789012345678`

### Value format (JSON string)

```json
{
  "tier": "monthly",
  "expires_at": "2026-06-01T00:00:00+00:00",
  "stripe_customer_id": "cus_UAf6a2WKTw6yCI"
}
```

| Field | Type | Description |
|---|---|---|
| `tier` | `string \| null` | Same as `users.subscription_tier` |
| `expires_at` | ISO 8601 string `\| null` | Same as `users.subscription_expires_at`; `null` = no expiry |
| `stripe_customer_id` | `string \| null` | Same as `users.stripe_customer_id` |

### TTL policy

- TTL = `(expires_at - now)` in seconds, rounded down to the nearest second.
- If `expires_at` is `NULL` (no expiry), the key is set **without a TTL**.
- On subscription cancellation / expiry, the backend must **delete** the key immediately.

### Bot read strategy

1. `GET sub:user:{user_id}` from Redis
2. If hit → parse JSON, compute `is_active` from `tier` + `expires_at`
3. If miss → query DB, write result to Redis with TTL, return

### When to invalidate

The **backend** must delete or update the Redis key on every mutation:
- Subscription created / renewed / upgraded / cancelled / expired
- `stripe_customer_id` updated
- Server linked / unlinked

---

## 3. Pub/Sub Contract

### Channel

```
moddy:subscription:updates
```

Direction: **backend → bot** (fire-and-forget; bot may miss messages if restarting).

### Message format

All messages are JSON strings published via `PUBLISH`.

#### `refresh` — invalidate cache

```json
{ "type": "refresh", "user_id": "123456789012345678" }
```

Bot action: delete `sub:user:{user_id}` from Redis. Next read will hit DB.

---

#### `notify_payment_late` — payment failed / overdue

```json
{ "type": "notify_payment_late", "user_id": "123456789012345678" }
```

Bot action:
1. Invalidate Redis cache.
2. Send DM to user:

> ### ⚠️ Problème de paiement
> Un problème est survenu lors du renouvellement de ton abonnement.
> Merci de mettre à jour tes informations de paiement pour maintenir l'accès.
>
> *[Gérer mon abonnement → https://dashboard.moddy.app/billing]*

---

#### `notify_subscription_started` — new subscription

```json
{ "type": "notify_subscription_started", "user_id": "123456789012345678", "tier": "monthly" }
```

Bot action:
1. Invalidate Redis cache.
2. Send DM to user:

> ### ✨ Abonnement activé
> Ton abonnement **Moddy Max** est maintenant actif. Merci pour ton soutien !
>
> *[Gérer mon abonnement → https://dashboard.moddy.app/billing]*

---

#### `notify_subscription_renewed` — renewal

```json
{ "type": "notify_subscription_renewed", "user_id": "123456789012345678", "tier": "yearly" }
```

Bot action:
1. Invalidate Redis cache.
2. Send DM to user:

> ### ✨ Abonnement renouvelé
> Ton abonnement **Moddy Max** a été renouvelé avec succès.
>
> *[Gérer mon abonnement → https://dashboard.moddy.app/billing]*

---

## 4. Backend Responsibilities

The following table lists every subscription event and what the backend must do.

| Event | DB writes | Redis | Pub/Sub |
|---|---|---|---|
| **Checkout completed / sub created** | Set `subscription_tier`, `subscription_expires_at`, `stripe_customer_id` on `users` | Write `sub:user:{id}` with TTL | Publish `notify_subscription_started` |
| **Invoice paid / renewed** | Update `subscription_expires_at` | Update or re-write `sub:user:{id}` with new TTL | Publish `notify_subscription_renewed` |
| **Payment failed (grace period)** | No change | No change | Publish `notify_payment_late` |
| **Subscription cancelled / expired** | Set `subscription_tier = NULL`, optionally clear `subscription_expires_at` | DELETE `sub:user:{id}` | Publish `refresh` |
| **Server linked** | INSERT into `subscription_servers` | Publish `refresh` (bot re-reads linked servers from DB on demand) | Publish `refresh` |
| **Server unlinked** | DELETE from `subscription_servers` | — | Publish `refresh` |
| **Stripe customer ID updated** | Update `stripe_customer_id` on `users` | Update `sub:user:{id}` | Publish `refresh` |

> **Note:** The bot **never writes** `subscription_tier`, `subscription_expires_at`, `stripe_customer_id`, or `subscription_servers`. All mutations are the backend's responsibility.

---

## 5. Bot Read-Only Invariants

- The bot's subscription helper (`utils/subscription.py`) only calls `GET`/`SETEX`/`SET`/`DELETE` on Redis keys prefixed with `sub:user:`.
- The bot's DB repository (`db/repositories/subscription.py`) only executes `SELECT` queries.
- If Redis is unavailable, the bot falls back to DB transparently.
- There is **no polling** — subscription state is checked lazily per interaction.
