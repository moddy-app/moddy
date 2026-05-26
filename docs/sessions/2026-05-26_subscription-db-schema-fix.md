# Session: Subscription DB Schema Fix

**Date:** 2026-05-26

## What was done

Fixed two production errors that occurred when a user invoked `/subscription`:

1. `column "subscription_tier" does not exist` — the `users` table was missing the subscription columns.
2. `relation "subscription_servers" does not exist` — the `subscription_servers` table had never been created.

Root cause: `db/base.py::_init_tables()` contained `CREATE TABLE IF NOT EXISTS` statements for every table the bot uses, but the three subscription-related schema objects were never added there.

## Files modified

- `db/base.py` — added to `_init_tables()`:
  - Migration block to `ALTER TABLE users ADD COLUMN subscription_tier TEXT` and `subscription_expires_at TIMESTAMPTZ` (idempotent via `information_schema` check)
  - `CREATE TABLE IF NOT EXISTS subscription_plans (id TEXT PK, name TEXT, is_active BOOLEAN)`
  - Seed row `INSERT INTO subscription_plans ... ON CONFLICT DO NOTHING` for the `'max'` plan
  - `CREATE TABLE IF NOT EXISTS subscription_servers (user_id TEXT, server_id TEXT, added_at TIMESTAMPTZ, PK (user_id, server_id))`
  - Index on `subscription_servers(user_id)`

## Decisions

- Used the same idempotent migration pattern already present in the codebase (DO $$ BEGIN IF NOT EXISTS … END $$) rather than touching the existing `CREATE TABLE IF NOT EXISTS users` block, to avoid any risk of column-order issues on fresh installs.
- Tables match the schema specified in `docs/SUBSCRIPTION_SCHEMA.md` exactly.

## Known issues / follow-ups

None. The bot never writes subscription data; these are read-only tables managed by the backend.
