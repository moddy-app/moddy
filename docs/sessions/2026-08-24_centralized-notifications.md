# Session: Centralized notification system

**Date:** 2026-08-24
**Agent:** Claude Code

## Summary

Every message Moddy sends to a human now goes through one system
(`bot.notifications`). A notification is stored, carries the identity of
whoever is behind it, and — when someone other than Moddy wrote the words —
can be reported by its recipient.

What shipped:

- **Uniform payload.** `NotificationContent` renders to Discord, to a mail
  shape and to a dashboard shape, so a suspension notice reads the same
  everywhere. Titles, body, sections, links, footer, icon, accent.
- **Attribution line** at the bottom of every non-official DM —
  `-# Sent by [**Server**](link) (`id`)`, plus the verification check when the
  server has one. Same shape the sanction DMs have always used, now on
  everything. (An earlier iteration in this session put buttons and an identity
  panel there instead; that was replaced by the line on request — see the
  decisions below.)
- **Abuse reports** posted to the staff review channel with Claim / See the
  message / Accept / Decline, every step mirrored to the report log channel, and
  the reporter told the outcome — through this same system.
- **Storage**: four tables. The wording is stored once per *template* (hashed
  before placeholder substitution), each notification storing its own
  `variables`, so ten thousand identical welcome DMs cost one body row and every
  one of them stays reproducible to the character.
- **Staff commands**: `/mod notif <uuid>` (everything about one notification or
  report) and `/com send` (one user, one server, or thousands of either).
- **Eleven senders migrated** off `user.send(...)`. The three whose card
  already printed its own `sent_by` line pass `attribution=False`.

## Changes Made

### New

- `notifications/models.py` — payload, source, enums, service registry, template
  hashing, placeholder substitution, mail/dashboard shapes
- `notifications/render.py` — payload → Components V2, attribution context
  (verified / official / reportable)
- `notifications/service.py` — `NotificationService`: record, deliver, mark,
  server notices, broadcasts, the whole report lifecycle
- `notifications/__init__.py` — public surface
- `utils/notification_views.py` — attribution row, identity panels, report Modal
  V2, staff review panel, report log card, six persistent `DynamicItem`s
- `db/repositories/notifications.py` — CRUD for the four tables
- `staff/commands/mod/notification.py` — `/mod notif`
- `staff/commands/com/send.py`, `staff/commands/com/_send_modal.py` — `/com send`
- `tests/test_notifications.py` — 39 tests (hashing, reproducibility,
  attribution rules, report authorization, i18n completeness on 5 locales)
- `docs/NOTIFICATIONS.md`
- `docs/NOTIFICATIONS_INTEGRATION.md` — backend/dashboard contract: tables read
  vs owned, exact payload rendering, the placeholder algorithm the backend must
  match character for character, the mail/dashboard delivery loop

### Modified

- `db/base.py` — DDL for `notification_contents`, `notifications`,
  `notification_deliveries`, `notification_reports` + indexes
- `db/repositories/users.py` — `get_all_user_ids()` (broadcast audience)
- `bot.py` — `self.notifications = NotificationService(self)`
- `config.py` — `MODDY_NOTIF_REPORT_CHANNEL_ID`, `MODDY_NOTIF_REPORT_LOG_CHANNEL_ID`
- `utils/persistent_views.py` — registers `NotificationsPersistence`
- `utils/staff_role_permissions.py` — nodes `notif_review`, `notif_lookup`
- Senders migrated: `modules/welcome_dm.py`, `modules/altguard.py`,
  `modules/interserver.py`, `modules/automod_ai.py`, `cogs/moderation_commands.py`,
  `cogs/reminder.py`, `cogs/token_detector.py`, `services/ticket_service.py`,
  `services/appeal_service.py`, `services/expiration_notifier.py`,
  `services/global_sanction_service.py`
- `locales/{fr,en-US,es-ES,pt-BR,de}.json` — `notifications.*`, `staff.notif.*`,
  `staff.com.*`
- `tests/test_persistent_views.py`, `tests/test_altguard.py`,
  `tests/test_expiration_notifications.py`, `tests/test_tickets.py`
- `CLAUDE.md` (rule #11), `docs/PERSISTENT_VIEWS.md`, `docs/DATABASE.md`,
  `docs/STAFF_COMMANDS_FRAMEWORK.md`, `docs/RAILWAY.md`, `docs/WELCOME_DM.md`

## Decisions & Rationale

- **Content is stored as a template, not as a finished message.** Hashing before
  substitution is what makes de-duplication real (one row per configured welcome
  DM, not one per member) while `variables` keeps each notification exactly
  reproducible. `str.format` was rejected for substitution: server text is
  arbitrary and a stray `{` must never raise mid-delivery.
- **Reportability is decided by who wrote the words, not by the message type.**
  `ContentAuthor.GUILD` → live flag; Moddy's own wording → greyed out, with the
  reason spelled out in the identity panel (a dead button with no explanation is
  a bug). Messages from `OFFICIAL` Moddy servers are never reportable.
- **`reportable` is frozen on the row**, and the runtime check can only make a
  notification *less* reportable. A server marked official later must not
  resurrect old flags.
- **Callers keep their own card.** Features with an established design (sanction
  DMs with appeal buttons, ticket cards, transcriptions) pass `view=`; the
  uniform content is still required because the dashboard, the mail pipeline and
  the staff preview all read it. A view-only message would be Discord-only.
- **Official notices carry no attribution at all** — a suspension is Moddy
  speaking as an institution; offering to "report" it is nonsense. They are
  still stored and counted.
- **A member's DM carries no component at all.** Attribution is plain text, so
  it cannot expire, lose its handler or need registering. This replaced an
  earlier button+panel design in the same session: a button can only carry an
  emoji (never a server icon), and one greyed line says everything the panel
  said that a member actually needs. The staff review panel keeps its
  `DynamicItem` buttons, keyed by the report uuid.
- **The Discord entry point for filing a report is gone with the buttons.**
  The pipeline behind it — `notification_reports`, the review panel, the
  outcome DM, `reportable` computed and frozen per row — is intact and
  reachable through `NotificationService.open_report()`. It is kept rather than
  deleted because the natural new trigger is a dashboard control, and
  everything downstream of it already works.
- **Broadcasts explode into one row per recipient** sharing a `batch_id`. Per-
  recipient delivery status is the whole point of the table, and "how did this
  campaign go" is still one query.
- **A database outage degrades to an unattributed DM**, never to a swallowed
  message. Recording is best-effort; delivery is not.

## Known Issues / Follow-ups

- [ ] The **mail** and **dashboard** platforms are declared, stored and rendered
      (`to_email()` / `to_dashboard()`) but nothing sends them: their delivery
      rows stay `pending` for the backend to pick up and mark. The contract is
      now written (`docs/NOTIFICATIONS_INTEGRATION.md`); the backend side is
      not built. Note that no bot caller opts into a non-Discord platform yet,
      so there are no pending rows to consume until one does.
- [ ] **The backend cannot trigger a send.** There is no `moddy:tasks` type for
      it, and it must not write `notifications` rows itself (the uuid has to
      exist before the DM goes out — the buttons carry it). A proposed
      `notification_send` payload is sketched in the integration doc §6. The
      legacy `send_announcement` task still posts unrecorded raw text to
      `guild.system_channel`.
- [ ] **Nothing can file a report today** — the flag button was removed and no
      other surface calls `open_report()` yet. A dashboard control is the
      obvious next step; it needs a bot-side task type, since filing also posts
      the review panel and logs it (see NOTIFICATIONS_INTEGRATION.md §5.2).
- [ ] Accepting a report records the decision and tells the reporter; it takes
      **no automatic action** against the server. Wiring it to the global
      sanction / case system is a deliberate next step, not an oversight.
- [ ] The recipient no longer sees the notification uuid anywhere in Discord.
      Support has to identify a message by user + date instead. Putting it back
      as a second `-#` line is one line of code if that turns out to hurt.
- [ ] `/com send` progress edits stop if the interaction token expires (15 min);
      the batch keeps running and stays queryable by `batch_id`. A long campaign
      would be better served by a status message in a staff channel.
- [ ] Recipient locale is the guild's (or the caller's), never the user's own —
      Moddy stores no per-user language preference yet.
