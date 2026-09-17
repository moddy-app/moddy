# 2026-09-17 — Tickets: make channel auto-delete-on-close optional

## What was asked

Following the same-day change that made closing a ticket always delete its
channel (removing Reopen/manual Delete entirely), the user asked to bring
"reopen" back as an **option** rather than gone for good.

## What changed

`modules/tickets.py`:

- New module-wide setting `SETTING_KEEP_CHANNEL = "keep_channel_on_close"`,
  off by default (auto-delete stays the default behaviour). Added to
  `SETTING_SWITCHES` (now four, not three) and `DEFAULT_SETTINGS`, parsed in
  `normalize_settings()` — picked up automatically by the `/config` → Tickets
  → Settings checkbox modal and status screen, which already iterate
  `SETTING_SWITCHES` generically.

`services/ticket_service.py::close_ticket()`:

- Branches on `settings.get(SETTING_KEEP_CHANNEL)`:
  - **Off (default):** unchanged from the earlier auto-delete change —
    archiving spinner (if transcripts on) → capture → DM (carrying
    `close_message`) → log → delete DB row → delete channel.
  - **On:** the pre-existing behaviour restored — capture the transcript,
    post the closing card (`TicketClosedView`, Reopen/Delete buttons,
    carrying `close_message` itself) in-channel, DM the opener (without
    `close_message`, since the card already has it), log, and leave the
    channel and DB row alone.
- Restored `reopen_ticket()` and the admin-only `delete_ticket()` service
  methods, both only reachable from the closing card's buttons — i.e. only
  when `keep_channel_on_close` is on.

`utils/ticket_views.py`:

- Restored `TicketClosedView` / `build_closed_message` / `build_reopen_dm`
  and their custom ids — but re-skinned to match the earlier restyle: blue
  accent + `TICKET` icon instead of red + `TICKET_CLOSE`.
- `_close_done_description()` — new helper picking between
  `close.done_description` (deleted) and `close.done_description_kept`
  (channel kept) based on the guild's setting, used by all three
  "ticket closed" ephemeral confirmations (`close_and_offer_rating`,
  `TicketOwnerLeftView.on_close`, `TicketCloseRequestView.on_accept`).

`cogs/tickets.py`: restored `/ticket reopen`.

`utils/persistent_views.py`: restored `TicketClosedView` in the persistent
view registry.

`locales/*.json` (all 5): restored `reopen.*`, `delete.*`,
`actions.reopen`/`actions.delete`, `errors.not_closed`, `close.card_title`,
`close.by`; added `close.done_description_kept`,
`settings.keep_channel_on_close_label`/`_hint`; reworded
`messages.close_hint` to name both destinations (DM by default, card if
kept).

`tests/test_tickets.py`: restored `test_closing_card_offers_reopen_and_delete`
alongside the new `test_archiving_card_has_no_buttons`; restored
`"reopen"`/`"delete"` in the i18n-completeness scanner's
`_INTERPOLATED_KEYS`, and added the two `close.done_description*` keys there
too (picked from a variable, not a literal `t(...)` call the regex scan can
see).

`tests/test_ticket_transcripts.py`: the `_service()` test helper now stubs
`service.setting` as an `AsyncMock` (was missing — `_close_done_description`
awaits it).

`docs/TICKETS.md`, `docs/TICKETS_INTEGRATION.md`, `docs/PERSISTENT_VIEWS.md`:
updated to describe both paths.

## Decisions made and why

- Kept auto-delete as the **default** — the user asked to make reopen
  optional, not to revert the default behaviour.
- `close_message` goes to the closing card when the channel is kept (its
  original home) and to the DM only when it isn't (its only remaining
  audience) — never both, to avoid showing it twice.

## Known issues / follow-ups

- None — full suite green (1954 passed).
