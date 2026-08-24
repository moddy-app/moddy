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

1. **Provenance.** Under every DM, a button carries the name and icon of the
   server or the Moddy service that sent it. Clicking it identifies the sender,
   links into the server, and shows the notification's uuid.
2. **Reporting.** Next to it, a red flag opens a Modal V2 that recaps the
   message, asks why, and requires an explicit "this report is legitimate"
   confirmation. The report lands in a staff review channel with Claim / See the
   message / Accept / Decline, and every step is logged.
3. **Memory.** Every notification is a row: uuid, sender, recipient, target
   platforms, per-platform delivery status, and enough to rebuild the exact
   wording months later.

---

## The three shapes of a notification

| Source | Buttons under the message | Report flag |
|---|---|---|
| **Official** — account suspension, leaked-token alert | *none at all* | — |
| **Service** — a Moddy feature acting alone (reminder) | `[ Service ]` `[ 🚩 ]` | greyed out (Moddy wrote it) |
| **Server** — a server's own words (welcome DM, sanction reason) | `[ Server ]` `[ 🚩 ]` | **live** |
| **Service + server** — a feature acting for a server (AltGuard, tickets, automod) | `[ Service ]` `[ Server ]` `[ 🚩 ]` | greyed out unless the server wrote the words |

Two rules decide the flag, and they are re-checked on **every click**, not only
at send time:

- **Who wrote the words** (`ContentAuthor`). Only `GUILD`-authored content can
  be abusive. A sanction notice worded by Moddy on a server's behalf is not
  reportable — its exit is the appeal, not the flag.
- **Whose server it is.** A message from a server carrying the `OFFICIAL`
  attribute (one of Moddy's own) is never reportable: reporting Moddy to Moddy
  is a loop with no exit. The flag is rendered disabled, and the identity panel
  says *why* — a dead button with no explanation is a bug.

A verified server (`VERIFIED`, `VERIFIED_ORG` or `PARTNER`) shows the
verification check next to its name, hyperlinked like everywhere else in Moddy
(CLAUDE.md rule #7). An official Moddy server shows it too, plus an explicit
"Official Moddy server" line.

> **Button icons.** A Discord button emoji must be a real emoji — a server icon
> URL cannot go on one. The button therefore carries a generic server icon (or
> the verification check), and the **real** server icon is shown in the panel
> the button opens.

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

1. The recipient clicks 🚩 on their DM. Authorization: the addressee, and only
   them (`may_report`) — for a server-wide notice, that means a member with
   Manage Server.
2. A Modal V2 recaps the source, the uuid and an excerpt, asks for a reason
   (15–1000 chars) and requires a `CheckboxGroup` confirmation.
3. The report is written and posted to `MODDY_NOTIF_REPORT_CHANNEL_ID` as a
   review panel: **Claim**, **See the message**, **Accept**, **Decline**.
4. Every step (created / claimed / accepted / declined) is mirrored to
   `MODDY_NOTIF_REPORT_LOG_CHANNEL_ID`.
5. On a decision, the reporter receives the outcome — as an official
   notification, through this same system.

Reviewer rules, re-checked on every click: the `notif_review` permission node,
and never your own report. A second click on the flag by the same person shows
the status of their existing report instead of opening a new one
(`UNIQUE (notification_id, reporter_id)`).

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

Every button is a `discord.ui.DynamicItem` whose `custom_id` carries the
notification (or report) uuid, registered through `NotificationsPersistence`:

| custom_id | Item | Auth |
|---|---|---|
| `moddy:notif:svc:<uuid>` | service identity panel | public |
| `moddy:notif:src:<uuid>` | server identity panel | public |
| `moddy:notif:flag:<uuid>` | report | the addressee |
| `moddy:notif:rvclaim:<uuid>` | claim | `notif_review`, not the reporter |
| `moddy:notif:rvshow:<uuid>` | see the message | `notif_review`, not the reporter |
| `moddy:notif:rvdec:<accept\|refuse>:<uuid>` | decide | `notif_review`, not the reporter |

A DM sent today must still be reportable after next week's deploy, so nothing
may rely on in-memory state: every callback re-derives the notification from its
uuid and the database. See [PERSISTENT_VIEWS.md](PERSISTENT_VIEWS.md).

---

## Files

```
notifications/
├── __init__.py       # public surface
├── models.py         # content, source, enums, service registry, hashing
├── render.py         # content → Components V2, attribution context
└── service.py        # NotificationService (bot.notifications)

utils/notification_views.py     # attribution row, report modal, review panels
db/repositories/notifications.py
staff/commands/mod/notification.py   # /mod notif
staff/commands/com/send.py           # /com send
tests/test_notifications.py
```

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
