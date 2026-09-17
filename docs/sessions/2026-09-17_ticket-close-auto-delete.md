# 2026-09-17 — Tickets: closing auto-deletes the channel, closing cards restyled

## What was asked

1. The ticket closing/log card should use a blue accent and the module's own
   ticket icon in its title, instead of the red accent + plain cross it had.
2. Closing a ticket should immediately delete its channel, instead of
   leaving it open behind a "Reopen" / "Delete the channel" card. When
   transcripts are enabled, a spinner card announcing the archive capture
   should be posted first, since that step is the one slow enough that a
   silently-vanishing channel would read as broken.

Confirmed with the user before implementing: this removes ticket reopening
entirely (impossible without a channel) and the manual "Delete the channel"
button (now redundant, since deletion is automatic).

## What changed

`services/ticket_service.py`:

- `close_ticket()` no longer posts an in-channel closing card. New order:
  lock → (if transcripts enabled) post the archiving spinner card, then
  `archiver.capture()` → DM the opener → post the ticket-log card → stats →
  delete the `tickets` DB row → delete the Discord channel.
- `_notify_owner_closed()` gained a `body` param — the category's rendered
  `close_message`, now shown in the opener's DM (its only surviving
  audience; the in-channel card that used to show it is gone).
- Removed `reopen_ticket()` and the admin-only `delete_ticket()` — both were
  reachable only from UI the closing flow no longer offers.

`utils/ticket_views.py`:

- Removed `TicketClosedView` / `build_closed_message` (the Reopen/Delete
  card), `build_reopen_dm`, and their now-dead `_CID_CLOSED_REOPEN` /
  `_CID_CLOSED_DELETE` custom ids.
- New `build_archiving_message(locale)` — a non-interactive, non-persistent
  Components V2 card (blue accent, `LOADING` spinner icon) posted right
  before the archive capture runs.
- `build_close_dm` (opener's closing DM) and `build_ticket_log_card` (the
  permanent log-channel record — the only closing card left) both switched
  from red (`0xED4245`) + `TICKET_CLOSE` (a plain cross) to blue
  (`0x5865F2`, matches `COLORS["primary"]`) + `TICKET` (the module icon).
  `build_close_dm` also gained the `body` param to carry the category's
  `close_message`.

`utils/persistent_views.py`: dropped `TicketClosedView` from the persistent
view registry (import + collection list).

`cogs/tickets.py`: removed the `/ticket reopen` slash command — unreachable
now that closing deletes the channel it would have run in.

`locales/*.json` (all 5): removed `modules.tickets.reopen.*`,
`modules.tickets.delete.*`, `actions.reopen`, `actions.delete`,
`errors.not_closed`, `close.card_title`, `close.by`; added
`close.archiving_title` / `close.archiving_description`; reworded
`close.done_description` and `messages.close_hint` (no longer "the card
posted in the channel" — there isn't one anymore).

`docs/TICKETS.md`, `docs/TICKETS_INTEGRATION.md`, `docs/PERSISTENT_VIEWS.md`:
updated the closing/reopening description, the actions table, the
`close_message` placeholder doc, and the persistence surface table to match.

`tests/test_tickets.py`: replaced
`test_closing_card_offers_reopen_and_delete` with
`test_archiving_card_has_no_buttons`; dropped `"reopen"`/`"delete"` from the
static `_INTERPOLATED_KEYS` action list (the i18n-completeness scanner).

## Decisions made and why

- Kept `close_message` alive by moving it into the closing DM rather than
  dropping the config field's effect silently — it was the one piece of
  content that had nowhere left to go once the in-channel card disappeared.
- `bot.db.delete_ticket(channel.id)` runs *before* `channel.delete()`: the
  existing `cogs/tickets.py::on_guild_channel_delete` listener (which cleans
  up a ticket manually deleted from Discord's own UI) reads `get_ticket()`
  first and no-ops when the row is already gone, so the two paths don't
  double-delete or race.
- Did not touch `cogs/interserver_commands.py`-style survivor commands —
  N/A here — nor `set_ticket_status`'s `'open'` status support, since it's
  generic infra unrelated specifically to the removed reopen feature.

## Known issues / follow-ups

- None — full test suite green (1948 passed) after the change.
