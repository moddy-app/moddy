# Session 2026-05-26 — Subscription System

## What was done

Implemented the full subscription system for Moddy, covering DB schema, Redis cache,
Pub/Sub listener, user-facing command, staff command, and documentation.

## Files modified / created

| File | Change |
|---|---|
| `db/migrations/subscription.sql` | **New** — idempotent SQL migration: adds `subscription_expires_at` + `subscription_tier` to `users`; creates `subscription_plans` and `subscription_servers` tables |
| `db/repositories/subscription.py` | **New** — read-only repository: `get_subscription(user_id)`, `get_subscription_servers(user_id)` |
| `db/base.py` | **Updated** — inherits `SubscriptionRepository` |
| `utils/subscription.py` | **New** — `get_subscription(bot, user_id)` and `is_subscribed(bot, user_id)` helpers; Redis-first, DB fallback, TTL aligned on `expires_at` |
| `bot.py` | **Updated** — `_listen_pubsub` now subscribes to `moddy:subscription:updates`; added `_handle_subscription_event` and `_send_subscription_dm` |
| `cogs/subscription.py` | **Rewritten** — full Components V2 view showing tier, expiry, Stripe link indicator, linked servers, and "Gérer mon abonnement" button |
| `cogs/staff_subscription.py` | **New** — `/staff subscription-info <user>` slash command with full data including `stripe_customer_id` and server `added_at` |
| `locales/fr.json` | **Updated** — added `commands.subscription.*` keys |
| `locales/en-US.json` | **Updated** — added `commands.subscription.*` keys |
| `docs/SUBSCRIPTION_SCHEMA.md` | **New** — DB schema, Redis contract, Pub/Sub contract, backend responsibilities |
| `CLAUDE.md` | **Updated** — project structure, utils list, docs index |

## Decisions made

- **Bot is read-only on subscription data** — only the backend writes subscription columns and `subscription_servers`. This avoids race conditions with Stripe webhooks.
- **Redis-first, lazy DB fallback** — no polling. The cache TTL is aligned to `expires_at` so it auto-expires when the subscription ends.
- **`moddy:subscription:updates` is a separate Pub/Sub channel** from `moddy:bot`. This keeps subscription events isolated and easy to trace.
- **DMs are Components V2** — follows the project-wide rule: no `discord.Embed()`.
- **`/staff subscription-info`** is a slash command group (`/staff <subcommand>`) using `app_commands.Group` as a `Cog` class variable, which is the clean discord.py pattern.
- **`stripe_customer_id` shown in staff view only** — the user command shows only a "linked" indicator, not the raw ID.

## Known issues / follow-ups

- The `db/migrations/subscription.sql` file references `servers(id)` as FK for `subscription_servers.server_id`. Verify the actual PK column name in the `servers` table before running the migration (it may be `guild_id`).
- The migration should be run manually by the backend team against the Railway DB.
- `is_subscribed()` / `get_subscription()` helpers take `bot` as first arg (not just the user_id). Callers must pass `self.bot`.
