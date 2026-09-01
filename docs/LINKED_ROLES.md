# Linked roles — the Moddy Team roles, and how a server gives us access

> How Discord knows who is on the Moddy team, what the bot owes the backend for
> that to work, and the three staff commands built on top of it:
> `/team role`, `/team access`, `/team ticket`.

---

## Table of contents

1. [The pipeline in three sentences](#the-pipeline-in-three-sentences)
2. [The bot's only obligation: `moddy:staff`](#the-bots-only-obligation-moddystaff)
3. [What the bot must never do](#what-the-bot-must-never-do)
4. [The Moddy Team roles](#the-moddy-team-roles)
5. [`/team role` — creating them](#team-role--creating-them)
6. [`/team role_delete` — removing them](#team-role_delete--removing-them)
7. [`/team see` — opening one channel](#team-see--opening-one-channel)
8. [`/team access` — asking for permissions](#team-access--asking-for-permissions)
9. [`/team ticket` — a ticket of our own](#team-ticket--a-ticket-of-our-own)
10. [Files](#files)
11. [Checking it works](#checking-it-works)
12. [The traps](#the-traps)

---

## The pipeline in three sentences

The backend publishes three booleans per account to Discord: `team` (on the
Moddy team), `manager` (leads it) and `premium` (active subscription). Discord
then assigns, on its own, the
roles servers configured against them. **The bot publishes nothing to Discord**
— it has exactly one obligation: telling the backend when the composition of the
team changes.

Premium is already covered: Stripe notifies the backend. The staff rank is not,
because the bot is what writes `staff_permissions` and the backend has no way of
learning it. `team` and `manager` both come out of that same table, so the one
`moddy:staff` message the bot sends covers them both — see
[The `manager` metadata](#the-manager-metadata).

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

## The Moddy Team roles

**Two** roles exist, and a server takes as many of them as it needs:

| Role | Metadata | Who Discord gives it to |
|---|---|---|
| **Moddy Team** | `team` | everybody on the team |
| **Moddy Team Manager** | `manager` | the accounts that lead it |

A manager holds **both**: `team` stays true for them, so anything granted to the
base role never has to be granted twice. Granting to the manager role is
therefore how a permission is kept to the people who lead the team rather than
handed to everybody on it.

**One role is the default.** `/team role` creates and links **Moddy Team** alone
unless it is told otherwise; `manager` and `both` are opt-ins. A server that
started with one role can come back for the second at any time — the two are
independent, and asking for one never disturbs the other.

Everything Moddy staff can do in somebody else's server goes through these
roles, and nothing goes anywhere else:

- `/team access` grants permissions **to one of them only** — the base role by
  default, the manager one on request; never to a staff member's account, never
  to a role the server uses for something else.
- `/team ticket` opens its channel **to them only** (plus the server's own
  administrators, who bypass overwrites by Discord's own rule). Both are put on
  the channel when both exist: it changes nothing for a manager, who holds the
  base role too, and it covers a server that only ever created the manager role.

Which means a server takes our access back with one role edit, without us, and
a destitution reaches every server at once without anybody cleaning up.

Helpers: [`utils/moddy_team_role.py`](../utils/moddy_team_role.py). Each role is
described by a `TeamRoleKind` carrying its name, its stored path and its
metadata key — the four differences in one object, so callers are written once
and a third role would be one entry. The ids live in
`guilds.data.moddy_team.role_id` and `.manager_role_id`, so renaming a role
loses nothing; the name lookup is only a fallback for a role created before the
bot knew about it. A stored id that no longer resolves is forgotten on the spot.

> **The name match is exact, and it has to be.** `Moddy Team Manager` contains
> `Moddy Team`. A `startswith` there would resolve the base role to the manager
> role in a server that has both, and `/team access` would then grant the team's
> permissions to the wrong one. A role already stored as the other kind is
> skipped for the same reason.

### The `manager` metadata

The bot **does not** publish it, read it, or register it — the standing rules
below are unchanged. It is listed here because it is the contract the backend
fills:

- Key: `manager`, boolean, alongside `team` and `premium` in the application's
  role-connection metadata schema.
- True for the accounts that lead the team; the bot's own hierarchy is in
  [`utils/staff_permissions.py`](../utils/staff_permissions.py) (`Dev` and
  `Manager` sit at the top).
- Computed from `staff_permissions`, the same table `team` comes from, so the
  existing `moddy:staff` publication already triggers its recomputation. **No
  new channel, no new message, no bot-side change.**
- `team` stays true for a manager. A schema where the two are exclusive would
  silently take the base role — and everything `/team access` granted to it —
  away from the people who lead the team.

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

### The linking window

Since a human must do it, `/team role` lends the permission to the staffer who
ran it instead of sending them to find an administrator. See
[`services/team_link_session.py`](../services/team_link_session.py).

1. The roles that still need a requirement are pushed to the bottom of the
   hierarchy — positions 1 and 2 when there are two of them.
2. A throwaway role, `Moddy Team — linking`, carrying **only** `manage_roles`,
   is created directly above them.
3. Every other role the staffer holds is taken off them and **written to
   `guilds.data.moddy_team.link_session` before the removal**.
4. They have `WINDOW_SECONDS` (75) to do the clicks themselves.
5. Whatever the outcome: roles back, throwaway role deleted, the linked roles
   moved back under Moddy, stored session cleared.

Discord refuses to edit any role at or above your own highest one, so from just
below the throwaway role the staffer can reach exactly the roles they are there
to link. That containment is the whole reason for the positions.

**One window covers every role**, rather than one window per role: a second pass
would mean stripping the same staffer of their roles twice in a row. It ends the
moment the *last* requirement appears, and the outcomes say how far it got:

| Outcome | Meaning |
|---|---|
| `done` | every role carries a requirement |
| `partial` | some do, some do not — run the command again; what is linked is not asked for twice |
| `expired` | the clock ran out with nothing linked |
| `cancelled` | the staffer did something outside the linking; it was reverted |
| `failed` | Discord refused a setup step; nothing was left dangling |

`WINDOW_SECONDS` went from 30 to 75 when the second role landed: seven clicks
was already tight for thirty seconds, and there can be fourteen now.

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
| `on_guild_role_update` | progress — `guild_connections` appeared on one of the roles; the window ends on the last one |
| `on_audit_log_entry_create` | the staffer did something else → revert it, close the window |

Watched actions: `role_create`, `role_delete`, `role_update`,
`member_role_update`, `overwrite_create/update/delete`. **`role_update` on any
role being linked is exempt** — that edit *is* the task, and cancelling on it
would make the feature cancel its own success. Reverts are best effort and
individually swallowed: a revert that fails must never stop the teardown.

The audit watch needs `View Audit Log`. Without it the window still runs, it
just has no watchdog.

#### If the process dies mid-window

The saved roles are in the database *before* they are removed, so
`recover_sessions()` — run on every `on_ready` — gives them back, deletes the
throwaway role and puts the linked roles back. A staffer left stripped of every
role by a crash is the one outcome this feature must never produce.

The stored payload carries `team_role_ids` (a list). A window interrupted by a
deploy that predates the second role stored a single `team_role_id`, and
recovery reads **both** shapes — anything else would leave that staffer without
their roles for good.

#### Never grant the roles

Nothing in this flow gives anybody **Moddy Team** or **Moddy Team Manager**.
Discord assigns them from the metadata; a manual grant is a duplicate Discord
removes on its next check. `_restorable()` filters both out of the restore
explicitly, in case the staffer already had one, and a test asserts that.


## `/team role` — creating them

```
/team role [guild_id] [team|manager|both]      @Moddy t.role [guild_id] [team|manager|both]
```

Defaults to the server it is run in, and to **`team` — one role**. Creates each
requested role **with no permissions at all** (`discord.Permissions.none()`),
stores its id, and prints, per role:

- the role, its id, its metadata key, and how many permissions it holds;
- whether it is linked yet — `RoleTags.is_guild_connection()`.

The message form takes the scope in either order (`t.role 123 manager` and
`t.role manager 123` are the same): a scope is a word from a three-item list and
a guild id is digits, so the two cannot be confused.

When a role is **not** linked, the command opens the window described above for
whichever ones are missing: it sends the click path *first* (the window is not
long enough to read instructions afterwards), runs it, then reports what came of
it — `window_done`, `window_partial`, `window_expired`, `window_cancelled` or
`window_failed`, followed by the manual path as a fallback. The instructions
name each role **with its requirement key**, since the two roles do not take the
same one.

Coming back for the second role later is the normal path, not an edge case:
`t.role <id> manager` in a server that already has the base role creates and
links only the manager one, and leaves the rest alone.

The window is never started on a hope. `_blocker()` refuses it upfront, with its
own sentence on the card, when: the staffer is not a member of the guild
(`not_member`), owns it (`owner` — they already have everything), another window
is running (`busy`), Moddy lacks `Manage Roles` (`no_permission`), or the Moddy
Team roles do not sit below Moddy's own (`no_room`). A window that fails
halfway leaves somebody without their roles, so it is never opened blind.

`no_room` is the only genuine floor, and it is deliberately narrow. The window
does **not** need two free positions to already exist: a new role is inserted at
position 1 and pushes everything above it up, so creating the throwaway role
produces the slots by itself — a Moddy sitting directly above Moddy Team ends
up two above it. What cannot be produced is authority over a role that is not
below Moddy: Discord refuses to move it, and refuses to let the bot raise its
own role to get over it. Only a human can do that.

#### When the box cannot be closed

A staffer whose highest role sits **at or above Moddy's** used to be refused
outright. They are not any more: `removable_roles()` sets aside what Discord
allows and leaves the rest, and the window runs.

Be clear about what that costs. Those roles stay on them for the length of the
window, with everything they carry — so it lends `Manage Roles` without
confining anybody to it, and the position trick protects nothing. The card names
the roles that stayed (`window_kept_roles`) rather than implying a containment
that is not there.

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

Run again at any time: it never creates a role that already exists, it
re-reports the state — and it is how a role linked in a window that only got
half way (`window_partial`) is finished off.

---

## `/team role_delete` — removing them

```
/team role_delete [guild_id] [team|manager|both]   @Moddy t.role_delete [guild_id] [team|manager|both]
                                                   @Moddy t.unrole [guild_id] [team|manager|both]
```

Takes the same scope as `/team role`, and the same default: **the base role
alone**. The whole undo, because everything hangs off the roles: Discord drops
the linked-role requirement with a role and takes it off everybody who held it.
`/team access` and `/team ticket` then find nothing to grant or to open a
channel for.

No confirmation dialog: the roles carry no permissions of their own and
`/team role` recreates them in one command. It **does** refuse while a linking
window is running (`blocked_busy`) — deleting a role out from under one would
leave it putting back a role that no longer exists.

With `both`, each id is forgotten right after its own deletion rather than at
the end: a refusal on the second role must not leave the first one remembered.

The stored id is forgotten (`remember_role(bot, guild_id, None, kind)`), so the
next `/team role` creates a fresh one rather than pointing at a ghost.

---

## `/team see` — opening one channel

```
/team see [grant|revoke]      @Moddy t.see [grant|revoke]
                              @Moddy t.channel [grant|revoke]
```

The narrow counterpart of `/team access`: that one grants guild-wide
permissions an administrator accepts; this one opens **the channel it is run
in** to the Moddy Team role, and nothing else. A staffer looking at a bug in one
channel needs their colleagues in that channel, not in the whole server.

The overwrite grants `view_channel`, `read_message_history`, `send_messages` and
`send_messages_in_threads` — see it, read what was said, take part. **Nothing
moderative**: managing messages or the channel stays a `/team access` decision.

**There is deliberately no `channel` option.** It acts on the current channel and
refuses when the staffer cannot see it themselves (`not_yours`) — you open a door
you are already standing in, so the command can never become a way of reaching a
channel you do not already have. Run in a thread it acts on the parent, since a
thread has no overwrites of its own.

The overwrite is written **even when the role can already reach the channel
through `@everyone`**. That access belongs to somebody else: the day `@everyone`
is closed on the channel, ours goes with it, silently, in the middle of whatever
the team was doing there. The explicit overwrite is what makes the access ours,
so the "nothing to do" case is the role's *own* overwrite already carrying the
four permissions — never its effective ones.

`revoke` **deletes** the overwrite rather than setting it to a denial: the
channel goes back to the exact state it had, instead of gaining an explicit
"Moddy Team cannot see this" that nobody asked for. With no overwrite of ours on
the channel it says `not_set` rather than reporting a removal that never
happened.

Pre-flight, each with its own sentence: Moddy needs `Manage Permissions` on the
channel (`no_permission`), the role must sit below Moddy's (`role_too_high`),
and Moddy must hold what it is granting (`moddy_cannot_see`) — Discord refuses
to grant, in an overwrite, a permission the actor does not have.

---

## `/team access` — asking for permissions

```
/team access [role]            @Moddy t.access [role]
```

Run it in the server concerned, in the channel where the conversation is
happening (a ticket, usually), with an administrator there to answer. There is
deliberately no `guild_id` option: the whole point is that somebody is in the
room.

`role` is `team` (the default) or `manager`. Since a manager holds both roles,
asking on the manager role is how a permission is kept to the people who lead
the team instead of going to everybody on it.

1. The staffer picks what they need from a **fixed catalogue of 25 permissions**
   (`ACCESS_PERMISSIONS`). `administrator` is not on it and cannot be requested
   through this surface at all.
2. A card is posted in the channel — in the **server's** language — naming who
   is asking, what for, and **which role** it lands on. The card always names the
   role: which of the two it is changes who ends up holding the permissions.
3. The administrator clicks **Accept** or **Refuse**. Nothing happens until they
   do.
4. Accepting adds the permissions to the role: `role.permissions | requested`.
   **Added, never replaced** — an earlier accepted request keeps standing, and
   accepting never quietly drops what a previous one granted.

Refused before it is ever shown, so an administrator is never asked for
something that would then fail:

| Checked | Why |
|---|---|
| the requested Moddy Team role exists | there is nothing to grant to otherwise |
| the role sits below the bot's top role | Discord refuses the edit |
| Moddy holds `manage_roles` | same |
| Moddy holds every requested permission itself | Discord refuses to let a bot grant what it does not have |

The labels are the ones the server logs already translate
(`modules.logs.permissions.*`), so an administrator reads the same wording here
as in their own audit log.

The role travels in the custom_ids alongside the bitfield, as an **optional**
segment: a card posted before the manager role existed has no third field, means
the base role, and stays answerable. Buttons that silently stop responding after
a deploy are not an acceptable way to ship a new option.

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
- The category grants `admin` to the Moddy Team roles and to nothing else,
  carries `close` and `participants` as its buttons, and has the claim system
  off — claiming is a queue-management tool for a server's own support team, and
  a staff ticket already has exactly one team on it.

The channel is created **outside any Discord category**, so it never inherits
overwrites from a category picked for the server's own tickets. Its opener is
the staffer, who may not even be a member of the guild — they are then simply
absent from the overwrites, and the ticket reads the same either way. The server
owner and the Moddy Team role are pinged once, in a message that deletes itself.

**A Moddy Team role is always on a staff ticket** — without one the channel
would be readable by the server's administrators and by nobody on our side. A
server that does not have the base role yet gets it here (same creation as
`/team role`, no permissions) rather than the staffer being sent away to run
another command first. **Both** roles go on the channel when both exist: that
changes nothing for a manager, who holds the base role too, and it covers a
server that only ever created the manager role. When a role is not bound to its
requirement yet, the confirmation says so: the channel exists and the role is on
it, but nobody *holds* the role until an administrator adds the requirement.

---

## Files

```
services/staff_events.py             The moddy:staff publication (the obligation).
utils/moddy_team_role.py             The two TeamRoleKinds; find / create / remember, linked-state check.
services/team_link_session.py        The linking window: containment, watchdog, teardown, recovery.
cogs/team_link_events.py             Gateway wiring for the window + the boot sweep.
utils/team_access_views.py           /team access: picker, request card, the grant itself.
staff/commands/team/team_role.py     /team role
staff/commands/team/role_delete.py   /team role_delete
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
   `team` is true (and a second role on `manager`, if the server wants it).
3. With a linked staff account: the role appears — both, for a manager.
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
- **Assuming the binding is guaranteed.** No API does it; a human clicks it in
  a borrowed window. Read the state line on the `/team role` card rather than the
  fact that the command answered.
- **Matching the role by name loosely.** `Moddy Team Manager` starts with
  `Moddy Team`. An `in` or a `startswith` there hands the base role's permissions
  to the manager role, in exactly the servers that have both.
- **Making `team` and `manager` exclusive backend-side.** A manager holds both.
  Flipping `team` off for them takes away everything `/team access` granted to
  the base role, in every server at once, with nothing bot-side to notice it.
- **Several backend workers get the same message**; a 15 s Redis lock means only
  one writes to Discord. Nothing to handle bot-side, but do not be surprised to
  see one push for an event received four times.
