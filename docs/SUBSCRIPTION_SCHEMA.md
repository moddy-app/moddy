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

#### `notify_invoice` — a Stripe invoice was issued

Published for **every** invoice, **including one at 0** — a free trial produces
a real Stripe invoice. Two sources feed it, and the backend deduplicates on
`invoice_id` (`invoice:sent:{id}`, TTL 7 days), so the bot receives **one event
per invoice** whichever path produced it:

- `invoice.payment_succeeded`, right after the `notify_subscription_*` of the
  same payment;
- `billing.start_trial`, directly — Stripe guarantees no event for an invoice
  with no payment.

```json
{
  "type": "notify_invoice",
  "user_id": "123456789012345678",
  "invoice": {
    "invoice_id": "in_1S...",
    "number": "MODDY-0001",
    "amount_paid": 0,
    "currency": "eur",
    "paid_at": "2026-08-30T11:26:40+00:00",
    "period_end": "2026-09-29T11:26:40+00:00",
    "tier": "monthly",
    "billing_reason": "subscription_create",
    "variant": "trial",
    "hosted_invoice_url": "https://invoice.stripe.com/i/...",
    "invoice_pdf": "https://invoice.stripe.com/i/....pdf"
  }
}
```

| Field | Type | Notes |
|---|---|---|
| `invoice_id` | `str` | Stripe identifier. De-duplication key |
| `number` | `str \| null` | Human-readable number. Can be `null` on a brand-new invoice |
| `amount_paid` | `int` | **Smallest unit of the currency** (cents for EUR/USD). **`0` is legitimate** |
| `currency` | `str` | Lowercase ISO code (`eur`) |
| `paid_at` | `str \| null` | ISO 8601 UTC |
| `period_end` | `str \| null` | ISO 8601 UTC — end of the covered period |
| `tier` | `"monthly" \| "yearly"` | Derived from the Stripe price interval |
| `billing_reason` | `str \| null` | `subscription_create` / `subscription_cycle` / … |
| **`variant`** | `"paid" \| "trial" \| "free"` | **What decides the wording** |
| `hosted_invoice_url` | `str \| null` | Stripe's invoice page — main button |
| `invoice_pdf` | `str \| null` | Stripe PDF |

Bot action (`services/invoice_notifier.py`, `bot.invoices`):
1. Invalidate the Redis cache, like every other `notify_*`.
2. DM the user: amount, number, dates, a button to `hosted_invoice_url`.
3. Write the `notifications` row like any other DM — those tables belong to the
   bot, the backend never inserts into them. **No mail:** the backend already
   sent it (Resend), and a second one would read as a second charge.

##### `variant` decides the wording — never the amount

| `variant` | Meaning | What the DM says |
|---|---|---|
| `paid` | An amount was charged | "The payment of **€4.99** has been received" |
| `trial` | 0 at the opening of the subscription — the **free trial** | "Your trial has started: **no amount was charged**" |
| `free` | 0 on a later period (full discount, credit note) | "**No amount was charged** for this period" |

The backend computes it (`invoices.variant_of`): `amount_paid > 0` → `paid`;
else `billing_reason == "subscription_create"` → `trial`; else `free`. The bot
recomputes the same rule only as a fallback for a payload without the field —
**a value it does not know is treated as `paid`**, because announcing that
nothing was charged when something was is the one error a billing message must
never make. The opposite error costs as much: "payment received" on a 0 invoice
sends someone hunting for a debit that does not exist on their statement.

⚠️ **On a `trial`, `period_end` is the date of the first charge, not a
renewal.** It is labelled *First charge* (`field_first_charge`), exactly like
the mail — same date, opposite meaning.

##### Four rules the DM obeys

1. **Amounts as reported, never recomputed.** `amount_paid` is the currency's
   smallest unit, except for the zero-decimal currencies
   (`ZERO_DECIMAL_CURRENCIES` in `services/invoice_notifier.py`: `jpy`, `krw`,
   `xof`…), where it already is the amount. No currency conversion, no VAT
   reconstruction, no hardcoded catalogue price.
2. **A `null` link is no button.** `invoice_pdf` and `number` can be missing on
   a fresh invoice; only the billing dashboard button is unconditional.
3. **No mail from the bot.** The backend already sent it.
4. **A missed event is not an incident.** A restart loses it, like every
   `notify_*` on this channel — the customer still has the mail and the Stripe
   receipt, so there is no catch-up mechanism. `invoice:dm:{invoice_id}`
   (`SET NX`, TTL 7 days) is a second belt against a replayed event, on top of
   the backend's own de-duplication; Redis being unavailable costs a possible
   duplicate, never the DM.

The DM goes through the notification system like everything else
([NOTIFICATIONS.md](NOTIFICATIONS.md)) under the `subscription` service, which
is one of the `OFFICIAL_SERVICES`: its attribution line carries the
verification check, because a billing DM is precisely what a scam impersonates.
Its language is the account's `LANG` attribute — the same one the backend's
mail reads, so the two halves of one invoice never arrive in two languages.

---

## 4. Backend Responsibilities

The following table lists every subscription event and what the backend must do.

| Event | DB writes | Redis | Pub/Sub |
|---|---|---|---|
| **Checkout completed / sub created** | Set `subscription_tier`, `subscription_expires_at`, `stripe_customer_id` on `users` | Write `sub:user:{id}` with TTL | Publish `notify_subscription_started` |
| **Invoice paid / renewed** | Update `subscription_expires_at` | Update or re-write `sub:user:{id}` with new TTL | Publish `notify_subscription_renewed` |
| **Invoice issued (payment or trial)** | — (already done by the event above) | — | Send the customer's mail, then publish `notify_invoice` (deduplicated on `invoice_id`) |
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

---

## 6. `stripe_action` — a stale customer id no longer bricks an account

Nothing to code on the bot side: the signed request/response contract
(`services/stripe_admin_client.py`) is **unchanged**. What changed is what the
backend does when a `stripe_customer_id` designates nothing for the Stripe key
in use (customer created in test mode and the key switched to live, or the
customer deleted). It used to block the account permanently; the customer is
now verified and recreated as soon as a write goes through, and reads return an
empty list instead of raising.

| Action | Before | Now |
|---|---|---|
| `start_trial` | `"Erreur interne"`, permanently | Works (customer recreated) — and sends the trial invoice |
| `cancel_subscription` | `"Erreur interne"` | `"Aucun abonnement Stripe en cours pour cet utilisateur"` |
| `refund` without `payment_intent_id` | `"Erreur interne"` | `"Aucun paiement remboursable trouvé pour cet utilisateur"` |

> ℹ️ `start_trial` still requires a non-empty `email`, even though it is only
> used when no Stripe customer is usable **and** no `users.email` exists. Keep
> sending it until that check moves.
