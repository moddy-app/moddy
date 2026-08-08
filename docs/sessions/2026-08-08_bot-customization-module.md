# 2026-08-08 — Bot Customization module

## What was done

New server module **`bot_customization`**: a server can make Moddy look like
its own bot, per-guild.

- **Premium** (guild covered by an active subscription): nickname, avatar, bio.
  The bio always keeps a trailing `<a:Rocket:…> Powered by @**Moddy**` line that
  the server cannot remove.
- **Free**: the name style (font, effect, colours) applied to the bot's display
  name, via the undocumented `display_name_font_id` / `display_name_effect_id` /
  `display_name_colors` fields.

Everything goes through `PATCH /guilds/{guild_id}/members/@me` with an
`X-Audit-Log-Reason`, and every change — successful or failed — emits a
technical log.

Also documented the **premium system**, which had no doc: it is
`subscription_servers` ⨝ an active subscription, *not* a guild attribute. Added
a cached `utils.subscription.is_guild_premium` helper and wired the existing
`premium_activated` / `premium_deactivated` Pub/Sub events to invalidate it
(they previously only logged).

## Files

**New**
- `modules/bot_customization.py` — module, validation, single write path, backend task handler
- `modules/configs/bot_customization_config.py` — `/config` panel + two Modals V2
- `tests/test_bot_customization.py` — validation tests (22)
- `docs/BOT_CUSTOMIZATION.md`, `docs/PREMIUM.md`

**Modified**
- `bot.py` — `bot_customization_update` task on the `moddy:tasks` stream + guild premium cache invalidation
- `cogs/config.py` — module routed in `/config`
- `config.py`, `utils/tech_logger.py` — new `bot_customization` log category + `log_bot_customization`
- `utils/subscription.py` — `is_guild_premium`, `invalidate_guild_cache`
- `utils/emojis.py` — `ROCKET`
- `utils/persistent_views.py` — `BotCustomizationConfigView` registered
- `locales/{fr,en-US,es-ES,pt-BR,de}.json` — full `modules.bot_customization` block
- `CLAUDE.md`, `docs/TECHNICAL_LOGS.md`

## Decisions

- **No draft/save cycle in the UI.** Every other module panel has
  Save/Cancel/Delete, this one has Edit/Reset. A modal submit patches Discord
  immediately and the change is instantly visible in the member list — a
  pending "Save" button would be lying about the state.
- **Single write path.** `apply_customization()` is the only place that
  validates, patches, persists and logs, so `/config` and the dashboard cannot
  drift apart. `UNSET` sentinel = leave alone, `None` = reset.
- **The avatar image is never stored**, only the hash Discord returns (used to
  build the CDN preview URL). Storing multi-MB base64 in `guilds.data` would be
  a bad idea, and attachment CDN URLs expire.
- **Only the style is re-applied on startup.** Nickname, avatar and bio are
  stored by Discord; the name style is not guaranteed to survive a restart, so
  `resync_style()` re-applies it once per process per guild.
- **Only the user portion of the bio is stored**, so the attribution suffix is
  never duplicated when a server re-edits its bio. `MAX_BIO_LENGTH` (137) is
  derived from the 190-char limit minus the suffix, and a test pins the
  arithmetic so a longer attribution text fails loudly.
- **Premium is re-checked on the modal submit**, not just when rendering the
  panel or clicking the button — a panel can sit open across a subscription
  lapse. Same on every dashboard task: the backend's check is a UX filter, not
  a trust boundary.
- **Locked, not hidden**, for non-premium servers: the identity section renders
  with a lock note and a dashboard link.

## Follow-ups

- **Banner** is supported by the endpoint and would be a two-line change in the
  write path, but was out of the requested scope.
- **No automatic revert on premium loss**: the stored identity stays applied and
  the panel just locks. A backend sweep on `premium_deactivated` would be needed.
- The 190-character member-bio limit is assumed from Discord's user-bio limit;
  worth confirming empirically for bot members.
- Backend still has to implement its half of the Redis contract (task producer +
  result consumer) — spec delivered separately, mirrored in `docs/BOT_CUSTOMIZATION.md`.
