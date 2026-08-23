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
6. [Channel permissions, escalation and closure](#channel-permissions-escalation-and-closure)
7. [The `tickets` table](#the-tickets-table)
8. [Actions](#actions)
9. [Slash commands, published per guild](#slash-commands-published-per-guild)
10. [Persistence](#persistence)
11. [i18n](#i18n)
12. [Backend / dashboard contract](#backend--dashboard-contract)
13. [Extending the module](#extending-the-module)

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
                                        panel posting/refresh. No ticket action.
services/ticket_service.py              Every ticket verb (bot.tickets).
utils/ticket_views.py                   Panel, control bar, closing card, close
                                        request, escalation notice, participants.
cogs/tickets.py                         /ticket group + channel cleanup listener.
db/repositories/tickets.py              The tickets table.
modules/configs/tickets_config.py       /config root: the panel list.
modules/configs/tickets_panel_config.py /config: one panel.
modules/configs/tickets_category_config.py
                                        /config: one category + its permissions.
tests/test_tickets.py                   Schema, permissions, overwrites, screens, i18n.
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
          "permissions": {             // role id (string) -> permissions
            "111": ["view", "close", "staff_thread"],
            "222": ["admin"]
          },
          "locale": "fr",              // en-US | fr | es-ES | pt-BR | de
          "open_message": "…",         // pinned in the ticket
          "close_message": "…",        // added to the closing card
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

---

## The permission model

Seven permissions, granted **per role, per category**:

| Key | What it allows |
|---|---|
| `view` | See and talk in the tickets of this category |
| `close` | Close and reopen a ticket |
| `staff_thread` | Open and join the private staff thread |
| `rename` | Rename the ticket channel |
| `move` | Move the ticket to another category |
| `participants` | Add and remove members and roles |
| `admin` | Everything above — **and keeps access after an escalation** |

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

## Channel permissions, escalation and closure

`TicketService.build_overwrites()` rebuilds the **whole** overwrite map from
scratch on every change rather than patching it. That is what keeps escalation,
closure and participant edits from drifting into states nobody can explain.

| State | Who can see the channel |
|---|---|
| Open | `@everyone` denied · the bot · every role with `view` · the opener · everyone added by hand |
| **Escalated** | the bot · only roles with `admin` · the opener · everyone added by hand (unless dropped) |
| **Closed** | the bot · every role with `view` — the opener and the manual participants are hidden |

- **Escalation** keeps the opener (a ticket without them is meaningless) and the
  manually added people, and the flow *offers to drop them* — that question is
  only asked when there is somebody to ask about, so the confirmation never
  becomes noise. It refuses outright when no role holds `admin`: escalating with
  nobody to escalate *to* would lock the ticket down to its opener and the
  server admins.
- **Closing keeps the channel.** Nothing is destroyed by a click: the ticket is
  locked, a closing card is posted with **Reopen** and **Delete the channel**,
  and the opener gets a DM (best effort — closed DMs are the norm, not an
  error). Deleting requires `admin`.
- **Reopening restores the map exactly**, because it is rebuilt, not undone.

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

A ticket channel deleted by hand is forgotten by
`Tickets.on_guild_channel_delete`. Without it, the member's open-ticket quota
would count a channel that no longer exists and they could never open another.

---

## Actions

Every one of them is a method on `bot.tickets`
(`services/ticket_service.py`), a `/ticket` subcommand, **and** — for the five
most used — a button on the pinned control bar. The buttons are the shortcut;
the commands are the contract. Both call the same method, so they cannot drift.

| Action | Permission | Notes |
|---|---|---|
| `open_ticket` | `can_open` | Enforces `max_open_per_user`. Name, overwrites and topic go in with the channel, in one call. |
| `close_ticket` | `close` | Locks, posts the closing card, DMs the opener. |
| `reopen_ticket` | `close` | |
| `delete_ticket` | `admin` | Destroys the channel. |
| `request_close` | none beyond `view` | Refused to someone who *can* close — they are told to just close it. |
| `cancel_close_request` | `close`, or being the requester | |
| `escalate` | `admin` | Asks about manual participants when there are any. |
| `deescalate` | `admin` | |
| `move_ticket` | `move` **in the current category** | The destination's permissions replace the current ones — that is the point of moving. |
| `rename_ticket` | `rename` | |
| `add_participant` / `remove_participant` | `participants` | Members *or* whole roles. The opener cannot be removed. |
| `open_staff_thread` | `staff_thread` | Private thread; each staffer joins by running the action once (Discord has no role-based thread membership). |

Failures are raised as `TicketError` carrying an **i18n key**, never a formatted
string: the caller decides whether to answer in the actor's language (a slash
command) or the ticket's (a message posted in the channel).

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

To give another module its own commands, do the same three things: declare the
group at module level, register it in `setup()`, and make sure the module's
`enabled` reflects what "this server uses the feature" means.

---

## Persistence

See [PERSISTENT_VIEWS.md](PERSISTENT_VIEWS.md). Three different models here, for
three different reasons:

| Surface | Model | Why |
|---|---|---|
| Ticket control bar, closing card, close request, escalation notice, participants, escalation confirmation | **Registered views, static custom_ids** | The channel the click comes from *is* the ticket. An id in the custom_id would only add a second source of truth that could disagree with the channel. |
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

---

## i18n

Two independent languages, and mixing them up is the easy mistake:

- **The actor's language** (`i18n.get_user_locale(interaction)`) for anything
  ephemeral — errors, confirmations, the `/config` screens.
- **The category's language** (`category['locale']`) for anything posted *in*
  the ticket — the control bar, the closing card, the escalation notice, the
  staff thread, the DM. A ticket speaks one language, whoever is typing.

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
  identity, messages or options.
- **A new panel style**: add it to `PANEL_STYLES`, teach `build_panel_view()`
  to render it and `max_categories_for_style()` its ceiling.
