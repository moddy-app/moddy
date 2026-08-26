# Centralized Notifications

> Every message Moddy sends to a human — a DM, a mail, a card on the dashboard —
> is a **notification**: stored, attributable, and (when someone other than
> Moddy wrote it) reportable.

**Nothing in the bot calls `user.send(...)` directly any more.** A feature says
*what* it wants to say and *who is behind it*; the system does the rest.

---

## Why

Before this, a member who received an abusive welcome DM had no way to know
which server sent it, and no way to tell us. And nothing was recorded: we could
not answer "what did we send this person, and when".

Three answers, one system:

1. **Provenance.** Every DM closes with one greyed line naming its origin:

   ```
   -# Sent by [**Server name**](https://discord.com/channels/1421493239579676682) (`1421493239579676682`)
   ```

   The same shape the sanction DMs have always used, now on everything Moddy
   sends. No buttons, no panel to open.
2. **Memory.** Every notification is a row: uuid, sender, recipient, target
   platforms, per-platform delivery status, and enough to rebuild the exact
   wording months later.
3. **Accountability.** An abuse report can be filed against a stored
   notification and reviewed by staff in Discord (Claim / See the message /
   Accept / Decline), with every step logged.

---

## The attribution line

| Source | Line at the bottom of the DM |
|---|---|
| **Official** — account suspension, leaked-token alert | *none* |
| **Service** — a Moddy feature acting alone (reminder, appeal outcome) | `-# Sent by **Reminders**` |
| **Server** — a server's own words (welcome DM, sanction reason) | `-# Sent by [**Server**](link) (`id`)` |
| **Service + server** — a feature acting for a server (AltGuard, tickets, automod) | same as above: the **server** is the origin |

The server is always the origin when there is one; which internal service acted
for it is not something a member needs. A notification with no server at all
names the Moddy service instead, so there is always exactly one origin.

An official notice carries no line: a suspension **is** Moddy speaking, there is
no third party to name.

A verified server (`VERIFIED`, `VERIFIED_ORG` or `PARTNER`) gets the
verification check right after its name, as the plain emoji — **not**
hyperlinked like everywhere else in Moddy (CLAUDE.md rule #7). The hyperlinked
form broke in this context, so `resolve_source_context()` sets `ctx["badge"]`
to the bare `VERIFIED` emoji instead of `format_verification_badge(VERIFIED)`.

The line goes **inside the last container** of the card, not under it: a `-#`
floating as its own component reads as a separate message.

### Cards that already say it

Three senders have printed their own `sent_by` line since long before this
system (the manual sanction DM, the automod sanction DM, the sanction-expiry
DM). They pass `attribution=False` so the line is not printed twice.

---

## Sending a notification

```python
from notifications import NotificationContent, NotificationSource

result = await bot.notifications.send_dm(
    member,
    content=NotificationContent(
        title=guild.name,
        body="Welcome {user} on {server}!",   # placeholders LEFT IN
        icon=WAVING_HAND,
        accent_color=0x5865F2,
        template_id="welcome_dm.wdm_a1b2",
    ),
    source=NotificationSource.guild(guild.id),  # the server wrote this
    variables={"user": member.mention, "server": guild.name},
    locale="fr",
)

if result.forbidden:      # DMs closed — every other send would fail the same
    return
```

Pass `attribution=False` when the card you hand over already ends with its own
`sent_by` line.

`send_dm` returns a `DeliveryResult` (`notification_id`, `message`, `status`,
`error`, plus `delivered` / `forbidden`). It never raises on a delivery
failure: closed DMs are the norm, not an exception.

### Keeping your own card

Features with an established card (a sanction notice, a ticket transcript, the
automod DM with its appeal buttons) pass it as `view=`; the attribution row is
appended to it:

```python
await bot.notifications.send_dm(
    member, content=content, source=source, view=my_view, files=files)
```

The uniform `content` is **still required** even then. It is what the dashboard
renders, what the mail pipeline sends, and what a staff reviewer sees when they
click *See the message*. A `view` without a `content` would be a Discord-only
message that no other surface can show.

### Source constructors

```python
NotificationSource.official("token_detector")            # no buttons at all
NotificationSource.service("reminder")                   # service button only
NotificationSource.guild(guild_id)                       # server + live flag
NotificationSource.service_guild("tickets", guild_id)    # service + server
```

`author=ContentAuthor.GUILD` marks the wording as the server's (reportable);
`ContentAuthor.STAFF` marks a Moddy-team broadcast; the default is
`ContentAuthor.MODDY`. `actor_id=` records the human who triggered the send.

### Other destinations

```python
# A server-wide notice: moddy-updates → Community Updates → system channel,
# optionally the owner too (and always the owner if no channel is usable).
await bot.notifications.notify_guild(guild, content=…, source=…, dm_owner=True)

# Thousands of recipients, one batch_id, paced and resilient.
stats = await bot.notifications.broadcast_users(user_ids, content=…, source=…,
                                                segment="PREMIUM", progress=cb)
stats = await bot.notifications.broadcast_guilds(guild_ids, content=…, source=…)
```

### Exotic senders

The token detector opens the DM channel with the *user's own token* when the
bot cannot. Such a sender records first and marks the outcome itself:

```python
record = await bot.notifications.record(content=…, source=…,
                                        recipient_type=RecipientType.DISCORD_USER,
                                        recipient_id=user_id)
...
await bot.notifications.mark_delivered(record, message)   # or mark_failed(record, "…")
```

---

## The uniform payload

`NotificationContent` is the same object on every platform, which is what makes
"a suspension notice reads the same in Discord, in an inbox and on the
dashboard" true rather than aspirational.

| Field | Discord | Mail | Dashboard |
|---|---|---|---|
| `title` | `### <icon> title` | subject | card title |
| `body` | markdown | text | markdown |
| `sections[]` | `**title**` + body | labelled blocks | blocks |
| `links[]` | link buttons | link list | buttons |
| `footer` | `-# footer` | last line | footer |
| `icon` | custom emoji | *stripped* | emoji string |
| `accent_color` | container accent | header colour | accent |

`to_email()` strips custom emojis (`<:done:123>` is literal noise in an inbox);
`to_dashboard()` keeps the markdown as-is.

### Placeholders and the content hash

**Strings keep their `{placeholders}`; the resolved values travel next to them
as `variables`.** This is the single most important convention in the system:

- `template_hash()` is the SHA-256 of the *unresolved* template, so ten thousand
  welcome DMs of the same server share **one** `notification_contents` row.
- The exact wording of any single notification is `content.render(variables)`,
  reproducible months later — that is the staff preview and the report evidence.

Substitution is `notifications.models.substitute()`, never `str.format`: server
text is arbitrary and a stray `{` must not raise mid-delivery. Unknown
placeholders are left visible rather than silently blanked.

---

## Database

Four tables (DDL in `db/base.py::_init_tables`, queries in
`db/repositories/notifications.py`):

| Table | One row per | Notable columns |
|---|---|---|
| `notification_contents` | template body | `hash` (PK), `payload`, `uses` |
| `notifications` | (message, recipient) | `id` (uuid), `batch_id`, `kind`, `author`, `source_service`, `source_guild_id`, `actor_id`, `recipient_type`, `recipient_id`, `recipient_ref`, `content_hash`, `variables`, `platforms[]`, `reportable`, `locale` |
| `notification_deliveries` | (notification, platform) | PK `(notification_id, platform)`, `status`, `channel_id`, `message_id`, `error` |
| `notification_reports` | abuse report | `id`, `notification_id`, `reporter_id`, `reason`, `status`, `claimed_by`, `decided_by`, `decision_note`, `review_channel_id`, `review_message_id`; `UNIQUE (notification_id, reporter_id)` |

`recipient_type` is `discord_user` | `discord_guild` | `all_users` | `all_guilds`
| `segment` | `email`. A broadcast is exploded into one row per recipient sharing
a `batch_id`, so "who did this campaign reach, and how did it go" is one query
(`get_batch_stats`).

`platforms[]` is what the notification *targets*; `notification_deliveries` is
what actually happened on each. The bot delivers Discord; the mail and dashboard
rows stay `pending` for the backend to pick up and mark.

`reportable` is frozen on the row at send time. The row's flag can only ever
make a notification *less* reportable, never more: a server marked official
later must not resurrect old flags.

---

## Reporting flow

> **No entry point in Discord today.** The flag button that used to open a
> report was removed along with the rest of the DM-side buttons. The pipeline
> below is intact and reachable through `NotificationService.open_report(...)`;
> what is missing is something that calls it — a report control on the
> dashboard being the obvious candidate. Everything downstream already works.

1. `open_report(notification_id, reporter, reason)` writes the report. Who may
   file one is `may_report()`: the addressee, and only them — for a server-wide
   notice, a member with Manage Server.
2. The report is posted to `MODDY_NOTIF_REPORT_CHANNEL_ID` as a review panel:
   **Claim**, **See the message**, **Accept**, **Decline**.
3. Every step (created / claimed / accepted / declined) is mirrored to
   `MODDY_NOTIF_REPORT_LOG_CHANNEL_ID`.
4. On a decision, the reporter receives the outcome — as an official
   notification, through this same system.

Reviewer rules, re-checked on every click: the `notif_review` permission node,
and never your own report. `UNIQUE (notification_id, reporter_id)` keeps it to
one report per person per notification.

`reportable` is still computed and frozen on every row (`ContentAuthor.GUILD`
wording, minus messages from `OFFICIAL` Moddy servers), so whatever surface
grows the entry point already has its answer.

Configuration:

| Env var | Default | Purpose |
|---|---|---|
| `MODDY_NOTIF_REPORT_CHANNEL_ID` | `1541231528754028594` | where reports are reviewed |
| `MODDY_NOTIF_REPORT_LOG_CHANNEL_ID` | `1541233478522241034` | where every step is logged |

---

## Staff commands

### `/mod notif <reference>` — look one up

Takes a notification uuid **or** a report reference (staff copy whichever id is
in front of them) and shows everything: origin, reportability and why, recipient
and batch, per-platform delivery with message ids, the resolved content, the
template hash and how many times that exact wording has been sent, plus every
report filed against it. Node: `notif_lookup`.

### `/com send <target> <recipient> [dm_owner]` — send one

| `target` | `recipient` | Result |
|---|---|---|
| `user` | a user id | one DM |
| `guild` | a server id | the server's Moddy channel, optionally its owner |
| `users` | `all`, or an attribute (`PREMIUM`, `BETA`…) | a DM per user |
| `guilds` | `all`, or an attribute (`OFFICIAL`, `PARTNER`…) | a notice per server |

The wording is written in a Modal V2 (title, body, optional button label + URL)
and stored as a uniform payload, so the same announcement can be rendered by the
dashboard and the mail pipeline without being retyped. Group sends are confirmed
first (with a warning past 500 recipients), then run in the background with a
live progress panel, paced by `BROADCAST_DELAY`. Node: `broadcast`.

---

## Persistence

A member's DM carries no component at all — its origin is plain text, which
cannot break, expire or lose its handler.

The staff review panel does, and every one of its buttons is a
`discord.ui.DynamicItem` whose `custom_id` carries the report uuid, registered
through `NotificationsPersistence`:

| custom_id | Item | Auth |
|---|---|---|
| `moddy:notif:rvclaim:<uuid>` | claim | `notif_review`, not the reporter |
| `moddy:notif:rvshow:<uuid>` | see the message | `notif_review`, not the reporter |
| `moddy:notif:rvdec:<accept\|refuse>:<uuid>` | decide | `notif_review`, not the reporter |

A report opened today must still be decidable after next week's deploy, so
nothing may rely on in-memory state: every callback re-derives the report from
its uuid and the database. See [PERSISTENT_VIEWS.md](PERSISTENT_VIEWS.md).

---

## Files

```
notifications/
├── __init__.py       # public surface
├── models.py         # content, source, enums, service registry, hashing
├── render.py         # content → Components V2, attribution context
└── service.py        # NotificationService (bot.notifications)

utils/notification_views.py     # staff review panel, decision modal, report log
db/repositories/notifications.py
staff/commands/mod/notification.py   # /mod notif
staff/commands/com/send.py           # /com send
tests/test_notifications.py
```

---

## Backend & dashboard

The mail and dashboard halves are delivered by the backend, from the same rows:
the bot creates their `notification_deliveries` entries as `pending` and never
touches them again. The full implementation guide for that side — schema and
enums, the rendering and placeholder algorithms it must match character for
character, the content hash, the read models, the delivery worker, the
dashboard inbox and its authorisation rules, security and test vectors — is in
[NOTIFICATIONS_INTEGRATION.md](NOTIFICATIONS_INTEGRATION.md).

---

## Adding a sender

1. Register the service in `notifications/models.py::SERVICES` (one line) and
   add its name under `notifications.services.<id>` in the five locale files.
2. Build a `NotificationContent` — **keep the placeholders in**, pass the values
   as `variables`.
3. Pick the source constructor that tells the truth about who wrote the words.
   If in doubt: did a human outside Moddy type this text? If yes,
   `ContentAuthor.GUILD`; if no, the default.
4. Call `bot.notifications.send_dm(...)` and act on the `DeliveryResult`.

Never call `.send()` on a user object directly. If a delivery path genuinely
cannot go through `send_dm`, use `record()` + `mark_delivered()` /
`mark_failed()` so the notification still exists in the record.
