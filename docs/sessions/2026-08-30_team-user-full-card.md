# 2026-08-30 — `/team user`: one full staff card for a user

## What was done

`/team user` was a thin Discord card (id, username, creation, attributes,
shared servers). It now answers the whole question "what does Moddy know about
this person", including the **email** and the Stripe customer stored on the
`users` row — data no command surfaced before.

`/team user <user>` (message: `t.user`, alias `t.whois`) renders one
Components V2 panel:

| Section | Content |
|---|---|
| Discord identity | id, username, bot flag, account creation, shared servers |
| Moddy account | first seen, last update + **email**, Stripe customer id, stored time zone *(node-gated)* |
| Subscription | active/inactive + tier, expiry, linked servers (max 5 listed) |
| Moddy staff *(only if staff)* | roles + badges, granted nodes, denied commands, staff since |
| Attributes | raw `users.attributes` |
| Moderation | global sanction level (+ soonest expiry), case counts (total / open) and how many servers |
| Notifications | how many recent ones, kind + date + uuid of the last |

The nine DB reads run concurrently (`asyncio.gather`) and each one is wrapped
so a single failing table degrades one section instead of the whole panel.

## Decisions

- **One command, not two.** A separate `/team account` was drafted first; it
  was folded into `/team user` — a staffer looking someone up should not have
  to know which of two commands holds the field they need.
- **The gate is on the section, not on the command.** `/team user` stays open
  to every staff member as it always was; email, Stripe customer and stored
  preferences render only for a caller holding the new `user_lookup` node
  (Support and Manager; Dev/super-admin bypass nodes as always). Without it
  the section states which node is missing. The node is re-derived from the
  caller on every invocation via `has_staff_node`, and fails closed.
- Every invocation is audit-logged by the dispatcher, and the panel footer
  states the data is personal whenever the sensitive section is shown.
- **No interactive components**, so nothing to make persistent (rule #8): the
  panel is a pure read-only render.
- Names render through `staff.framework.badges` (rule #7 — verification badge
  on every displayed name), and all text goes through i18n in the 5 locales.

## Files

- `staff/commands/team/user.py` — rewritten (sections + personal-data gate)
- `utils/staff_role_permissions.py` — `user_lookup` node + label (Support, Manager)
- `locales/{fr,en-US,es-ES,pt-BR,de}.json` — new `staff.team.user.*` keys
- `tests/test_staff_user_command.py` *(new)* — sections, gate on/off, degraded
  reads, i18n on the 5 locales
- `docs/STAFF_SYSTEM.md`, `docs/STAFF_COMMANDS_FRAMEWORK.md`, `CLAUDE.md` — docs

## Follow-ups

- `users.email` is only written when a Stripe customer is created
  (`/manage stripecreate`, `/manage stripe trial`), so most accounts show
  "no email on file". If the dashboard ever collects an email at login, that
  section becomes far more useful — nothing to change in the command.
- `/team subscription` is now a strict subset of this card. Worth removing
  once staff habits have moved over.
