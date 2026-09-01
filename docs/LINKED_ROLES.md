# Linked roles — the Moddy Team role, and how a server gives us access

> How Discord knows who is on the Moddy team, what the bot owes the backend for
> that to work, and the three staff commands built on top of it:
> `/team role`, `/team access`, `/team ticket`.

---

## Table of contents

1. [The pipeline in three sentences](#the-pipeline-in-three-sentences)
2. [The bot's only obligation: `moddy:staff`](#the-bots-only-obligation-moddystaff)
3. [What the bot must never do](#what-the-bot-must-never-do)
4. [The Moddy Team role](#the-moddy-team-role)
5. [`/team role` — creating it](#team-role--creating-it)
6. [`/team role_delete` — removing it](#team-role_delete--removing-it)
7. [`/team access` — asking for permissions](#team-access--asking-for-permissions)
8. [`/team ticket` — a ticket of our own](#team-ticket--a-ticket-of-our-own)
9. [Files](#files)
10. [Checking it works](#checking-it-works)
11. [The traps](#the-traps)

---

## The pipeline in three sentences

The backend publishes two booleans per account to Discord: `team` (on the Moddy
team) and `premium` (active subscription). Discord then assigns, on its own, the
roles servers configured against them. **The bot publishes nothing to Discord**
— it has exactly one obligation: telling the backend when the composition of the
team changes.

Premium is already covered: Stripe notifies the backend. The staff rank is not,
because the bot is what writes `staff_permissions` and the backend has no way of
learning it.

---

## The bot's only obligation: `moddy:staff`

Pub/Sub channel `moddy:staff`, bot → backend, in
[`services/staff_events.py`](../services/staff_events.py).

```python
await notify_staff_change(bot, user_id, event=EVENT_RANKED, roles=roles)
```

| `type` | When |
|---|---|
| `staff_ranked` | the account joins the team, or gains one more role |
| `staff_unranked` | the account leaves the team (no role left) |
| `staff_updated` | its roles change without its membership changing |

The backend does the same thing for all three (recompute and republish); they
exist so its logs stay readable. `staff_updated` alone would be correct in every
case.

### Where it is called

Every place that writes `staff_permissions`, **after** the write and after the
`TEAM` attribute sync — the two always go together:

| Call site | Event |
|---|---|
| `staff/commands/manage/rank.py` | `staff_ranked`, or `staff_updated` for an existing member |
| `staff/commands/manage/unrank.py` | `staff_unranked` |
| `staff/commands/manage/staff.py` (roles select) | `staff_ranked` / `staff_updated`, `staff_unranked` when the last role goes |
| `staff/commands/manage/staff.py` (Remove button) | `staff_unranked` |
| `bot.py` (dev auto-assign at startup) | `staff_ranked`, **only when something actually changed** |

Order matters: **the backend re-reads `staff_permissions` from the database**,
it does not trust the message. Publishing before the write would have it read
the old state, and the Discord role would stay wrong until the next 6 h resync.

That is also what makes the channel safe. `roles` is context for the logs and
nothing else; a forged message on `moddy:staff` cannot hand anybody a role — at
worst it triggers a republication that recomputes the truth.

Publishing is **fire-and-forget**: `notify_staff_change` never raises and never
fails a promotion because Redis hiccuped. A lost message is caught by the
resync.

---

## What the bot must never do

- **Never write the `discord_role_connections` table.** It carries users' OAuth2
  tokens and belongs to the backend alone, like `notification_delivery_attempts`
  or the `ai_*` tables. The bot neither reads nor writes it, and adds no column
  to it.
- **Never call Discord's metadata endpoint**
  (`PUT /users/@me/applications/{id}/role-connection`). It needs a *user* token,
  not the bot's, and only the backend has one.
- **Never register the metadata schema at startup.** The `PUT` replaces the
  whole schema: doing it automatically would wipe, for the length of a partial
  deploy, the keys servers configured their roles against.
- **Never assign the Discord role by hand.** Discord assigns it from the
  metadata; adding it ourselves would be a duplicate Discord removes on its next
  check.

---

## The Moddy Team role

One role per server, bound to the *Moddy Team* linked-role requirement.
Everything Moddy staff can do in somebody else's server goes through it, and
nothing goes anywhere else:

- `/team access` grants permissions **to that role only** — never to a staff
  member's account, never to a role the server uses for something else.
- `/team ticket` opens its channel **to that role only** (plus the server's own
  administrators, who bypass overwrites by Discord's own rule).

Which means a server takes our access back with one role edit, without us, and
a destitution reaches every server at once without anybody cleaning up.

Helpers: [`utils/moddy_team_role.py`](../utils/moddy_team_role.py). The role id
is remembered in `guilds.data.moddy_team.role_id`, so renaming it loses nothing;
the name lookup (`Moddy Team`) is only a fallback for a role created before the
bot knew about it. A stored id that no longer resolves is forgotten on the spot.

### Why a human has to do the linking

**No API attaches a linked-role requirement to a role.** The official one has no
field for it: the REST payload for creating or editing a role carries nothing of
the sort, and `role.tags.guild_connections` only *reports* the requirement,
read-only.

The Discord client does call something —
`PUT /guilds/{guild.id}/roles/{role.id}/connections/configuration`, documented by
[Discord Userdoccers][ud] and by nobody else. Moddy tried it. Discord answered:

```
HTTP 403 (Discord code 20001): Bots cannot use this endpoint
```

on the `GET` as well as the `PUT`. `20001` is not a missing permission — it is
the code Discord returns when an endpoint is closed to bot tokens outright. Nor
is there a way around it: no OAuth2 scope covers guild role configuration
(`role_connections.write` writes a *user's* metadata; `rpc.api` is not public),
and the only credential that works is a real user session, which is
self-botting. **The step belongs to a human, and that is final.**

[ud]: https://docs.discord.food/resources/guild#role-connection-configuration-object

### The thirty-second window

Since a human must do it, `/team role` lends the permission to the staffer who
ran it instead of sending them to find an administrator. See
[`services/team_link_session.py`](../services/team_link_session.py).

1. **Moddy Team** is pushed to position 1, the very bottom of the hierarchy.
2. A throwaway role, `Moddy Team — linking`, carrying **only** `manage_roles`,
   is created at position 2 — directly above it.
3. Every other role the staffer holds is taken off them and **written to
   `guilds.data.moddy_team.link_session` before the removal**.
4. They have `WINDOW_SECONDS` (30) to do the clicks themselves.
5. Whatever the outcome: roles back, throwaway role deleted, **Moddy Team**
   moved back under Moddy, stored session cleared.

Discord refuses to edit any role at or above your own highest one, so from
position 2 the staffer can reach exactly one role: the one they are there to
link. That containment is the whole reason for the positions.

#### Say what it is

This is a privilege escalation, and a deliberate one: **no administrator of the
server approves it**, unlike `/team access`. It is short, confined, reverted and
logged, but the honest sentence is that Moddy hands one of its own staff
`Manage Roles` in somebody else's server for thirty seconds.

Two limits follow, and neither is theoretical:

- **The hierarchy does not contain everything.** `manage_roles` is also *Manage
  Permissions* on a channel, and channel overwrites are not bounded by role
  position. Only the audit-log watch catches that, and an audit entry can arrive
  late or not at all — detection, not a guarantee.
- **Success means "a requirement exists", not "our requirement exists".**
  `guild_connections` is a boolean. Reading *which* requirement is on the role
  needs the same endpoint Discord closes to bots, so a role linked to another
  app's criterion is indistinguishable from a correct one. What protects us is
  that the person doing it is our own staff, not the check.

#### The watchdog

`cogs/team_link_events.py` feeds the window two gateway events:

| Event | What it does |
|---|---|
| `on_guild_role_update` | success — `guild_connections` appeared on the role |
| `on_audit_log_entry_create` | the staffer did something else → revert it, close the window |

Watched actions: `role_create`, `role_delete`, `role_update`,
`member_role_update`, `overwrite_create/update/delete`. **`role_update` on the
Moddy Team role is exempt** — that edit *is* the task, and cancelling on it
would make the feature cancel its own success. Reverts are best effort and
individually swallowed: a revert that fails must never stop the teardown.

The audit watch needs `View Audit Log`. Without it the window still runs, it
just has no watchdog.

#### If the process dies mid-window

The saved roles are in the database *before* they are removed, so
`recover_sessions()` — run on every `on_ready` — gives them back, deletes the
throwaway role and puts **Moddy Team** back. A staffer left stripped of every
role by a crash is the one outcome this feature must never produce.

#### Never grant the role

Nothing in this flow gives anybody **Moddy Team**. Discord assigns it from the
metadata; a manual grant is a duplicate Discord removes on its next check.
`_restorable()` filters it out of the restore explicitly, in case the staffer
already had it, and a test asserts that.


## `/team role` — creating it

```
/team role [guild_id]          @Moddy t.role [guild_id]
```

Defaults to the server it is run in. Creates the role **with no permissions at
all** (`discord.Permissions.none()`), stores its id, and prints:

- the role, its id, and how many permissions it currently holds;
- whether it is linked yet — `RoleTags.is_guild_connection()`.

When it is **not** linked, the command opens the thirty-second window described
above: it sends the click path *first* (thirty seconds is not long enough to
read instructions afterwards), runs the window, then reports what came of it —
`window_done`, `window_expired`, `window_cancelled` or `window_failed`, followed
by the manual path as a fallback.

The window is never started on a hope. `_blocker()` refuses it upfront, with its
own sentence on the card, when: the staffer is not a member of the guild
(`not_member`), owns it (`owner` — they already have everything), another window
is running (`busy`), Moddy lacks `Manage Roles` (`no_permission`), or the Moddy
Team role does not sit below Moddy's own (`no_room`). A window that fails
halfway leaves somebody without their roles, so it is never opened blind.

`no_room` is the only genuine floor, and it is deliberately narrow. The window
does **not** need two free positions to already exist: a new role is inserted at
position 1 and pushes everything above it up, so creating the throwaway role
produces the second slot by itself — a Moddy sitting directly above Moddy Team
ends up two above it. What cannot be produced is authority over a Moddy Team
role that is not below Moddy: Discord refuses to move it, and refuses to let the
bot raise its own role to get over it. Only a human can do that.

#### When the box cannot be closed

A staffer whose highest role sits **at or above Moddy's** used to be refused
outright. They are not any more: `removable_roles()` sets aside what Discord
allows and leaves the rest, and the window runs.

Be clear about what that costs. Those roles stay on them for the thirty seconds,
with everything they carry — so the window lends `Manage Roles` without
confining anybody to it, and the position trick protects nothing. The card names
the roles that stayed (`window_partial`) rather than implying a containment that
is not there.

Lending and setting aside are two separate requests for the same reason: the
first is what makes the window useful, the second is what makes it safe, and a
server where the second is impossible still gets the first. If the removal is
refused outright, the saved list is cleared — the teardown must never try to
give back roles it never took.

Almost nobody hits this in the real case: a staffer in a customer's server is a
guest with no roles, so below Moddy. It shows up when the staffer is also an
administrator of the server — that is, when they could have done the clicks
without borrowing anything.

`defer = True` on the command: the dispatcher writes a staff-log entry before
`execute` runs, and with the window on top the 3 s interaction budget is long
gone — without it Discord answers *Unknown interaction*.

Run again at any time: it never creates a second role, it re-reports the state.

---

## `/team role_delete` — removing it

```
/team role_delete [guild_id]      @Moddy t.role_delete [guild_id]
                                  @Moddy t.unrole [guild_id]
```

The whole undo, because everything hangs off the role: Discord drops the
linked-role requirement with it and takes it off everybody who held it.
`/team access` and `/team ticket` then find nothing to grant or to open a
channel for.

No confirmation dialog: the role carries no permissions of its own and
`/team role` recreates it in one command. It **does** refuse while a linking
window is running (`blocked_busy`) — deleting the role out from under one would
leave it putting back a role that no longer exists.

The stored id is forgotten (`remember_role(bot, guild_id, None)`), so the next
`/team role` creates a fresh one rather than pointing at a ghost.

---

## `/team access` — asking for permissions

```
/team access                   @Moddy t.access
```

Run it in the server concerned, in the channel where the conversation is
happening (a ticket, usually), with an administrator there to answer. There is
deliberately no `guild_id` option: the whole point is that somebody is in the
room.

1. The staffer picks what they need from a **fixed catalogue of 25 permissions**
   (`ACCESS_PERMISSIONS`). `administrator` is not on it and cannot be requested
   through this surface at all.
2. A card is posted in the channel — in the **server's** language — naming who
   is asking, what for, and that everything lands on the Moddy Team role.
3. The administrator clicks **Accept** or **Refuse**. Nothing happens until they
   do.
4. Accepting adds the permissions to the role: `role.permissions | requested`.
   **Added, never replaced** — an earlier accepted request keeps standing, and
   accepting never quietly drops what a previous one granted.

Refused before it is ever shown, so an administrator is never asked for
something that would then fail:

| Checked | Why |
|---|---|
| the Moddy Team role exists | there is nothing to grant to otherwise |
| the role sits below the bot's top role | Discord refuses the edit |
| Moddy holds `manage_roles` | same |
| Moddy holds every requested permission itself | Discord refuses to let a bot grant what it does not have |

The labels are the ones the server logs already translate
(`modules.logs.permissions.*`), so an administrator reads the same wording here
as in their own audit log.

**Authorization is re-derived on every click**: `Administrator` on
`interaction.user`, never carried by the view. A member who was an admin when
the card was posted and is not one now cannot answer it. The requested bitfield
travels in the custom_id and is re-filtered through the catalogue on the way
out, so a hand-edited id cannot smuggle `administrator` past the picker.

---

## `/team ticket` — a ticket of our own

```
/team ticket [reason] [guild_id]      @Moddy t.ticket [guild_id] [reason]
```

A channel to talk to a server's administrators, opened by us, **without going
through — or even needing — that server's ticket configuration**. It is a real
ticket underneath: a row in the `tickets` table, the same control message, the
same buttons, the same `/ticket` verbs. Closing, reopening, adding somebody,
opening a staff thread all work exactly as they do everywhere else (see
[TICKETS.md](TICKETS.md)).

What tells it apart is one sentinel pair, `panel_id = category_id =
`​`__moddy_staff__`:

- `TicketService.resolve()` short-circuits on it and builds the panel and
  category **on the spot** from the guild's Moddy Team role
  (`staff_ticket_context`), instead of reading them from the guild's config. A
  server that never enabled the Tickets module therefore has a working staff
  ticket, and an admin deleting a category mid-conversation cannot break one.
- The category grants `admin` to the Moddy Team role and to nothing else,
  carries `close` and `participants` as its buttons, and has the claim system
  off — claiming is a queue-management tool for a server's own support team, and
  a staff ticket already has exactly one team on it.

The channel is created **outside any Discord category**, so it never inherits
overwrites from a category picked for the server's own tickets. Its opener is
the staffer, who may not even be a member of the guild — they are then simply
absent from the overwrites, and the ticket reads the same either way. The server
owner and the Moddy Team role are pinged once, in a message that deletes itself.

**The Moddy Team role is always on a staff ticket** — without it the channel
would be readable by the server's administrators and by nobody on our side. A
server that does not have one yet gets it here (same creation as `/team role`,
no permissions) rather than the staffer being sent away to run another command
first. When the role is not bound to the linked-role requirement yet, the
confirmation says so: the channel exists and the role is on it, but nobody
*holds* the role until an administrator adds the requirement.

---

## Files

```
services/staff_events.py             The moddy:staff publication (the obligation).
utils/moddy_team_role.py             Find / create / remember the role, linked-state check.
utils/team_access_views.py           /team access: picker, request card, the grant itself.
staff/commands/team/team_role.py     /team role
staff/commands/team/access.py        /team access
staff/commands/team/ticket.py        /team ticket
services/ticket_service.py           open_staff_ticket + staff_ticket_context.
tests/test_linked_roles.py           Publication order, bitfield safety, category shape, i18n.
```

Keys live under `staff.team.role.*`, `staff.team.access.*` and
`staff.team.ticket.*` in the five locale files. The ticket card and the access
card are read by the **server**, so they follow the server language
(`utils/guild_language.py`); everything ephemeral follows the staffer.

---

## Checking it works

1. Backend deployed, metadata schema registered.
2. On a test server: Roles → new role → *Links* → *Add requirement* → Moddy →
   `Moddy Team` is true.
3. With a linked staff account: the role appears.
4. Take them off the staff with the bot → the role disappears **within
   seconds**. If it takes hours, `moddy:staff` was not published and the backend
   only caught up at the resync.
5. Backend side, `GET /staff/linked-roles/metrics` shows how many accounts are
   linked, the queue and the errors; its logs carry the `[staff-events]` prefix.

A member who has ever signed in to the **dashboard** is already linked — the
`role_connections.write` scope is asked for at the first login. The linking page
is only for the rest.

---

## The traps

- **Publishing before the database write** → the backend re-reads the old state.
  The most common one by far.
- **Forgetting the destitution.** That is the case that matters: nobody notices
  a role that was not granted, everybody notices a former member still wearing
  the badge.
- **Believing the Redis message carries the state.** It carries an id. Changing
  `roles` in the message changes nothing.
- **A member who never linked their account** will never get the role, whatever
  the bot does. The backend answers `absent` and moves on — that is normal, not
  something to investigate.
- **Assuming the binding is guaranteed.** It rides an undocumented route. Read
  the state line on the `/team role` card rather than the fact that the command
  answered.
- **Several backend workers get the same message**; a 15 s Redis lock means only
  one writes to Discord. Nothing to handle bot-side, but do not be surprised to
  see one push for an event received four times.
