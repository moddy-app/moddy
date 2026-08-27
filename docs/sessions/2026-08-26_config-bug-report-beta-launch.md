# 2026-08-26 — `/config` refresh, support requests, beta launch messages

## What was done

### 1. `/config` opened up and redesigned
- **Removed the TEAM/BETA gate.** Anyone with **Manage Server** can now
  configure their server; the attribute check (and its `dev_only` error panel +
  i18n keys) is gone.
- **One dropdown.** The *Server settings* button was folded into the module
  select as its first entry (`SETTINGS_OPTION`): from the reader's side, the
  language Moddy speaks here is one more thing to configure, not a different
  kind of action. The select is capped at Discord's 25 options with the
  settings entry pinned first.
- **Accent bar** on the container (`COLORS["primary"]`), and **link buttons
  outside the container**: Dashboard, Support, Documentation.
- **Text rewritten** in the five content locales (title, description, hint,
  the settings entry's short description).

### 2. `/bug-report` (new, permanent)
Global slash command (servers **and** DMs), Modal V2 with three fields
(summary, what happened, how to reproduce). The report lands as a card in the
team's bug channel where staff can **Claim / Reply / Close**; the reply reaches
the reporter as a DM through the notification system, and they can reply back
from the DM. Localized in the 32 Discord command locales.

### 3. Support requests (new subsystem)
`/bug-report` and the "**Configure it for me**" button share one table, one
service, one card and one reply flow — `kind` (`bug` / `config_help`) is the
only branch.

- `db/repositories/support_requests.py` + two tables in `db/base.py`
  (`support_requests`, `support_request_messages`)
- `services/support_request_service.py` → `bot.support_requests`
- `utils/support_request_views.py` — card, reply DM, modals, five persistent
  dynamic items
- New staff node `support_request` (Support + Supervisor_Sup)
- Anti-spam: 3 requests of one kind per user per 10 minutes

### 4. Install welcome (new)
`on_guild_join` now DMs **the person who installed Moddy** (resolved from the
audit log, falling back to the owner): how to configure it (`/config`, the
dashboard), the free month of premium for the server (ticket on the support
server), the beta warning with `/bug-report`, and the *Configure it for me*
button.

### 5. Beta-launch campaign (temporary)
`utils/beta_announcement.py` + `/com beta`, marked temporary everywhere: they
exist for the launch and can be deleted together afterwards.
- Targets: `preview`, `test <user_id>` (one real send, every platform), `owners`
  (one DM per **owner**, naming all of their servers).
- The mass send is confirmed through a **Modal** where the sender types `SEND`.
- Platforms: Discord + dashboard + email (the backend serves the last two from
  the stored row).
- A **Translate** button re-renders the DM in the reader's own language, from
  the notification's stored template and variables.

### 6. Attribution: "Sent by **the Moddy Team**✓"
`notifications/render.py` now gives the two services that *are* Moddy
(`moddy`, `moddy_team`) the verification check on their attribution line, with
no database read — it is true by construction. `sent_by_service` gained a
`{badge}` placeholder in the five locales.

### 7. Bug fixed
`modules/welcome_dm.py::on_member_join` called `self._locale()` without
`await`, so a coroutine object was passed as the notification's `locale` and
every welcome DM failed to record (`asyncpg.DataError: expected str, got
coroutine`). One missing `await`.

## Files modified

**New:** `cogs/bug_report.py`, `services/support_request_service.py`,
`utils/support_request_views.py`, `utils/install_welcome.py`,
`utils/beta_announcement.py`, `staff/commands/com/beta.py`,
`db/repositories/support_requests.py`, `docs/SUPPORT_REQUESTS.md`,
`tests/test_support_requests.py`

**Changed:** `cogs/config.py`, `modules/welcome_dm.py`, `bot.py`, `config.py`,
`db/base.py`, `notifications/models.py`, `notifications/render.py`,
`utils/emojis.py`, `utils/persistent_views.py`,
`utils/staff_role_permissions.py`, the five `locales/*.json`, the 32
`locales/commands/*.json`, `CLAUDE.md`, `docs/DATABASE.md`,
`docs/NOTIFICATIONS.md`, `docs/PERSISTENT_VIEWS.md`, `docs/RAILWAY.md`,
`docs/STAFF_SYSTEM.md`, `docs/EMOJIS.md`

## Decisions

- **One table for both request kinds.** A bug report and a configuration-help
  request differ only in wording and destination channel; splitting them would
  have duplicated the card, the reply flow and five persistent buttons.
- **The install welcome goes to the installer, not to a channel.** They are the
  one who will set Moddy up; a card in a random channel reaches everyone else.
- **The "Configure it for me" button carries no owner id.** Whoever clicks it
  *is* the person asking, so there is nothing to authorize — the rare public
  dynamic item. The optional `:<guild_id>` suffix only prefills the modal.
- **The beta campaign records the notification before building the card**,
  because the Translate button needs the row's uuid in its `custom_id`.
- **No em dashes in user-facing strings** (explicit request): every new locale
  string uses commas or colons instead.

## Review pass (same session)

- `sent_by_moddy`: the article sits outside the bold ("Sent by the **Moddy
  Team**✓"), and `support` joined `OFFICIAL_SERVICES` so a team reply carries
  the check too. Service names are brand names now: *Moddy Team*, *Moddy
  Support*.
- The Translate button re-appends the attribution line
  (`NotificationService.attribution_line()` / `append_attribution()`) — a
  rebuilt card was dropping it — and is blue (`ButtonStyle.primary`).
- Commands inside a message are written `**\`/config\`**` via
  `config.command_label()`, replacing the `</config:id>` mention (a stale id
  renders as raw text). `MODDY_CONFIG_COMMAND_MENTION` is gone.
- References always render as code: `support.reply.reference` carries its own
  backticks.
- Link buttons carry no icon anywhere (`/config`, support cards, beta card,
  install welcome).
- Verified **servers**: the guild badge path was checked and is unchanged; a
  guild carrying `VERIFIED` / `VERIFIED_ORG` / `PARTNER` / `OFFICIAL` still gets
  its check, and a regression test now pins it.

## Follow-ups

- Delete `utils/beta_announcement.py`, `staff/commands/com/beta.py` and the
  `notifications.beta` / `staff.com.beta` i18n blocks once the campaign is over.
- The email and dashboard halves of the campaign depend on the backend serving
  `notification_deliveries` rows — worth confirming with a `test` send before
  the mass run.
