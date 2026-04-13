# Session: Backend integration rewrite

**Date:** 2026-04-13
**Summary:** Complete rewrite of the bot ↔ backend communication layer.

---

## What was done

The backend was rebuilt from scratch with a new architecture. The old model (bot makes HTTP calls to backend) was replaced with a shared-database + Redis bus model.

### Architecture change

**Before:**
- Bot called backend via HTTP (`/internal/user/info`, `/internal/subscription/info`, etc.)
- Shared secret `INTERNAL_API_SECRET` + `BACKEND_INTERNAL_URL`
- `BackendClient` (httpx) + Railway diagnostic module

**After:**
- Bot and backend share the **same PostgreSQL database** directly
- Communication via **Redis**: Pub/Sub (`moddy:bot`) for non-critical events, Streams (`moddy:tasks`) for critical tasks
- Bot exposes `GET /status` (called by backend) and `GET /health` (Railway health check)
- No more outbound HTTP calls from bot to backend

---

## Files modified

### Deleted
- `services/backend_client.py` — HTTP client to backend (replaced by DB + Redis)
- `services/railway_diagnostic.py` — Railway DNS diagnostic (no longer needed)
- `schemas/internal.py` — Pydantic schemas for old HTTP API
- `internal_api/middleware/auth.py` — Auth middleware for old `/internal/*` routes
- `internal_api/routes/internal.py` — Old `/internal/notify`, `/internal/roles/update` routes
- `docs/INTERNAL_API.md` — Old HTTP API documentation
- `docs/BACKEND_INTEGRATION_STATUS.md` — Old integration diagnostic (outdated)
- `docs/endpoints/` — All individual endpoint spec files

### Rewritten
- `services/__init__.py` — Now empty (no more BackendClient exports)
- `internal_api/server.py` — Simplified: exposes `GET /health` and `GET /status`
- `internal_api/middleware/__init__.py` — Emptied
- `internal_api/routes/__init__.py` — Emptied
- `docs/RAILWAY.md` — Updated env vars (add REDIS_URL/PASSWORD, remove BACKEND_INTERNAL_URL)
- `cogs/subscription.py` — Now queries DB directly (PREMIUM attribute + stripe_customer_id)
- `staff/support_commands.py` — Removed `sup.invoices` and `sup.refund` (Stripe-only, dashboard only), `sup.subscription` now queries DB

### Updated
- `config.py` — Added `REDIS_URL`, `REDIS_PASSWORD`
- `bot.py`:
  - Added `self.redis` connection in `setup_hook`
  - Added `_listen_pubsub()` task (Pub/Sub `moddy:bot`)
  - Added `_consume_task_stream()` task (Stream `moddy:tasks`)
  - Added `_handle_bot_event()` and `_process_task()` handlers
  - Added Redis cache invalidation in `on_guild_join` / `on_guild_remove`
  - Replaced backend health check in `run_startup_checks()` with Redis check
  - Cleaned up `close()` (Redis instead of BackendClient)
- `requirements.txt` — Removed `httpx` (no longer needed)
- `CLAUDE.md` — Updated docs index
- `docs/AGENTS.md` — Updated docs reference

---

## Decisions

- **`sup.invoices` and `sup.refund` removed**: These require Stripe API access which the bot no longer has. Invoice/refund management is now dashboard-only. Kept `sup.subscription` showing the PREMIUM attribute from DB.
- **`/subscription` command simplified**: Shows Premium status (from `attributes`) + stripe_customer_id. Links to dashboard for billing details. No Stripe data needed.
- **INTERNAL_API_SECRET kept**: Still used (optionally) to protect the `/status` endpoint.
- **PORT used instead of INTERNAL_PORT**: Aligns with the new spec and Railway convention.

---

## Follow-ups

- The handlers in `_handle_bot_event()` and `_process_task()` are minimal stubs — expand as backend publishes more event types.
- `update_panel` stream task assumes `module_manager.reload_module(guild, module_id)` exists — verify when implementing dashboard panel updates.
- Consider adding `ensure_user` / `ensure_guild` upserts on `on_message` / `on_member_join` as specified in `BACKEND-INTEGRATION.md` section 13.
