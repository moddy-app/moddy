# Session — 2026-05-09 — Token Detector: DB persistence, button cleanup, doc

## What was done

Follow-up to the 2026-05-08 token detector session. Three main changes:

### 1. Removed the "Change Password" button
Discord requires a captcha for `POST /auth/forgot` (hCaptcha enterprise, domain-locked to `discord.com`) — it cannot be solved programmatically or on a third-party page. The button was removed entirely. A "What to do now" section was added to the alert body with a direct link to `discord.com/settings/account`, and a `Change Password` link button was added to the action row.

### 2. DB persistence for alert metadata (buttons survive restart)
Previously, all action buttons showed "Action No Longer Available" after a bot restart because the in-memory Fernet cache was empty. Fix:

- **New table:** `token_alerts` — stores everything except the token itself (masked content, message coordinates, guild/channel/author metadata, button state).
- **New repository:** `db/repositories/token_alerts.py` — `save_token_alert`, `get_token_alert`, `update_token_alert_state`, `update_token_alert_dm`.
- **New helper:** `peek_alert_with_db_fallback(ck, bot)` — tries memory cache first, falls back to DB. All button callbacks now use this.
- After restart: Message Info and Delete Message work normally. Invalidate Token shows "Token Already Cleared" (correct — the token is gone) and directs the user to change their password.
- DB writes are fire-and-forget with `try/except` — a DB failure never blocks the alert flow.

### 3. Message and UX improvements
- Added "What to do now" bullet list in the alert body.
- Updated footer: `Moddy never asks for your password or token`.
- Added **Support Server** link button (`moddy.app/support`) to both user and bot alert views.
- Updated **Learn More** link to `https://docs.moddy.app/articles/token-detector` (new public doc, see below).
- Removed unused `SETTINGS` emoji import.
- Removed `pw_reset_sent` from the state dict (no longer needed).

### 4. Public-facing documentation
Created `docs/public/token-detector.md` — the page that `Learn More` links to. Covers: what tokens are, what Moddy does, action button descriptions, post-alert recommendations, privacy guarantees, and FAQ.

## Files modified

| File | Change |
|---|---|
| `cogs/token_detector.py` | Remove `UserResetPwButton`, add `peek_alert_with_db_fallback`, update all callbacks, update views/message/footer/links, save/update DB on alert creation |
| `db/repositories/token_alerts.py` | **New** — `TokenAlertRepository` |
| `db/base.py` | Import `TokenAlertRepository`, add to `ModdyDatabase`, create `token_alerts` table in `_init_tables` |
| `docs/public/token-detector.md` | **New** — public-facing doc for `Learn More` link |

## Decisions made

- **Token never in DB.** Only metadata is persisted. `get_token_alert` always returns `token: ""`. This is intentional and the correct security posture.
- **DB failures are non-fatal.** All `bot.db.*` calls in token_detector are wrapped in `try/except` with `logger.debug`. A broken DB connection must never prevent an alert from being delivered.
- **No captcha solving.** Discord's hCaptcha sitekey is domain-locked to `discord.com`. Neither programmatic solving (ToS violation, fragile) nor hosting the widget on our domain (rejected by hCaptcha) is viable. Redirect to Discord settings is the correct solution.
- **No table expiry / cleanup task added** — token_alert rows accumulate indefinitely. Volume is low (rare event). A cleanup cron can be added later if needed.
