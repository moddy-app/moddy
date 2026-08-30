# Session: Close requests point both ways

**Date:** 2026-08-30
**Agent:** Claude Code

## Summary

`/ticket close-request` used to be the member's action only: whoever could not
close asked the staff to, and someone holding `close` was refused ("you can
close it yourself"). That missed the case it is most often wanted for — the
**staff** proposing the closure to the person who opened the ticket instead of
ending the conversation under their nose.

The action is now **bidirectional**, one command and one button, with the
direction decided by who runs it:

- a member (`view`, no `close`) asks the staff to close — the roles holding
  `close` are rung and answer the card;
- a staffer (`close`) offers the closure to the opener — the opener is rung and
  answers.

Either way the card carries the same two buttons (**Close the ticket** /
**Keep it open**) and nobody is forced.

## Changes Made

- `db/base.py` — new `tickets.close_request_to_staff` column + idempotent
  migration next to the claim columns.
- `db/repositories/tickets.py` — the column in `_row_to_dict`, a `to_staff`
  argument on `set_close_request`, cleared on close like the rest of the
  request metadata.
- `services/ticket_service.py` — `request_close` now needs `view` again and
  derives the direction from the actor's permissions, rings the side that has
  to answer, and returns `(ticket, to_staff)`; new `accept_close_request` and
  `_may_answer_close_request`; `cancel_close_request` follows the same rule
  plus "you may withdraw your own"; `close_ticket` gained a
  `bypass_permission` flag used by that one caller.
- `utils/ticket_views.py` — the card is worded for the side that answers
  (`to_staff`), its accept button goes through `accept_close_request`, and the
  control bar reports which way the request went.
- `cogs/tickets.py` — same for the slash command, whose description is no
  longer staff-specific.
- `locales/{fr,en-US,es-ES,pt-BR,de}.json` — split wording per direction
  (`*_to_staff` / `*_to_member`), dropped the now impossible
  `errors.can_close_directly`, reworded the button hint.
- `locales/commands/*.json` (32) — the `ticket close-request` description.
- `tests/test_tickets.py` — direction/authorization tests and a card-wording
  test; the new i18n keys added to the interpolated-key list.
- `docs/TICKETS.md`, `docs/DATABASE.md` — the actions table, a new
  "A close request points both ways" section, the schema.

## Decisions & Rationale

- **The direction is stored, not recomputed.** A card is answered long after it
  is posted — the requester's roles may have changed, or they may have left the
  server. Deriving "who may answer" from live permissions would silently flip
  the card's meaning; `close_request_to_staff` freezes it.
- **A staffer may also answer a request pointed at the opener.** They could
  have closed the ticket outright, so refusing them the button they offered
  would be theatre.
- **The opener may not accept their own request to the staff.** Accepting is
  the *other* side's answer; the requester can only withdraw it (`refuse`).
- **`close_ticket(bypass_permission=True)` is deliberately narrow.** The opener
  holds no `close` permission, so `accept_close_request` does the checking and
  is the only caller allowed to set the flag — documented on both sides.
- The closing reason of an accepted request is the reason given when asking:
  the ticket log should say *why* it was closed, not stay empty because the
  person clicking had nothing to add.

## Follow-ups

None. `close_request` stays out of `DEFAULT_TICKET_BUTTONS`: it is a command
unless a server ticks the button back on.
