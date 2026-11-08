# 2026-11-08 — Bump Reminder: server-profile banner on join

## What was done
- New optional `MiniBotSpec.guild_banner` (image path relative to the repo).
- `minibots/builtin.py::apply_guild_banner` sets it with
  `PATCH /guilds/{id}/members/@me` (per-server profile only — the global
  profile is never modified). Called from `MiniBotClient.on_guild_join`
  before the welcome DM. Never raises.
- The Bump Reminder mini-bot ships `assets/minibots/bump_reminder_banner.png`
  (1024×361 PNG, downloaded from the Discord CDN banner the user supplied).

## Files
- `minibots/spec.py`, `minibots/builtin.py`, `minibots/client.py`
- `assets/minibots/bump_reminder_banner.png`
- `tests/test_minibots.py` (`TestGuildBanner`)
- `docs/features/MINIBOTS.md`

## Decisions
- The image is committed instead of fetched from the CDN URL: a banner hash
  URL 404s as soon as the source banner changes.
- Data URI built once per process (cache), not per join.

## Follow-ups
- Servers the bot joined before this change do not get the banner (only
  `on_guild_join` applies it). A one-off backfill could be added if wanted.
