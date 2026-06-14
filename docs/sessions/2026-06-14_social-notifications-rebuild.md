# 2026-06-14 — Social Notifications module rebuild

## What was done

Rebuilt the social-media notification feature from scratch as a single unified
**Social Notifications** module, backed by the external `moddy-feeds` service
over shared Redis (replacing the old YouTube-only WebSub implementation).

- **DB:** new `social_subscriptions` table + `db/repositories/social.py`
  (`SocialSubscriptionsRepository`). Indexed on `(platform, target_id)` for
  event dispatch and on `guild_id` for the config panel. Stores the canonical
  `target_id` resolved by the service.
- **Redis transport:** `services/feeds_client.py` (`FeedsClient`) — `XADD`
  `feeds:commands`, correlated replies on `feeds:replies` via per-`request_id`
  futures + a background reader, and a `discord-bot` consumer group on
  `notifications:queue` with `XACK`. Heartbeat check via `feeds:heartbeat`.
- **Module:** `modules/social_notifications.py` — platform catalogue, the
  premium/free poll-interval policy, and the Components V2 notification renderer
  (`build_notification_view`, with role pings + `AllowedMentions`).
- **Cog:** `cogs/social_notifications.py` — owns the feeds client, dispatches
  events to every follower, centralises subscribe/unsubscribe reconciliation,
  and cleans up on `on_guild_remove`.
- **Config UI:** `modules/configs/social_notifications_config.py` — live panel
  (no batched Save) following DESIGN.md: list → Add (platform/channel/roles/
  source) → Manage (channel/roles/message/pause/remove). Subscribe is the final
  committing step to avoid orphan targets.
- **Wiring:** `/config` routes to the new async factory; old YouTube route gone.
- **Emojis:** centralized platform constants in `utils/emojis.py`
  (`PLATFORM_EMOJIS`, `get_platform_emoji`) — currently placeholders.
- **i18n:** full `modules.social_notifications.*` keys (fr + en); old
  `modules.youtube_notifications.*` removed.
- **Docs:** `docs/SOCIAL_NOTIFICATIONS.md` + CLAUDE.md structure/index updates.

## Files removed
- `cogs/youtube_websub.py`
- `modules/youtube_notifications.py`
- `modules/configs/youtube_notifications_config.py`

## Decisions
- **Table over JSONB:** a target is shared by many guilds and events must fan
  out, so a dedicated indexed table is the scalable store. The `ModuleBase`
  shell exists only so the module shows up in `/config`.
- **Live config panel:** adding a target requires a Redis round-trip to resolve
  it, so actions apply immediately rather than via a Save button.
- **Poll interval from premium status** (not user-configurable): premium = the
  platform's fastest allowed, free = slower but reasonable. Values documented in
  SOCIAL_NOTIFICATIONS.md and `POLL_INTERVALS` — to be mirrored in the backend.

## Known follow-ups
- **Platform emojis are placeholders** (`utils/emojis.py`: `YOUTUBE`, `TWITCH`,
  `BLUESKY`, `RSS`, `INSTAGRAM`, `SOCIAL`). Replace the ids with the real custom
  emojis — single source of truth, propagates everywhere.
- Could not runtime-test discord UI here (discord.py not installed in the
  session env); API usage matches existing `bot.py` / `utils/components_v2.py`.
- Premium-status changes don't retroactively rewrite stored `poll_interval`
  (only applied on next subscribe). Acceptable for v1.
