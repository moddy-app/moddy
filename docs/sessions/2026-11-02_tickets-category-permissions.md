# 2026-11-02 — Tickets: destination category permissions (Top.gg review)

## Context
Top.gg declined Moddy a third time: "Ticket panel > Button to create ticket
requires ADMINISTRATOR". The reviewer gave Manage Channels + Manage Roles,
then a role with everything but Administrator; Moddy kept answering that it
was missing `manage_channels` / `manage_roles`. Only Administrator worked.

## Findings
- The reviewer's ticket category (pre-existing on their server) had its own
  overwrites. Without Administrator, a category's overwrites take precedence
  over the bot's server-wide permissions; a category hidden from `@everyone`
  strips everything from the bot there.
- Verified on a real server (bot with Manage Channels + Manage Roles
  server-wide, no Administrator, category hidden from `@everyone`): Discord
  refuses the channel create with `403 50013`. The requirement is Discord's,
  not Moddy's — and it is "allow Moddy's role on the category", never
  Administrator.
- Our error said "Grant them on the destination category (or server-wide)":
  the server-wide half could never work, which is what led the reviewer to
  conclude Administrator was required.
- Separately, overwrites were clamped to the bot's permissions in the
  category only, while Discord checks a create against guild-level
  permissions: a category-only grant (e.g. `manage_messages`) could be
  handed to staff and fail the create with 50013.

## Changes
- `services/ticket_service.py`:
  - `grantable_permissions()`: overwrites are clamped to what the bot holds
    at guild level AND in the category.
  - `destination_blockers()`: core permissions the bot lacks in a destination.
  - Errors name the category (`missing_bot_permissions_category`,
    `cannot_create_channel_category`); a refused create is logged with
    Discord's code and text.
- `modules/configs/tickets_category_config.py`: warning under the destination
  as soon as the picked category blocks Moddy.
- `locales/*.json` (5 locales): new keys, "server-wide" hint removed.
- `tests/test_ticket_permissions.py`: clamp, blockers, messages.
- `docs/features/TICKETS.md`: bot permissions section.

## Follow-ups
- `/pwned` asks for a password; Top.gg's guidelines forbid asking for login
  information. It was hidden at runtime for the review; a durable decision
  (hash-only input, or removal) is still open.
- `tests/test_translate_button.py::TestDefaultEnabled::test_runs_where_nothing_is_stored_and_can_be_paused`
  fails on the base branch as well (unrelated).
