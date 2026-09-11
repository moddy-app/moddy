# Tickets — support tickets with panels, categories and per-role permissions

> Server module `tickets`. Configured in `/config` → **Tickets**, used through
> a **panel** message and the `/ticket` command group.

---

## Table of contents

1. [The three levels](#the-three-levels)
2. [Files](#files)
3. [Limits (free / premium)](#limits-free--premium)
4. [Configuration schema](#configuration-schema)
5. [The permission model](#the-permission-model)
6. [The claim system](#the-claim-system)
7. [Channel permissions, escalation and closure](#channel-permissions-escalation-and-closure)
8. [The ticket message](#the-ticket-message)
9. [Pings](#pings)
10. [The staff thread](#the-staff-thread)
11. [The `tickets` table](#the-tickets-table)
12. [Actions](#actions)
13. [Membership changes](#membership-changes)
14. [Module settings](#module-settings)
15. [Transcripts](#transcripts)
16. [Closure detection](#closure-detection)
17. [Ratings](#ratings)
18. [The ticket log](#the-ticket-log)
19. [Slash commands, published per guild](#slash-commands-published-per-guild)
20. [Persistence](#persistence)
21. [i18n](#i18n)
22. [Backend / dashboard contract](#backend--dashboard-contract)
23. [Extending the module](#extending-the-module)

---

## The three levels

Keeping these apart is what keeps the rest simple.

| Level | What it is | Where it lives |
|---|---|---|
| **Panel** | One message in a public channel, with its own title, description, colour, and either **buttons** or a **dropdown**. | `guilds.data.modules.tickets.panels[]` |
| **Category** | One entry inside a panel — the button or dropdown option a member clicks. Decides *where* the ticket opens, *who* may open it, *what* each staff role may do in it, its *language* and its *messages*. | `panels[].categories[]` |
| **Ticket** | The channel a member ends up in. | the `tickets` table |

A guild can have several panels; each panel has its own categories. A category
that has no destination category, or that is paused, simply does not appear on
the panel — and a panel with nothing left to click is taken down rather than
left up with dead buttons.

---

## Files

```
modules/tickets.py                      Config schema, limits, permission model,
                                        status dots, panel posting/refresh.
                                        No ticket action.
services/ticket_service.py              Every ticket verb (bot.tickets).
utils/ticket_views.py                   Panel, ticket message, closing card,
                                        close request, escalation notice, claim
                                        notice, participants modal, DMs.
cogs/tickets.py                         /ticket group + channel cleanup and
                                        staff-thread guard listeners.
db/repositories/tickets.py              The tickets table.
services/ticket_transcript_service.py   Exports a closing ticket's conversation
                                        (bot.ticket_transcripts).
services/ticket_closure_detector.py     Spots a finished conversation and offers
                                        the closure (bot.ticket_closure).
services/data/ticket_closure_references.json
                                        The closing keywords (5 languages) and
                                        the phrases embedded to score against.
utils/compression.py                    zstd/zlib, one codec decision.
utils/ticket_rating_views.py            The rating modal, the DM button, the
                                        "rate it" prompt.
utils/ticket_stats_views.py             The /ticket stats cards.
db/repositories/ticket_transcripts.py   The transcript archive.
db/repositories/ticket_ratings.py       The ratings and their aggregates.
modules/configs/tickets_config.py       /config root: the panel list, and the
                                        module-wide settings screen.
modules/configs/tickets_panel_config.py /config: one panel.
modules/configs/tickets_category_config.py
                                        /config: one category + its permissions.
tests/test_tickets.py                   Schema, permissions, overwrites, screens, i18n.
tests/test_ticket_transcripts.py        Compression, export, closure detection,
                                        settings, membership changes.
tests/test_ticket_ratings.py            The modal, the DM button, the stats cards.
```

The split between `modules/tickets.py` (configuration) and
`services/ticket_service.py` (actions) is deliberate: the config surface and the
runtime surface can then be read, changed and tested separately, and neither
grows a dependency on the other's state.

---

## Limits (free / premium)

| | Free | Premium |
|---|---|---|
| Panels per server | **3** | **10** |
| Categories per panel | **5** | **15** |

Constants: `FREE_MAX_PANELS`, `PREMIUM_MAX_PANELS`, `FREE_MAX_CATEGORIES`,
`PREMIUM_MAX_CATEGORIES` in `modules/tickets.py`.

`get_limits(bot, guild_id)` reads premium **at the moment of the action**, never
from a rendered panel — a config screen can sit open for hours and a stale
render must never be an entitlement (see [PREMIUM.md](PREMIUM.md)). A failed
premium lookup falls back to the **free** quota: an outage must not silently
hand out the premium one.

Discord's own ceilings cap the plan on top of that:
`max_categories_for_style(style, limit)` returns at most 15 for a button panel
(three rows of five) and 25 for a dropdown.

The limit is enforced in three places, on purpose: the **Add** button is
disabled at the cap, the click re-checks it against a fresh read, and
`validate_config` rejects an over-quota config however it arrived — including
straight from the dashboard.

---

## Configuration schema

Stored in `guilds.data.modules.tickets`. Everything that reads it goes through
`normalize_config()`, so a config written by the dashboard, an old one missing a
key added later, and one just built by `/config` all come out identical.

```jsonc
{
  "panels": [
    {
      "id": "p_a1b2c3",            // generated, stable, used in custom_ids
      "name": "Support",           // internal name (config screens only)
      "channel_id": 123,           // where the panel message is posted
      "message_id": 456,           // the posted message — written by the bot
      "title": "Need help?",       // shown to members (falls back to name)
      "description": "Pick a category below",
      "accent_color": 5793266,     // 0xRRGGBB
      "style": "buttons",          // "buttons" | "select"
      "placeholder": "Choose…",    // dropdown placeholder ("select" only)
      "enabled": true,
      "categories": [
        {
          "id": "c_d4e5f6",
          "name": "General support",   // button label / option label
          "emoji": "<:support:123>",   // optional
          "description": "Anything else",  // dropdown option description
          "button_style": "primary",   // primary | secondary | success | danger
          "discord_category_id": 789,  // where the ticket channel is created
          "allowed_role_ids": [],      // empty = everyone
          "denied_role_ids": [],
          "ping_role_ids": [],         // mentioned when a ticket opens
          "ping_staff_roles": true,    // …and so are the roles that can see it
          "permissions": {             // role id (string) -> permissions
            "111": ["view", "close", "claim", "staff_thread"],
            "222": ["admin"]
          },
          "open_message": "…",         // the WHOLE pinned message
          "close_message": "…",        // added to the closing card
          "buttons": ["close", "claim", "escalate",
                      "staff_thread", "participants"],
          "claim_enabled": true,       // the claim system, per category
          "claim_lock": false,         // only the claimer answers
          "name_format": "ticket-{number}",
          "max_open_per_user": 1,
          "enabled": true
        }
      ]
    }
  ]
}
```

**Snowflakes may arrive as strings** — JSON has no 64-bit integer, so the
dashboard writes them as text. `normalize_config` parses them back; never
compare a raw stored id to a `discord.Object.id` without going through it.

`permissions` keys are **strings** for the same reason.

### Placeholders

Usable in `open_message` / `close_message`: `{user}` `{username}`
`{display_name}` `{server}` `{category}` `{number}` `{ticket}`.

Usable in `name_format`: `{number}` (zero-padded to 4) `{username}`
`{display_name}` `{category}`.

`render_text()` substitutes them with plain `str.replace`, **not**
`str.format` — an admin who types a stray `{` would otherwise blow up the
message for everyone.

`buttons` is the list of controls the ticket message carries, out of `close`,
`claim`, `escalate`, `staff_thread`, `participants` and `close_request`. An
**empty list is a real answer** ("commands only") and is stored as such; only a
*missing* key falls back to `DEFAULT_TICKET_BUTTONS` — which is the five above
minus `close_request`, that one being a command (`/ticket close-request`)
unless a server ticks it back on.

### Defaults are offered, not hidden

Every field that has a default — the opening and closing messages, the panel
title and description — opens **pre-filled with the wording that would be used
anyway** (`default_open_message()`, `default_close_message()`,
`default_panel_title()`, `default_panel_description()`). An admin should be
editing the message their members will read, never guessing at it in front of
an empty box. An existing value always wins over the default.

`default_open_message()` substitutes its `{icon}` with `str.replace` rather
than passing it to `t()`: `t()` runs `str.format` over the whole string as soon
as it gets one kwarg, which would eat the `{number}` / `{user}` placeholders
the admin is meant to keep.

The two ticket messages are pre-filled in the **server** language, not the
admin's: those words are what the member reads. Only the labels around them
follow the admin.

---

## The permission model

Ten permissions, granted **per role, per category**:

| Key | What it allows |
|---|---|
| `view` | See and talk in the tickets of this category |
| `close` | Close and reopen a ticket |
| `claim` | Take a ticket in charge, and release your own |
| `unclaim_others` | Take a ticket off the agent holding it |
| `staff_thread` | Open and join the private staff thread |
| `rename` | Rename the ticket channel |
| `move` | Move the ticket to another category |
| `participants` | Add and remove members and roles |
| `stats` | Read this category's handling ratings (`/ticket stats`) |
| `admin` | Everything above — **and keeps access after an escalation** |

`claim` and `unclaim_others` are deliberately separate. Releasing your own
ticket is part of holding it; taking a case off a colleague is a different
decision, and one an agent should not be able to make. `admin` expands to
both, so a responsible needs nothing extra.

Resolution (`member_permissions(member, category, ticket)`), in this order:

1. A **guild administrator** always has everything.
2. Each of the member's roles contributes what it was granted;
   `admin` expands to the full set.
3. The ticket **owner**, and anyone added to the ticket by hand (member or
   role), always gets `view` — and nothing else. That mirrors the channel
   overwrites exactly: someone who can read the ticket but whom the permission
   model claims cannot see it would be refused the actions open to everyone in
   it. Closing your own ticket is the server's decision (grant `close` to a
   members role for that); *asking* for it needs no permission at all.

### Who may open a ticket

`can_open(member, category)`:

- A **denied** role always wins — over an allowed role, and over being a guild
  administrator. A deny list that could be walked past would be a trap.
- An **empty allow list means everyone**, which is what an admin expects from a
  field they never filled in.
- Otherwise the member needs one of the allowed roles (guild administrators pass).

---

## The claim system

Optional, per category (`claim_enabled`). It answers one question a support
team asks constantly: *who is on this?*

### One button, three outcomes

`TicketService.toggle_claim` is what the **Claim** button runs, and who is
clicking decides what happens:

| State | Clicker | Result |
|---|---|---|
| Unclaimed | holds `claim` | they take it |
| Claimed by them | — | they release it (a mis-click undoes itself) |
| Claimed by someone else | holds `unclaim_others` | released |
| Claimed by someone else | does not | refused, naming the holder |

`/ticket claim` and `/ticket unclaim` say which one they mean instead of
toggling — same methods underneath, so they cannot drift from the button.

An **escalated** ticket can only be claimed by a role holding `admin`
(`claim_permission()`): letting a plain agent claim it back would undo the
escalation through the side door.

### The coloured dot in the channel name

A claimed ticket is readable from the channel list: `🟢〡ticket-0003`.

| Dot | State |
|---|---|
| 🔴 | open, nobody on it |
| 🟢 | claimed |
| 🟣 | escalated |
| ⚫ | closed |

These four are the **only** Unicode emojis Moddy uses outside country flags
(CLAUDE.md rule 3), and not by choice: a Discord channel name cannot carry a
custom emoji. A category with `claim_enabled: false` gets no dot at all — the
escalated and closed colours only read as a scale next to the other two.

The dot is a *prefix*, stripped before a new one is applied
(`strip_status_prefix` / `apply_status_prefix`), so a ticket that changes hands
ten times still carries exactly one. `/ticket rename` and `move_ticket` go
through the same helpers, so neither drops it.

`sync_status_prefix()` fires the rename **in a background task**. Discord
allows two channel renames per ten minutes; a busy ticket claimed, released and
re-claimed would otherwise make the click wait out the rate limit while
discord.py sleeps on the request. The claim is already stored and its
permissions already applied — the name catching up late is the cheap half.

### `claim_lock`

With `claim_lock` on, a claimed ticket lets only the claimer, the roles holding
`admin`, the opener and the manually added people **speak**. Every other staff
role keeps `view_channel` and loses `send_messages`: a locked ticket is not a
private one, and the rest of the team must still be able to read it.

The lock only bites once somebody actually holds the ticket — an unclaimed
ticket nobody may answer would be a dead end.

The claimer's own member-level overwrite is written **last**, because it has to
outrank the role overwrite that just muted them.

---

## Channel permissions, escalation and closure

`TicketService.build_overwrites()` rebuilds the **whole** overwrite map from
scratch on every change rather than patching it. That is what keeps escalation,
closure and participant edits from drifting into states nobody can explain.

| State | Who can see the channel |
|---|---|
| Open | `@everyone` denied · the bot · every role with `view` · the opener · everyone added by hand |
| **Claimed + `claim_lock`** | the same, but only the claimer, the `admin` roles, the opener and the manual participants may *write* |
| **Escalated** | the bot · only roles with `admin` · the opener · everyone added by hand (unless dropped, or kept read-only) |
| **Closed** | the bot · every role with `view` — the opener and the manual participants are hidden |

- **Escalation** keeps the opener (a ticket without them is meaningless) and the
  manually added people. The flow asks what to do with them — and asks only
  when there is somebody to ask about, so the confirmation never becomes noise:

  | Answer | What happens |
  |---|---|
  | **Keep them** | they read and write as before |
  | **Keep them, read-only** | `escalation_mute`: they follow along, they no longer weigh in |
  | **Remove them** | the manual participant list is emptied |

  Two answers were not enough: keeping someone in the room and keeping them
  *talking* are different decisions, and an escalation usually wants the first
  without the second.

  Escalating also **releases the claim and parks it** in
  `pre_escalation_claim`. An escalated ticket belongs to the responsibles, so
  the agent who had it must not keep the channel — and cancelling the
  escalation puts the same person back on it, rather than leaving the ticket
  unassigned. `set_escalated()` does both halves in one statement, so the two
  can never disagree.

  Escalation refuses outright when no role holds `admin`: escalating with
  nobody to escalate *to* would lock the ticket down to its opener and the
  server admins.
- **Closing keeps the channel.** Nothing is destroyed by a click: the ticket is
  locked, a closing card is posted with **Reopen** and **Delete the channel**,
  and the opener gets a DM (best effort — closed DMs are the norm, not an
  error). Deleting requires `admin`.
- **Reopening restores the map exactly**, because it is rebuilt, not undone —
  and it DMs the opener too. The closure was announced in a DM; its
  cancellation has to be, or a member told their ticket was over never learns
  that a channel which vanished from their list is back.

---

## The ticket message

The pinned message a ticket opens with is **entirely** the category's
`open_message`: its title line, its body, its footer. The module adds no
heading and no meta line of its own, so an admin editing that field is editing
the whole of what their members read.

The default (`default_open_message()`) is what that looks like:

```
### <:ticket:…> Ticket #{number}
Thanks for opening a ticket. Describe your request as precisely as you can —
the team will answer you here.
-# Opened by · {user} · `{category}`
```

A line holding nothing but `---` becomes a real Components V2 **separator**
(`split_message_blocks` → `add_message_body`). It is the only piece of layout
an admin can ask for from a text box: markdown's horizontal rule does not exist
inside a container.

**The buttons live outside the container**, on the view itself. A container is
the card; the actions belong under it, not inside its frame. Every card in
`utils/ticket_views.py` follows the same shape — container first, then
`_add_rows(self, …)` — and `tests/test_tickets.py` asserts no `ActionRow` ends
up inside a `Container`.

Which buttons appear is `buttons` (see the schema). The **registered shell**
(`TicketControlView()`, built with no category) deliberately declares *every*
button id: a registered view matches on custom_id, so an id the shell never
declared would be a dead button after a restart, whatever that guild
configured.

| Button | Style | Icon |
|---|---|---|
| Close | red | `TICKET_CLOSE` (a plain cross) |
| Claim | **blue**, straight after Close | `TICKET_CLAIM` |
| Escalate | grey | `TICKET_ESCALATE` |
| Staff thread | grey | `TICKET_STAFF_THREAD` |
| Participants | grey | `TICKET_PARTICIPANTS` |
| Request closure | grey, off by default | `TICKET_CLOSE_REQUEST` |

**Participants is a Modal** (`TicketParticipantsModal`), not a panel of
selects: both pickers open **pre-filled with the current participants**, and
one submit applies the whole picture. That is what makes unselecting the
obvious way to remove somebody — the form shows who is in, not a queue of
additions. Modals are the one surface deliberately excluded from persistence.

---

## Pings

**A ping is its own message, and the bot deletes it immediately**
(`TicketService.ping`). Discord has already delivered the notification by the
time the message goes, so nothing is lost — and the ticket is not left with a
permanent wall of blue names at the top of every card. Three places ping:
opening a ticket, requesting a closure, escalating.

This also sidesteps the Components V2 rule that used to force the mentions
*into* the card: Discord rejects any message carrying both a `content` field
and the `IS_COMPONENTS_V2` flag that discord.py sets for every `LayoutView`, so
a ping could never have ridden along with a card anyway.
`tests/test_tickets.py::TestNoContentWithLayoutView` guards both halves — no
`send()` in the service passes `content=` and `view=` together, and `ping`
still deletes what it sent.

On a ticket opening, the ping covers the opener, the category's
`ping_role_ids`, and — unless `ping_staff_roles` is switched off — every role
that can actually *see* this category's tickets. Pinging a role that cannot
read the ticket would be pure noise, which is why it is the `view` holders and
not an arbitrary list.

---

## The staff thread

A **private** thread, so the opener never sees it. Discord has no role-based
membership for threads, so it is not pre-filled with every staff member: each
staffer joins by running the action once.

That leaves one hole, and `Tickets.on_thread_member_join` closes it:
**mentioning somebody inside a thread adds them to it.** One stray ping is
enough to hand a ticket's opener the staff-only conversation about them.
Anyone joining who is not allowed to see this category's tickets *as staff* is
therefore removed again, straight away.

The rule is `TicketService.may_be_in_staff_thread`: the member's **role** grant
decides (`view` on the category), never their presence in the ticket. The
opener and the manually added participants hold `view` through the ticket
itself — and the staff thread is precisely the room they must not be in.

---

## The `tickets` table

One row per ticket **channel** that exists in Discord. `channel_id` is UNIQUE
and is the only lookup key the runtime uses: a ticket action always happens
inside its own channel, so the channel id *is* the ticket's identity. That is
also why the ticket control buttons need no id in their `custom_id`.

```sql
CREATE TABLE tickets (
    id                   BIGSERIAL PRIMARY KEY,
    guild_id             BIGINT NOT NULL,
    channel_id           BIGINT NOT NULL UNIQUE,
    panel_id             TEXT NOT NULL,
    category_id          TEXT NOT NULL,
    number               INTEGER NOT NULL,      -- per-guild counter
    owner_id             BIGINT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'open',   -- open | closed
    escalated            BOOLEAN NOT NULL DEFAULT FALSE,
    staff_thread_id      BIGINT,
    participants         BIGINT[] NOT NULL DEFAULT '{}',
    participant_roles    BIGINT[] NOT NULL DEFAULT '{}',
    close_requested_by   BIGINT,
    close_request_reason TEXT,
    close_request_to_staff BOOLEAN,     -- TRUE: member → staff, FALSE: staff → opener
    claimed_by           BIGINT,        -- who is handling it right now
    claimed_at           TIMESTAMPTZ,
    pre_escalation_claim BIGINT,        -- parked there while escalated
    escalation_mute      BOOLEAN NOT NULL DEFAULT FALSE,
    opened_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at            TIMESTAMPTZ,
    closed_by            BIGINT,
    close_reason         TEXT,
    CONSTRAINT tickets_guild_number UNIQUE (guild_id, number)
);
```

`number` is **predicted** by `next_ticket_number()` before the channel is
created, so the channel gets its final name in the same call that creates it —
renaming afterwards would spend one of the two renames Discord allows per
channel per 10 minutes, and a staffer's `/ticket rename` right after opening
would then hang. Two simultaneous opens can still collide on
`tickets_guild_number`; the insert falls back to a fresh `MAX(number) + 1` and
retries, leaving the channel name one off rather than attempting a rename that
may not go through for ten minutes. Cheaper and simpler than an advisory lock
for a rare race.

The four claim columns and `close_request_to_staff` are added by an
**idempotent migration** (`ALTER TABLE … ADD COLUMN IF NOT EXISTS`) next to the
`CREATE TABLE`, so a database created before the claim system — or before close
requests learned which way they point — gains them on the next boot with no
manual deploy step.

### Moddy staff tickets

One kind of row in this table comes from nowhere in the server's config: the
ticket the **Moddy team** opens with `/team ticket` (see
[LINKED_ROLES.md](LINKED_ROLES.md)). It carries the sentinel pair
`panel_id = category_id = "__moddy_staff__"`, and `TicketService.resolve()`
short-circuits on it: its panel and category are built on the spot from the
guild's Moddy Team role (`staff_ticket_context`) rather than read from
`guilds.data.modules.tickets`.

That is what makes it work in a server that never enabled the module — and what
stops an admin deleting a category mid-conversation from breaking one. The
category grants `admin` to the Moddy Team role and to nothing else, carries
`close` and `participants`, and has claiming off. Everything else — the verbs,
the buttons, the channel cleanup listener, the closing card — is the code above,
unchanged.

A ticket channel deleted by hand is forgotten by
`Tickets.on_guild_channel_delete`. Without it, the member's open-ticket quota
would count a channel that no longer exists and they could never open another.

---

## Actions

Every one of them is a method on `bot.tickets`
(`services/ticket_service.py`), a `/ticket` subcommand, **and** — for the ones
a category puts on the ticket message — a button. The buttons are the shortcut;
the commands are the contract. Both call the same method, so they cannot drift.

| Action | Permission | Notes |
|---|---|---|
| `open_ticket` | `can_open` | Enforces `max_open_per_user`. Name (with its status dot), overwrites and topic go in with the channel, in one call. |
| `close_ticket` | `close` | Locks, posts the closing card, DMs the opener. |
| `reopen_ticket` | `close` | Rebuilds the map and DMs the opener with a link back. |
| `delete_ticket` | `admin` | Destroys the channel. |
| `request_close` | `view` | **Bidirectional** — see below. Returns `(ticket, to_staff)`. |
| `accept_close_request` | the side that was asked (`close`, or the opener on a staff offer) | Closes with the reason given for asking. |
| `cancel_close_request` | the side that was asked, or being the requester | Refusing, or withdrawing your own request. |
| `claim_ticket` | `claim` (`admin` while escalated) | Posts a claim notice in the channel. |
| `unclaim_ticket` | `claim` for your own, `unclaim_others` for someone else's | |
| `toggle_claim` | *see above* | What the button runs. |
| `escalate` | `admin` | Asks about manual participants when there are any; releases and parks the claim. |
| `deescalate` | `admin` | Restores the parked claim. |
| `move_ticket` | `move` **in the current category** | The destination's permissions replace the current ones — that is the point of moving. |
| `rename_ticket` | `rename` | Keeps the status dot. |
| `add_participant` / `remove_participant` | `participants` | Members *or* whole roles. The opener cannot be removed. |
| `open_staff_thread` | `staff_thread` | Private thread; each staffer joins by running the action once (Discord has no role-based thread membership). |
| `evict_from_staff_thread` | — | Housekeeping, run by the thread guard. |

### A close request points both ways

`/ticket close-request` (and the optional `close_request` button) is **one
action with two directions**, decided by who runs it:

| Run by | Direction | Who is rung | Who answers the card |
|---|---|---|---|
| a member (`view`, no `close`) | asks the staff to close | the roles holding `close` | the staff |
| a staffer (`close`) | offers the closure to the opener | the opener | the opener — and the staff, who could have closed it outright |

Neither side is forced: the card carries **Close the ticket** and **Keep it
open**. Accepting goes through `accept_close_request`, which closes the ticket
with the reason the requester gave — the opener holds no `close` permission, so
that method does the checking itself and calls `close_ticket(...,
bypass_permission=True)`. Nothing else may set that flag.

The direction is **stored** (`close_request_to_staff`), not recomputed when the
buttons are clicked: a card is answered later, possibly after the requester's
roles changed or after they left the server, and who may answer depends on
which way the request pointed when it was made.

Failures are raised as `TicketError` carrying an **i18n key**, never a formatted
string: the caller decides whether to answer in the actor's language (a slash
command) or the ticket's (a message posted in the channel).

---

## Membership changes

Two listeners in `cogs/tickets.py`, neither of them a setting: both are facts
the staff and the member need, not features to opt into.

**When the author leaves** (`on_member_remove`), every ticket *they opened* and
that is still open gets a card saying so, with a **Close the ticket** button.
Only the tickets they opened: a ticket does not lose its purpose because
somebody who had been added to it left.

Nothing closes by itself, and the button is not a shortcut past the permission
model — it calls `close_ticket` like everything else, so only somebody holding
`close` can act on it. A departure often *is* the end of a ticket, but not
always: a report still has to be acted on, a refund still has to be recorded.
The decision stays with the staff.

**When they come back** (`on_member_join`), `restore_member_access` puts them
back into every open ticket they belong to — the ones they opened *and* the ones
they were added to. Leaving a server drops the per-member channel overwrites
with it, so without this they would return to a ticket they opened and can no
longer read. Access is rebuilt from the stored ticket through
`sync_permissions` rather than patched, so a claim lock or an escalation that
happened while they were away is honoured exactly as it would be for anyone
else.

---

## Module settings

Three features and a log channel, stored in
`guilds.data.modules.tickets.settings` and edited from
**`/config` → Tickets → Settings**:

| Key | Default | What it does |
|---|---|---|
| `transcripts_enabled` | `true` | Archive the conversation when a ticket closes |
| `closure_detection_enabled` | **`false`** | Offer the closure when the conversation looks over |
| `rating_enabled` | `true` | Ask the opener to rate how they were looked after |
| `transcript_retention_days` | `0` | Days before a transcript is purged; `0` = forever, capped at 3650 |
| `log_channel_id` | `null` | Where the closing card is posted |

These are **module-wide, not per-category**, and that is the distinction worth
keeping: `claim_enabled` or `claim_lock` genuinely differ from one workflow to
the next, but a server archives its tickets or it does not. It also means one
place to switch a feature off, which is what "optional" has to mean.

Closure detection is the one that defaults to off: it spends AI quota, and
everything that goes through `bot.gateway.ai` is opt-in here.

A config written before these existed loads with the defaults above
(`normalize_settings`), so no guild has to be touched. The five keys are also
accepted flat at the root of the module config, for a dashboard write that
predates the `settings` object.

The three switches and the retention window live behind a modal, so the screen
prints their current value as text; the log channel is a `ChannelSelect`, which
displays its own state, so it does not — see CLAUDE.md rule 9.

---

## Transcripts

When a ticket closes, its conversation is exported, compressed and stored. The
member gets a link in their closing DM, the staff get the same link on the
ticket log card, and the dashboard renders the archive.

The exporter is **native** (`services/ticket_transcript_service.py`): it reads
`channel.history()` and writes a compact JSON body. No external exporter binary,
no extra language runtime, no subprocess, and no bot token leaving the process.
Railway bills almost entirely on resident memory ([RAILWAY.md](RAILWAY.md)), and
a second runtime kept alive to archive a few hundred tickets a day would be the
most expensive line on the invoice.

### What keeps it small

1. Short keys, and **every empty field omitted** — a plain text message costs
   about forty bytes before compression.
2. Authors stored **once**, in `ticket_transcript_authors`; a message carries
   only an author id.
3. The whole body compressed with zstd level 19 (`utils/compression.py`), which
   removes another 90%+ on conversational JSON.

Attachments are referenced by their CDN url, never downloaded: an archiver that
copies files is a storage bill, not an archive.

In practice a 30-message support ticket stores in roughly 300 bytes.

### The Components V2 trap

Moddy posts its own cards with Components V2, so their `content` and `embeds`
are **empty** and all their text lives in the component tree.
`extract_component_text` walks it. Without that, every card Moddy wrote — the
opening message, the closing card, every claim notice — would archive blank.

### Bounds

- **20 000 messages** per transcript. Beyond that the *most recent* are kept and
  `truncated` is set: losing the beginning of a very long ticket costs less than
  losing how it ended, which is what anyone reading an archive is looking for.
- **4000 characters** per message, like every other stored free-text field.
- **120 s** hard timeout on the whole export.

### It never fails a closure

`capture()` returns `None` on every failure path — a forbidden history, a
timeout, a database error — and logs it. The closure carries on and simply
offers no transcript link, rather than a dead one. Archiving is not worth
failing a closure over.

### Reopening

Reopening touches nothing. The next closure re-reads the whole history and
creates a **new** row, so every closure of a ticket keeps its own archive and
every link already handed out keeps showing what it showed.

### Retention

`transcript_retention_days` is enforced by a daily task in `cogs/tickets.py`,
driven by the guilds that actually own transcripts rather than by every guild
Moddy is in. Deleting a transcript cascades to its authors.

> The exact schema, the body format field by field, the decompression
> algorithm and the `/transcripts/<key>` authorisation model are in
> [TICKETS_INTEGRATION.md](TICKETS_INTEGRATION.md) — that is what the backend
> implements against.

---

## Closure detection

When someone writes "merci, c'est bon" the ticket is almost always over, and
nobody closes it. `services/ticket_closure_detector.py` spots that and **offers**
the closure.

### The cost model comes first

This runs on every message of every ticket of every server, so the order of the
checks is the design:

1. `TicketService.is_open_ticket_channel` — an in-memory set of open ticket
   channel ids, hydrated once at boot. Answers "is this even a ticket?" without
   a query.
2. `looks_like_closure` — a lexical test over ~90 multilingual roots
   (`merci`, `thanks`, `gracias`, `obrigado`, `danke`, `résolu`, `solved`,
   `you can close`, …). Free, no I/O. **No keyword hit, no API call, ever.**
3. Structural checks, also free: 8–160 characters, no `?` (a question is not a
   goodbye), no mention (still addressed to somebody), not a command, ticket
   older than 120 s with at least 3 human messages.
4. A **20 s debounce** per channel, so "merci" two seconds before "attends en
   fait non" produces nothing.
5. Only then is the message embedded and scored.

The embedding engine, its cosine maths and its LRU+TTL cache are reused from
`automod/` rather than rewritten; only the reference corpus
(`services/data/ticket_closure_references.json`, 40 phrases across the five
languages) and the threshold are this module's own. The threshold is **0.62**,
deliberately higher than automod's 0.45: a false positive there routes a message
to a second model, a false positive here puts a card in front of everybody in
the channel.

### Suggesting is not granting

The card has one action button, and its callback re-derives the **clicker's**
permissions — never the permissions of whoever wrote the detected message:

| The clicker holds | What happens |
|---|---|
| `close` | `close_ticket()` |
| `view` only (typically the opener) | `request_close()` — the same close request `/ticket close-request` produces |
| neither | an ephemeral refusal |

Both branches go through the service, which is the only thing in the codebase
that checks a ticket permission. Offering a closure therefore cannot hand out
one. `tests/test_ticket_transcripts.py::TestClosureSuggestionAuthorisation`
pins this down.

The second button, **Not yet**, holds the suggestion off for six hours from that
click. One suggestion per ticket per window; everything the detector remembers
between messages is in memory, and losing a debounce timer to a redeploy costs
one slightly-early card — not worth a table.

---

## Ratings

The member who opened a ticket can say how they were looked after: one adjective
out of five, optionally who helped them, optionally a comment.

Scores are **adjectives, never numbers** — `Pas top`, `Moyen`, `Correct`,
`Très bien`, `Excellent`, translated in the five languages. "3/5" means nothing
consistent across people; "Correct" does.

### The modal

`TicketRatingModal` (`utils/ticket_rating_views.py`) uses three of Discord's
five top-level slots: a `RadioGroup` of the five adjectives, a `Select` of the
staff who actually touched this ticket, and an optional comment.

The staff picker is built from `claimed_by`, `closed_by` and the staff among the
participants, pre-selected on **the claimer, else the closer**, plus a *nobody
in particular* option — a real answer, not an empty one. It is a plain `Select`
rather than a `UserSelect` because a `UserSelect` cannot be pre-filled and
because nobody should be able to rate a member of the server who never touched
their ticket. With no candidate at all the component is dropped, and the modal
has two.

> `RadioGroup` exposes `.value`, not `.values`. Reading `.values` there silently
> returns nothing — see [MODALS_V2.md](MODALS_V2.md).

### The three moments

Discord requires `send_modal` to be the **first** response to an interaction, so
a single click cannot both close a ticket and open a form. The modal therefore
always opens on a second interaction: the first two paths close the ticket and
answer with an ephemeral card carrying a **Leave a review** button.

| Trigger | Where |
|---|---|
| `close_request` | the member accepts a closure the staff offered them |
| `self_close` | the member closes their own ticket, from the button or `/ticket close` |
| `dm_button` | the closing DM, days later |

The DM button is the durable one, and the interesting one. By the time it is
clicked the channel — and its `tickets` row — may be long gone, so it is a
`DynamicItem` carrying the **transcript key** and resolves everything from
`ticket_transcripts`: the guild, the opener (which is also the authorisation
check), the claimer, the closer. That is the same reason transcripts have no
foreign key on `tickets`.

Once left, the rating is shown in the DM (edited in place, button gone) and on
the ticket log card (edited through the `log_channel_id` / `log_message_id`
remembered on the transcript row).

One rating per **closure**, enforced by `UNIQUE (transcript_id)` rather than by
a check-then-insert: two clicks a few milliseconds apart would both pass a
check. A ticket reopened and closed again is a new interaction and can be rated
again.

### `/ticket stats [staff] [days]`

Without `staff`: the team, ordered **by volume, not by average** — a single 5/5
must not outrank somebody who handled fifty tickets — with a caveat marker under
three reviews.

With `staff`: their volume, their average, the 1→5 histogram, and their ten
latest reviews with comments.

Read with plain SQL aggregates, not through `bot.stats`: that is built for
bounded counters and deliberately refuses to carry a user id as a dimension.
Ratings are named data about named people and live in their own table.

Volume is counted from `ticket_transcripts`, not from `tickets`: a closed ticket
whose channel was tidied away still counts towards the work its staff did.

Access is `Manage Server`, **or** the new per-category `stats` permission — in
which case the results are limited to the categories where the caller holds it.

---

## The ticket log

`log_channel_id` (module settings) is where Moddy posts one card per closure:
number, category, opener, claimer, closer, reason, duration, participant count,
the transcript link, and the rating as soon as there is one.

It is configured on the module rather than through `serverlogs/` on purpose:
that registry catalogues the 163 **Discord** events, and a ticket closing is an
application event of Moddy's own making. The calque is AltGuard's
`log_channel_id`, not the server-logs module.

`refresh_ticket_log` re-renders a posted card when a rating lands afterwards,
which is what `log_channel_id` / `log_message_id` on the transcript row are for.
It is best effort throughout: a card that cannot be edited — deleted message,
revoked permission, channel gone — must never turn a member's rating into an
error.

---

## Slash commands, published per guild

`/ticket` exists **only in the guilds where the module is enabled**. The
mechanism is generic, not ticket-specific:

1. The group is declared at **module level** in `cogs/tickets.py`, never as a
   Cog attribute — a Cog attribute would be added to the global tree by
   discord.py, which is exactly what must not happen.
2. `setup()` calls `bot.register_module_commands("tickets", [ticket_group])`.
3. `ModdyBot._register_guild_command_set()` adds it to a guild's tree only when
   `get_enabled_module_ids(guild_id)` contains the module.
4. `ModuleManager` calls `bot.resync_module_commands(guild_id)` after **every**
   config save, delete or dashboard-pushed reload. The bot skips the sync when
   the enabled set did not change — guild command syncs are rate-limited, and a
   save that only changes a colour must not spend one.

**If `/ticket` never appears**, the module and the cog are loaded by two
different mechanisms — `modules/tickets.py` by `ModuleManager.discover_modules()`,
`cogs/tickets.py` by `load_extensions()` — so tickets can work perfectly in
`/config` while the commands never publish. The startup log says which is the
case:

```
Module commands registered for 'tickets': ['ticket']      # setup() ran
Module-gated commands available: tickets -> ['ticket']    # on_ready
Guild commands synced for <name> (<id>) — modules: ['tickets']
```

A missing first line means the cog failed to load — look for
`[FAIL] Cog error tickets` earlier in the log. `modules: []` on the third means
no panel is enabled in that guild yet.

To give another module its own commands, do the same three things: declare the
group at module level, register it in `setup()`, and make sure the module's
`enabled` reflects what "this server uses the feature" means.

---

## Persistence

See [PERSISTENT_VIEWS.md](PERSISTENT_VIEWS.md). Three different models here, for
three different reasons:

| Surface | Model | Why |
|---|---|---|
| Ticket message, closing card, close request, escalation notice, escalation confirmation | **Registered views, static custom_ids** | The channel the click comes from *is* the ticket. An id in the custom_id would only add a second source of truth that could disagree with the channel. The ticket message's shell declares every button id, since which ones a guild shows is configurable. |
| Claim notice, closing DM, reopening DM | **Nothing to register** | No interactive child at all. |
| The participants editor | **A Modal** | Deliberately excluded from persistence, like every modal: it is answered in the moment and Discord closes it on a restart anyway. |
| The public panel's buttons / dropdown | **`DynamicItem`** (`TicketOpenButton`, `TicketOpenSelect`), registered by `TicketsPersistence` | They carry the panel and category ids. |
| `/config` panel, category and permission screens | **`DynamicItem`**, registered by `TicketsConfigPersistence`; the wrapper views are deliberately *not* registered | They are scoped to an entity (a panel, a category, a role) that a static custom_id cannot carry — same as `LogsCategoryView`. |

The `/config` **root** (`TicketsConfigView`) is a normal registered view:
`interaction.guild_id` plus a Manage Server check is the whole auth context it
needs.

Because a `DynamicItem` reconstructs from scratch on every click, there is no
`self` to stage edits on — which is why the panel, category and permission
screens **apply every change immediately** instead of batching behind a Save
button. Deleting a panel or a category is the one exception: it asks first, on
the same screen.

Authorization is never carried by a view. Every callback resolves the ticket
from the channel, the category from the guild's config and the actor's
permissions from their roles. A stale button clicked a month after a restart is
therefore exactly as safe as a fresh one.

### A card never carries a mention

Discord rejects any message that carries both a `content` field and the
`IS_COMPONENTS_V2` flag, and discord.py sets that flag automatically for every
`LayoutView`. So `channel.send(content=..., view=SomeLayoutView())` is a
guaranteed `400 … The 'content' field cannot be used when using
MessageFlags.IS_COMPONENTS_V2` — and if the call is wrapped in a
`try/except HTTPException` that only logs, the message just silently never
appears.

Pings are therefore their own message, deleted immediately — see
[Pings](#pings). `tests/test_tickets.py::TestNoContentWithLayoutView` scans the
service for a `send()` that passes both, so this cannot come back.

---

## i18n

Two independent languages, and mixing them up is the easy mistake:

- **The actor's language** (`i18n.get_user_locale(interaction)`) for anything
  ephemeral — errors, confirmations, the `/config` screens.
- **The server language** (`TicketService.ticket_locale()`, which reads
  `utils/guild_language.py`) for anything posted *in* the ticket — the control
  bar, the closing card, the escalation notice, the staff thread, the DM. A
  ticket speaks one language, whoever is typing, and it is the same language
  as the rest of Moddy on this server (`/config` → **Server settings**).
  Categories used to carry one each; they no longer do.

Keys live under `modules.tickets.*` in the five locale files.
`tests/test_tickets.py` asserts the five stay in step, key for key.

Command names and descriptions are localized separately, in all 32 Discord
locales — see [COMMAND_LOCALIZATION.md](COMMAND_LOCALIZATION.md).

---

## Backend / dashboard contract

The dashboard writes `guilds.data.modules.tickets` directly and notifies the bot
on Redis with `module_id: "tickets"` (see
[MODULE_SYSTEM.md §3bis](MODULE_SYSTEM.md)). `TicketsModule.on_external_config_change`
then does what `/config` does on save:

- **updated** → re-post every enabled panel, take down the paused ones. Reloading
  the config alone is not enough: a panel is a *message*, and its title, its
  buttons and its very existence are stored state that only a re-post brings in
  line.
- **deleted** → take every panel message down and write nothing back (the config
  is already gone; writing would half-resurrect the module).

The transcripts, the ratings and the module settings have their own contract —
the exact schema of the three tables, the compressed body format field by field,
the decompression algorithm, the `/transcripts/<key>` authorisation model and
the aggregate queries the dashboard needs:
**[TICKETS_INTEGRATION.md](TICKETS_INTEGRATION.md)**.

The short version: the bot is the only writer of `ticket_transcripts`,
`ticket_transcript_authors` and `ticket_ratings`; the dashboard reads them and
writes only `settings`. And `staff_thread` inside a transcript body is staff-only
content that must be withheld from the ticket's own author.

The recap relayed to `moddy:dashboard` reports `panels`, `panels_posted` and
`panels_failed`, so an admin can tell a save that stored fine but could not post.

`message_id` is **owned by the bot**. The dashboard should round-trip whatever
it reads there and never invent one.

Enabling or disabling the module also adds or removes `/ticket` in that guild —
handled automatically, no extra event needed.

---

## Extending the module

- **A new ticket action**: add the method to `TicketService` (raising
  `TicketError` with an i18n key), then a `/ticket` subcommand. Only add a
  button if it belongs in the five most-used — the control bar is one row.
- **A new permission**: add the constant, put it in `TICKET_PERMISSIONS`, add
  `modules.tickets.permissions.<key>.{name,description}` to the five locale
  files. `admin` expands to the whole tuple, so it needs nothing else. The
  permission dropdown and the audit line pick it up on their own.
- **A new per-category setting**: add it to `normalize_category()` with a safe
  default (every stored config predates it), then to the right modal —
  identity, messages or options. A yes/no setting goes into the options
  modal's `CheckboxGroup` (`_CATEGORY_SWITCHES`) rather than becoming a
  component of its own: a modal is capped at five top-level components.
- **A new ticket-message button**: add the constant, put it in
  `TICKET_BUTTONS` (order matters — that tuple *is* the row order), add
  `modules.tickets.actions.<key>` and `modules.tickets.buttons.<key>_hint` to
  the five locale files, then a spec line in `TicketControlView._build_view`.
  The `/config` picker and the registered shell pick it up on their own.
- **A new panel style**: add it to `PANEL_STYLES`, teach `build_panel_view()`
  to render it and `max_categories_for_style()` its ceiling.
