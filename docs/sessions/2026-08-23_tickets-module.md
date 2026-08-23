# 2026-08-23 — Tickets module

Full implementation of the **Tickets** server module: panels, categories,
per-role permissions, the whole ticket lifecycle, and a generic mechanism for
publishing a module's slash commands only where that module is enabled.

---

## What was done

### The module (three levels)

- **Panel** — one message in a public channel, with its own title, description,
  accent colour and either **buttons** or a **dropdown**.
- **Category** — one entry inside a panel: where the ticket opens, who may open
  it, what each staff role may do in it, its language, its messages.
- **Ticket** — the channel itself, tracked in a new `tickets` table.

Free servers get 3 panels × 5 categories; premium 10 × 15. Discord's own
ceilings (15 buttons / 25 dropdown options) cap the plan on top of that.

### Permissions

Seven, granted per role per category: `view`, `close`, `staff_thread`,
`rename`, `move`, `participants`, `admin`. `admin` expands to everything **and**
survives an escalation. Guild administrators always have everything; the ticket
owner and anyone added by hand get `view` and nothing else — mirroring the
channel overwrites, so the permission model never disagrees with what someone
can actually read.

### Actions

`close`, `reopen`, `delete`, `close-request`, `escalate`, `deescalate`, `move`,
`rename`, `add`, `remove`, `participants`, `staff-thread`, `info` — every one a
`/ticket` subcommand, the five most used also on the pinned control bar.

### Files added

```
modules/tickets.py                          config schema, limits, permission model, panels
services/ticket_service.py                  every ticket verb (bot.tickets)
utils/ticket_views.py                       panel, control bar, closing card, request, participants
cogs/tickets.py                             /ticket group + channel cleanup
db/repositories/tickets.py                  the tickets table
modules/configs/tickets_config.py           /config root (panel list)
modules/configs/tickets_panel_config.py     /config one panel
modules/configs/tickets_category_config.py  /config one category + permissions
tests/test_tickets.py                       77 tests
docs/TICKETS.md
```

### Files modified

- `bot.py` — `bot.tickets`; `register_module_commands()`,
  `get_enabled_module_ids()`, `resync_module_commands()`, and
  `_register_guild_command_set()` now takes the enabled-module set.
- `modules/module_manager.py` — `_sync_module_commands()` called after every
  save / delete / dashboard-pushed reload.
- `db/base.py` — the `tickets` table + `TicketsRepository` mixin.
- `cogs/config.py` — route to the Tickets panel.
- `utils/emojis.py` — a `TICKETS` section (aliases onto existing icons, see
  "Follow-ups").
- `utils/persistent_views.py`, `tests/test_persistent_views.py` — registration
  and contract coverage for the eight new view classes and the twelve new
  dynamic items.
- `locales/{fr,en-US,es-ES,pt-BR,de}.json` — ~180 keys under `modules.tickets`.
- `locales/commands/*.json` — `/ticket` and its 12 subcommands in all 32
  Discord locales.
- `CLAUDE.md`, `docs/{DATABASE,MODULE_SYSTEM,PERSISTENT_VIEWS,PREMIUM}.md`.

---

## Decisions, and why

**Config and runtime are separate files.** `modules/tickets.py` owns the schema
and never performs an action; `services/ticket_service.py` owns the actions and
never writes config. Either half can be read, changed and tested without the
other. It is also what makes "every action is available as a slash command"
true by construction: the buttons and the commands are two thin shells over the
same method, so they cannot drift.

**A ticket action always happens inside its own channel**, so `channel_id` is
the ticket's identity — `tickets.channel_id` is UNIQUE, and the ticket-channel
views use **static** custom_ids. Putting an id in those custom_ids would only
create a second source of truth that could disagree with the channel.

**The `/config` screens below the root are dynamic items**, because they are
scoped to a panel, a category or a role, which a static custom_id cannot carry.
That forces immediate application of every change (a `DynamicItem` has no
`self` to stage edits on) — the same trade `LogsCategoryView` already makes.
Deleting a panel or a category is the one thing that asks first.

**Closing keeps the channel.** Nothing is destroyed by a click: the ticket is
locked, a card with **Reopen** / **Delete the channel** is posted, and deleting
needs `admin`. Overwrites are rebuilt from scratch on every change rather than
patched, so escalation, closure and participant edits can never drift into a
state nobody can explain.

**A denied role beats everything**, including being a guild administrator. A
deny list that could be walked past would be a trap rather than a setting.

**The participants panel replaces the list instead of adding to it.** A picker
that already shows who is in reads as "this is who is in", and unselecting is
then the obvious way to remove someone; an add-only picker would need a second,
mirror-image remove control for no gain.

**The escalation confirmation only appears when there is something to confirm** —
when manual participants exist. A confirmation that always appears stops being
read.

**Two languages, never mixed**: the actor's for anything ephemeral, the
category's for anything posted in the ticket. A ticket speaks one language,
whoever is typing. `TicketError` therefore carries an i18n *key*, never a
formatted string, and the caller picks the locale.

**A failed premium lookup falls back to the free quota.** Failing open on the
limit would let an outage hand out the premium one.

**Module-gated commands are a generic mechanism, not a ticket special case.**
Declare the group at module level (a Cog attribute would be added to the global
tree by discord.py), register it in `setup()`, and the bot publishes it per
guild — skipping the sync entirely when the enabled set did not change, because
guild command syncs are rate-limited.

---

## Follow-ups

1. **Icons.** `utils/emojis.py` gained a `TICKETS` section whose 14 constants
   are **aliases onto existing icons**, not dedicated artwork (`TICKET` →
   `SUPPORT`, `TICKET_CLOSE` → `LOGOUT`, `TICKET_ESCALATE` → `SHIELD`, …).
   Everything references the constants, so replacing the ids in that one block
   is the whole job. Marked `(placeholder)` inline.
2. **Transcripts.** Closing keeps the channel, so history is not lost — but
   there is no export. An HTML/JSON transcript posted to a log channel on close
   would be the natural next feature, and the `tickets` row already carries
   everything it would need.
3. **A ticket log channel.** Opens/closes/escalations are only visible in the
   ticket itself today. A per-panel log channel would fit next to
   `ping_role_ids`.
4. **Staff thread membership.** Discord has no role-based thread membership, so
   each staffer joins the private thread by running the action once. If that
   proves annoying on busy servers, pre-adding the members of the smallest role
   holding `staff_thread` is the obvious escalation — at a real API cost.
5. **`/ticket` outside a ticket.** Every subcommand answers "this channel is not
   a ticket". A `/ticket list` for staff (open tickets of the server) would give
   the group a reason to exist outside a ticket channel.

---

## Verification

```
python3 -m pytest -q          # 1164 passed
```

Including `tests/test_persistent_views.py` (every new view builds as a bare
shell, no custom_id collision, no dynamic-item template overlap) and
`tests/test_command_localizations.py` (the 32 `/ticket` translations are valid
Discord names).
