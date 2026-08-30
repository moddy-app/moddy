# 2026-08-30 — `/team account`: staff lookup of a user's Moddy account

## What was done

Added a staff command that answers "what does Moddy know about this person",
including the **email** and the Stripe customer stored on the `users` row —
data no existing command surfaced.

`/team account <user>` (message: `t.account`, aliases `t.acc`, `t.whois`)
renders one Components V2 panel with:

| Section | Content |
|---|---|
| Discord identity | id, username, bot flag, account creation, shared servers |
| Moddy account | **email**, Stripe customer id, first seen, last update, stored time zone |
| Subscription | active/inactive + tier, expiry, linked servers (max 5 listed) |
| Moddy staff *(only if staff)* | roles + badges, granted nodes, denied commands, staff since |
| Attributes | raw `users.attributes` |
| Moderation | global sanction level (+ soonest expiry), case counts (total / open) and how many servers |
| Notifications | how many recent ones, kind + date + uuid of the last |

The nine DB reads run concurrently (`asyncio.gather`) and each one is wrapped
so a single failing table degrades one section instead of the whole panel.

## Decisions

- **New command rather than growing `/team user`.** `/team user` stays the
  quick "who is this Discord account" card available to every staffer; the
  account lookup carries personal data and needed its own gate.
- **Gated behind a new `user_lookup` node** (`utils/staff_role_permissions.py`),
  granted to Support and Manager (Dev/super-admin bypass nodes as always).
  `/team` is open to all staff, so the role check alone was not an acceptable
  guard for an email address. The invocation is audit-logged by the dispatcher
  like every staff command, and the panel footer states the data is personal.
- **No interactive components**, so nothing to make persistent (rule #8): the
  panel is a pure read-only render.
- Names render through `staff.framework.badges` (rule #7 — verification badge
  on every displayed name), and all text goes through i18n in the 5 locales.

## Files

- `staff/commands/team/account.py` *(new)* — the command + its section builders
- `utils/staff_role_permissions.py` — `user_lookup` node + label (Support, Manager)
- `locales/{fr,en-US,es-ES,pt-BR,de}.json` — `staff.team.account.*`
- `tests/test_staff_account.py` *(new)* — sections, degraded reads, node gating, i18n
- `docs/STAFF_SYSTEM.md`, `docs/STAFF_COMMANDS_FRAMEWORK.md`, `CLAUDE.md` — docs

## Follow-ups

- `users.email` is only written when a Stripe customer is created
  (`/manage stripecreate`, `/manage stripe trial`), so most accounts show
  "no email on file". If the dashboard ever collects an email at login, that
  section becomes far more useful — nothing to change in the command.
- If moderators end up needing the identity half without the billing half,
  split the node rather than widening `user_lookup`.
