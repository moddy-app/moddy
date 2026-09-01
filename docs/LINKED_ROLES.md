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
6. [`/team access` — asking for permissions](#team-access--asking-for-permissions)
7. [`/team ticket` — a ticket of our own](#team-ticket--a-ticket-of-our-own)
8. [Files](#files)
9. [Checking it works](#checking-it-works)
10. [The traps](#the-traps)

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

### Why the linking step is manual

**Discord exposes no API for role-connection requirements.** They are set in
*Server Settings → Roles → Links*, by a human, and the REST payload for creating
or editing a role has no field for them. The bot can create the role and
*verify* the binding afterwards (`discord.RoleTags.is_guild_connection`), never
perform it — which is why `/team role` ends on instructions rather than on a
done panel.

---

## `/team role` — creating it

```
/team role [guild_id]          @Moddy t.role [guild_id]
```

Defaults to the server it is run in. Creates the role **with no permissions at
all** (`discord.Permissions.none()`), stores its id, and prints:

- the role, its id, and how many permissions it currently holds;
- whether it is linked yet — `RoleTags.is_guild_connection()`;
- when it is not: the exact path an administrator has to click, plus a link to
  the account-linking page.

Run again at any time: it never creates a second role, it re-reports the state.
That is how you check a binding took.

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
- **Several backend workers get the same message**; a 15 s Redis lock means only
  one writes to Discord. Nothing to handle bot-side, but do not be surprised to
  see one push for an event received four times.
