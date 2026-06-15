# 2026-06-14 — Social Notifications: fully customizable messages

## What was done

Made the Social Notifications message **fully customizable** and reworked the
notification rendering + config UI (Modals V2). This builds on the
2026-06-14 rebuild — same architecture (table-backed, `moddy-feeds` over Redis),
targeted improvements only.

### Notification rendering (`modules/social_notifications.py`)
- Role pings now render **outside** the container, **above** it (top-level
  `TextDisplay`), instead of inside.
- The container holds **only the user's message** (their custom template or the
  platform default) — no bot-authored type label / author / title / preview.
- Author avatar (pp) is shown as a **Section thumbnail** beside the text (same
  integration as `/user`), gated by `show_avatar` + platform support.
- Large media/cover shown as a `MediaGallery` at the bottom, gated by
  `show_media` + platform support.
- Accent colour = subscription `embed_color` or the platform brand colour.
- New `{timestamp}` placeholder (unix epoch from the event, falls back to
  dispatch time) — meant to be wrapped as `<t:{timestamp}:R>`; included in the
  per-platform default templates.
- New per-platform English default templates (`DEFAULT_MESSAGES`), title in `##`
  with the platform emoji. Per-platform placeholder availability
  (`PLATFORM_PLACEHOLDERS`) + `supports_avatar` / `supports_media` /
  `platform_color` helpers.

### Config UI (`modules/configs/social_notifications_config.py`)
- **Add** flow now uses **two Modals V2**: `AccountModal` (handle/URL) and
  `MessageCustomizationModal` (message + hex colour + display checkboxes).
- **Manage** edit opens the same `MessageCustomizationModal`, pre-filled with the
  current message as the **default value** (not a placeholder).
- The modal shows a placeholder cheat-sheet under the message field and a hex
  colour field defaulting to the platform colour; checkboxes (`CheckboxGroup`,
  discord.py 2.7) adapt per platform (Bluesky = avatar only, RSS = none).
- Pause/Resume button now uses the `PAUSE` / `PLAY` emojis.
- Persistence: every component has a stable namespaced `custom_id`, views are
  `timeout=None`. The **main panel** is registered as a persistent shell
  (`SocialNotificationsConfigView`, added to `utils/persistent_views.py`); auth
  switched from "command invoker only" to **Manage Server** so callbacks can
  re-derive everything from `interaction` after a restart.

### Data / contract
- New columns on `social_subscriptions`: `embed_color INTEGER`,
  `show_avatar BOOLEAN DEFAULT TRUE`, `show_media BOOLEAN DEFAULT TRUE`
  (`db/base.py` create + `ADD COLUMN IF NOT EXISTS` migration).
- `db/repositories/social.py`, `cogs/social_notifications.py` (incl.
  `handle_backend_task`) thread the new fields through.
- `requirements.txt`: `discord.py` 2.6.3 → **2.7.1** (needed for modal
  `Checkbox`/`CheckboxGroup`).

## Files modified
- `requirements.txt`, `utils/emojis.py` (PAUSE/PLAY), `db/base.py`,
  `db/repositories/social.py`, `cogs/social_notifications.py`,
  `modules/social_notifications.py`, `modules/configs/social_notifications_config.py`,
  `utils/persistent_views.py`, `locales/{en-US,fr}.json`,
  `CLAUDE.md` (always-persist rule), `docs/SOCIAL_NOTIFICATIONS.md`.

## ⚠️ Backend integration (schema change to mirror)
`social_subscriptions` gained `embed_color`, `show_avatar`, `show_media`. The
`message` semantics changed: it now contains the **title** (markdown heading)
and `NULL` means the **platform default template** (no longer a localized
caption). Backend Option A (`moddy:tasks`) passthrough fields added:
`embed_color`, `show_avatar`, `show_media` on `social_subscribe` / `social_update`.

## Addendum — fixes (same session)
- **Premium detection fixed:** the quota used `has_attribute('guild', …, 'PREMIUM')`
  which is never set. Premium is subscription-based — added
  `db.is_guild_premium(guild_id)` (`subscription_servers` JOIN `users` on
  `user_id`, active tier check) and used it everywhere (cog + config). Doc
  corrected (no per-guild `PREMIUM` attribute).
- **Identifier normalization:** `normalize_identifier(platform, raw)` turns a
  pasted profile URL into a clean handle (`youtube.com/@x` → `@x`, `twitch.tv/x`
  → `x`, `bsky.app/profile/x` → `x`; RSS kept verbatim). Applied in the account
  modal and in `add_subscription` (idempotent, covers backend tasks).
- **/subscription:** linked servers now show **name + (`id`)** instead of just
  the id (`cogs/subscription.py`).

## Known follow-ups
- Could not runtime-test the discord UI here (discord.py not installed in the
  session env); Modals V2 / Checkbox usage follows `docs/MODALS_V2.md`.
- Restart-persistence covers the **main panel** (the durable entry point). The
  Add/Manage sub-panels are `timeout=None` (never expire during a run) and are
  always re-entered from the main panel; fully persisting their mid-flow state
  across a restart would need `DynamicItem` groundwork (not present in the
  codebase) — left as a follow-up if desired.
- Platform emojis for pause/play assume the ids provided are uploaded.
