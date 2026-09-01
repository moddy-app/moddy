# 2026-09-01 — Linked roles: the `moddy:staff` publication and three `/team` field commands

## What was done

### 1. The obligation: publishing on `moddy:staff`

The backend publishes `team` and `premium` per account to Discord, which then
assigns linked roles on its own. Premium was already covered (Stripe → backend);
the staff rank was not, because the bot is what writes `staff_permissions`.

`services/staff_events.py` adds `notify_staff_change(bot, user_id, event=…,
roles=…)` — fire-and-forget, never raises, publishes `staff_ranked` /
`staff_unranked` / `staff_updated` on `moddy:staff`. Wired into **every** place
that writes `staff_permissions`, always after the write and after the `TEAM`
attribute sync:

- `/manage rank` (ranked, or updated for an existing member)
- `/manage unrank` (unranked)
- `/manage staff`: the roles select (ranked / updated / unranked) and the Remove
  button (unranked)
- `bot.py` dev auto-assign at startup — **only when something actually
  changed**, so a boot does not republish for nothing.

### 2. `/team role`

Creates (or re-reports) the server's **Moddy Team** role: no permissions at all,
id remembered in `guilds.data.moddy_team.role_id`, name lookup as fallback.
Reports whether the linked-role requirement is in place
(`RoleTags.is_guild_connection()`) and prints the exact path an administrator
must click when it is not.

### 3. `/team access`

The staffer picks from a fixed 25-permission catalogue; a card is posted in the
channel, in the **server's** language; an administrator accepts or refuses.
Accepting does `role.permissions | requested` on the Moddy Team role — added,
never replaced, and never to an account.

### 4. `/team ticket`

A real ticket (same table, same buttons, same verbs) opened outside the server's
own ticket configuration, via the sentinel `panel_id = category_id =
"__moddy_staff__"` that `TicketService.resolve()` short-circuits on.

## Files

| File | Change |
|---|---|
| `services/staff_events.py` | **new** — the `moddy:staff` publication |
| `utils/moddy_team_role.py` | **new** — find / create / remember the role, linked-state check |
| `utils/team_access_views.py` | **new** — picker + request card + the grant |
| `staff/commands/team/team_role.py` | **new** — `/team role` |
| `staff/commands/team/access.py` | **new** — `/team access` |
| `staff/commands/team/ticket.py` | **new** — `/team ticket` |
| `tests/test_linked_roles.py` | **new** — 33 tests |
| `services/ticket_service.py` | staff-ticket sentinels, `staff_ticket_context`, `open_staff_ticket`, `resolve()` short-circuit |
| `staff/commands/manage/{rank,unrank,staff}.py`, `bot.py` | publish on `moddy:staff` after each write |
| `utils/persistent_views.py` | registers the two `/team access` views |
| `locales/*.json` (×5) | `staff.team.{role,access,ticket}.*` |
| `docs/LINKED_ROLES.md` | **new** — the full contract |
| `CLAUDE.md`, `docs/{STAFF_SYSTEM,STAFF_COMMANDS_FRAMEWORK,TICKETS,REDIS_COMMUNICATION,PERSISTENT_VIEWS}.md` | updated |

## Decisions and why

- **Publish after the write, never before.** The backend re-reads
  `staff_permissions`; publishing first would have it read the old state and
  leave the Discord role wrong until the 6 h resync. Asserted in the tests from
  the source itself, because getting it wrong is silent.
- **The bot creates the role but cannot link it.** Discord exposes no API for
  role-connection requirements — the REST payload for creating or editing a role
  has no field for them, they are set in *Server Settings → Roles → Links* by a
  human. So the commands end on instructions and can only *verify* the binding
  afterwards. This is a Discord limitation, not a shortcut.
- **`administrator` is not in the `/team access` catalogue at all**, and the
  bitfield that travels in the custom_id is re-filtered through the catalogue on
  the way out — a hand-edited id cannot smuggle it back in.
- **Pre-flight checks before the card is shown** (role exists, role below the
  bot's, `manage_roles`, and the bot holds every requested permission itself):
  an administrator must never accept something that then fails.
- **A staff ticket reuses the ticket system rather than copying it.** One
  sentinel and a `resolve()` short-circuit were enough for every verb, button,
  command and listener to work on it unchanged — including the channel-deleted
  cleanup.
- **The Moddy Team role is always on a staff ticket**, created on the spot when
  the server does not have one, rather than the staffer being sent away to run
  another command first (Jules, mid-session). When the role is not linked yet,
  the confirmation says so — the channel exists and the role is on it, but
  nobody *holds* the role until an administrator adds the requirement.
- **`/team access` has no `guild_id` option**, unlike the other two: the whole
  point is that an administrator is in the room to answer it.

## Known follow-ups

- The optional `/link` command (pointing a member at
  `https://api.moddy.app/linked-roles`) was **not** added. It is a public
  command, so it would need its name and description in all 32
  `locales/commands/*.json` files — worth doing on its own. The link is already
  a button on `/team role`.
- Nothing reads `discord_role_connections` bot-side, and nothing should: it
  carries users' OAuth2 tokens and belongs to the backend alone.
