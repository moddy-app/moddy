# 2026-08-23 — Tickets: claim system, configurable ticket message, ping hygiene

## What was done

Fourteen changes to the Tickets module, asked for in one pass.

### 1. Pings leave nothing behind

`TicketService.ping()` sends the mentions as their **own** message and deletes
it immediately. Discord has already delivered the notification by then, so
nothing is lost — and a ticket no longer opens with a permanent line of blue
names. Used on opening, on a close request and on an escalation; the
`mentions=` parameter is gone from every view builder.

### 2. The staff roles are pinged too

New per-category `ping_staff_roles` (default on): on top of `ping_role_ids`,
the roles that actually hold `view` in the category are rung. Pinging a role
that cannot read the ticket would be noise, which is why it is the `view`
holders and not an arbitrary list.

### 3. The claim system

Optional per category (`claim_enabled`).

- Two new permissions: **`claim`** (take a ticket, release your own) and
  **`unclaim_others`** (take one off a colleague). `admin` expands to both.
- One button, three outcomes (`toggle_claim`): take an unheld ticket, release
  your own, refuse a colleague's unless you may release it. `/ticket claim`
  and `/ticket unclaim` say which they mean.
- **Coloured dot in the channel name** — `🔴〡ticket-0003` unclaimed, `🟢`
  claimed, `🟣` escalated, `⚫` closed. Applied as a *prefix* that is stripped
  before a new one goes on, so a ticket never accumulates dots. The rename runs
  in a background task: Discord allows two per channel per ten minutes and a
  claim must not wait that out.
- **`claim_lock`** — only the claimer, the responsibles, the opener and the
  manual participants may write; everyone else on the staff side keeps reading.
- Claiming an **escalated** ticket needs `admin`, not `claim`.
- Claiming and releasing post a notice in the channel.

### 4. Richer ticket topic

`Ticket #{number} · {category} · opened by {user} ({user_id})`.

### 5. Icons

`TICKET` is now the real `<:ticket:…>`, `TICKET_STAFF_THREAD` the real
`<:threads:…>`. `TICKET_CLOSE` → `UNDONE` (a plain cross),
`TICKET_PARTICIPANTS` → `MANAGE_USER`, new `TICKET_CLAIM` → `QUESTIONS`.

### 6. Participants open on who is already in

`TicketParticipantsView` became **`TicketParticipantsModal`** (Modal V2), with
both pickers pre-filled with the current members and roles. One submit applies
the whole picture, which is what makes unselecting the obvious way to remove
somebody.

### 7. The staff thread stays staff-only

`Tickets.on_thread_member_join` removes anyone who joins the private staff
thread without holding `view` on the category — mentioning somebody inside a
thread adds them to it, and one stray ping was enough to hand a ticket's opener
the conversation about them. The **role** grant decides, never presence in the
ticket: the opener and the manual participants hold `view` through the ticket
itself, and the staff thread is precisely the room they must not be in.

### 8. Escalation: keep / keep read-only / remove

Third answer added (`escalation_mute`): the people added by hand follow along
without weighing in. Escalating now also **releases the claim and parks it** in
`pre_escalation_claim`; cancelling the escalation puts the same person back on
the ticket instead of leaving it unassigned.

### 9. Buttons outside the container

Every card in `utils/ticket_views.py` adds its `Container` first and its
`ActionRow`s to the view. Close is red with a cross, **Claim is blue and comes
straight after it**, the rest are grey.

### 10. Request closure is a command

Dropped from the default button set. Still in the catalogue, so a server that
wants it back ticks it.

### 11. The opening message is entirely the admin's

`open_message` is now the **whole** message — title line, body, footer. The
module adds no heading of its own. A line holding only `---` becomes a real
Components V2 separator (`split_message_blocks`). New default:

```
### <:ticket:…> Ticket #{number}
Thanks for opening a ticket. Describe your request as precisely as you can — …
-# Opened by · {user} · `{category}`
```

### 12. The button set is configurable

New `buttons` list on a category, edited from the messages modal. An **empty
list is a real answer** ("commands only") and is stored as such; only a missing
key falls back to the default set. The registered shell declares *every* button
id, since a registered view matches on custom_id and an id it never declared
would be dead after a restart.

### 13. Reopening DMs the opener

The closure was announced in a DM, so its cancellation is too — with a link
back to the channel that had vanished from their list (`build_reopen_dm`).

## Files modified

| File | What |
|---|---|
| `modules/tickets.py` | 2 new permissions, `buttons` / `claim_enabled` / `claim_lock` / `ping_staff_roles`, status dots, `split_message_blocks`, new default open message |
| `services/ticket_service.py` | `ping`, `sync_status_prefix`, claim verbs, claim lock + escalation mute in `build_overwrites`, reopen DM, staff-thread rules |
| `utils/ticket_views.py` | Rewritten: buttons outside containers, configurable button set, claim notice, participants modal, reopen DM |
| `cogs/tickets.py` | `/ticket claim`, `/ticket unclaim`, participants modal, `on_thread_member_join` guard, claim in `/ticket info` |
| `db/base.py` | 4 claim columns + idempotent migration |
| `db/repositories/tickets.py` | `set_claim`, claim-aware `set_escalated` |
| `modules/configs/tickets_category_config.py` | Button picker, `---` hint, behaviour `CheckboxGroup`, claim in the summary |
| `utils/emojis.py`, `docs/EMOJIS.md` | `TICKET`, `THREADS`, `QUESTIONS`, `TICKET_CLAIM` |
| `utils/persistent_views.py` | `TicketParticipantsView` dropped (now a modal) |
| `locales/*.json` (5) | ~80 keys each |
| `locales/commands/*.json` (32) | `/ticket claim`, `/ticket unclaim` |
| `tests/test_tickets.py` | Claim overwrites, status dots, message blocks, button config, participants modal |
| `docs/TICKETS.md`, `docs/PERSISTENT_VIEWS.md`, `CLAUDE.md` | Documentation |

## Decisions and why

- **The status dot is Unicode.** The four coloured circles are the only
  Unicode emojis Moddy uses outside country flags (CLAUDE.md rule 3). A Discord
  channel name cannot carry a custom emoji, so there was no alternative; a
  category with `claim_enabled: false` gets no dot at all.
- **The rename is a background task.** Two renames per channel per ten minutes
  is a hard Discord limit. Blocking the claim on it would make the button hang
  for minutes on a busy ticket; the claim itself is already stored and already
  applied to the permissions.
- **`claim` and `unclaim_others` are separate permissions.** Releasing your own
  ticket is part of holding it; taking a case off a colleague is a different
  decision an agent should not be able to make on their own.
- **The claim lock mutes, it does not hide.** A locked ticket is not a private
  one — the rest of the team must still be able to read it.
- **The three yes/no category settings share one `CheckboxGroup`.** A modal is
  capped at five top-level components, and three separate checkboxes would eat
  the whole budget for what reads as one block of switches.
- **The registered control-bar shell declares every button.** Which buttons a
  guild shows is configurable, and a registered view matches on custom_id.
- **`default_open_message()` substitutes `{icon}` with `str.replace`.** `t()`
  runs `str.format` over the whole string as soon as it gets a kwarg, which
  would eat the `{number}` / `{user}` placeholders the admin keeps.

## Known issues / follow-ups

- `on_thread_member_join` needs the members intent (the bot has it). A member
  added to the staff thread while the bot is down is not evicted retroactively;
  the guard is event-driven only.
- The claim dot means a ticket claimed and released repeatedly can queue
  renames behind Discord's rate limit. The name is eventually consistent by
  design; nothing else depends on it.
- `MAX_TICKET_MESSAGE` stays at 2000 characters now that `open_message` is the
  whole message. Raise it if a server hits the ceiling.

## Tests

`python3 -m pytest tests/ -q` — 1198 passed.
