# Bump prospects

An **internal** feature (not a server module): Moddy spots the servers that bump
on a directory (DISBOARD, DiscordL, French.gg…) **without any reminder**, and
puts each one in front of the team as a card in the Moddy team server, so the
team can pitch Moddy's [Bump Reminder](BUMP_REMINDER.md).

## Table of contents

- [What makes a prospect](#what-makes-a-prospect)
- [How servers are looked at](#how-servers-are-looked-at)
- [The card](#the-card)
- [`/dev bumpscan`](#dev-bumpscan)
- [The `bump_prospects` table](#the-bump_prospects-table)
- [Files](#files)
- [Configuration](#configuration)

## What makes a prospect

All three, at once:

1. at least one **bump directory** is a member — any of
   `bumpreminder/registry.py::BUMP_BOTS`;
2. **no third-party reminder bot** is a member —
   `bumpreminder/prospects.py::REMINDER_BOTS` (`478321260481478677`,
   `1441619898756501574`, `735147814878969968`; adding one is a one-line change);
3. Moddy's **Bump Reminder module is not running** — its stored state is neither
   `active` nor `simulation`. A *paused* module still counts as a prospect: the
   server tried it and stopped, which is worth a word.

The team server itself is never a prospect.

## How servers are looked at

`services/bump_prospect_service.py` (`bot.bump_prospects`) owns a **scan queue**
worked by one task, one server per `SCAN_DELAY` (1 s). A look is **one gateway
member query** (`guild.query_members(user_ids=WATCHED_IDS)`) for the ten watched
ids — the startup member chunk is off, so the cache cannot say who is *absent*.
The pace keeps us far below the shard's 120 gateway commands per minute. A
chunked guild is answered from the cache, without a query.

The queue is fed by `cogs/bump_prospects.py`:

| When | What is queued |
|---|---|
| Startup (`on_ready`, once per process) | every server that has **no row yet** |
| Moddy joins a server | that server, first in line |
| A directory or a reminder bot joins / leaves a server | that server, first in line |
| `/dev bumpscan` | every server |

A server is only ever looked up in the database once a directory was found, so
the thousands of servers without one cost one gateway query each and nothing else.

**Follow-up.** Every `FOLLOW_UP_MINUTES` (30), still-open cards are settled:
the module is now running → `converted`; Moddy is no longer in the server →
`left` (also set at once by `on_guild_remove`). A rescan converts an open card
the same way.

## The card

Posted in `MODDY_BUMP_PROSPECT_CHANNEL_ID`, rendered in English like every
staff card (`STAFF_PANEL_LOCALE`), mentions disabled. It shows the server, its
owner, member count, when it was detected, and the directories found.

| Button | Who | What |
|---|---|---|
| **Invite** | `bump_prospect` node | A single-use, 24 h invite, created on click and shown only to the clicker (ephemeral). Nothing is created for servers nobody opens |
| **Claim** | `bump_prospect` node | `open` → `claimed`; the card shows who has it |
| **Unclaim** (same button, once claimed) | the claimant only | back to `open` |
| **Close** | `bump_prospect` node | `open`/`claimed` → `closed` |
| **Reopen** (closed cards) | `bump_prospect` node | back to `open`, or `claimed` if someone held it |

`converted` and `left` cards keep no buttons. Every button is a `DynamicItem`
keyed by the server id (`moddy:bumpprospect:<action>:<guild_id>`, registered by
`BumpProspectPersistence`); a click re-reads the row and re-checks the staff
node. A click on a card that changed in the meantime gets an ephemeral
"already changed" and the card is already up to date.

The **`bump_prospect`** staff node belongs to the Communication role
(`utils/staff_role_permissions.py`); developers, Managers and the super-admin
pass without it.

## `/dev bumpscan`

Queues **every** server Moddy is in (message form: `d.bumpscan`). Answers with
the count and an ETA (one second per server), then posts a summary in the
prospect channel when the queue is empty: servers scanned, new prospects,
converted, not checked (lookup timed out, server unavailable). A rescan never
posts a second card for a server that already has a row — **whatever its
status**: a closed card stays closed.

## The `bump_prospects` table

One row per server, primary key `guild_id` — the key *is* the dedup.

| Column | Meaning |
|---|---|
| `guild_id` | Server (PK) |
| `guild_name` | Name at detection, shown once Moddy left |
| `directories` | `TEXT[]` of registry keys found at detection |
| `status` | `open` / `claimed` / `closed` / `converted` / `left` |
| `claimed_by`, `claimed_at` | Who holds it |
| `closed_by`, `closed_at` | Who closed it |
| `channel_id`, `message_id` | Where the card is, to refresh it |
| `created_at`, `updated_at` | |

Partial index `idx_bump_prospects_active` on `status` for the follow-up.
Repository: `db/repositories/bump_prospects.py` — every transition is one
guarded `UPDATE … WHERE status = …`, so two staffers clicking at once cannot
both win.

## Files

| File | Role |
|---|---|
| `bumpreminder/prospects.py` | Pure: watched ids, reminder bots, `prospect_directories()` |
| `services/bump_prospect_service.py` | Queue, scan, cards, staff actions, follow-up |
| `cogs/bump_prospects.py` | Gateway events, startup pass, follow-up loop |
| `utils/bump_prospect_views.py` | Card, scan summary, persistent buttons |
| `db/repositories/bump_prospects.py` | `bump_prospects` table |
| `staff/commands/dev/bumpscan.py` | `/dev bumpscan` |
| `tests/test_bump_prospects.py` | Detection, dedup, follow-up, card, i18n |

## Configuration

`MODDY_BUMP_PROSPECT_CHANNEL_ID` (default `1564692382107041894`, in the team
server). `0` turns the whole feature off: no scan, no card.
