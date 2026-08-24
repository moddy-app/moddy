# Notifications ↔ backend — integration contract

> What the **backend** and the **dashboard** have to do with the centralized
> notification system: which rows they read, which rows they own, how to render
> the stored payload so a mail and a dashboard card say exactly what the Discord
> DM said, and what they must never touch.
>
> Functional overview: [NOTIFICATIONS.md](NOTIFICATIONS.md).
> Bot implementation: [`notifications/`](../notifications/),
> [`db/repositories/notifications.py`](../db/repositories/notifications.py).
> General bot ↔ backend rules: [BACKEND-INTEGRATION.md](BACKEND-INTEGRATION.md).

The bot and the backend **share the same PostgreSQL database**, so this
integration is a table contract, not an HTTP API. Nothing here needs a call to
the bot.

---

## 0. Who owns what

| Thing | Owner | The other side may |
|---|---|---|
| Creating a notification row | **bot** | read |
| `notification_contents` | **bot** (insert-on-conflict) | read |
| `notifications` | **bot** | read |
| `notification_deliveries` where `platform = 'discord'` | **bot** | read |
| `notification_deliveries` where `platform IN ('email','dashboard')` | **backend** | bot only creates the row, `pending` |
| `notification_reports` | **bot** | read only — see §7 |

One sentence to keep: **the bot writes the message, the backend delivers the
non-Discord half of it.** A row the bot created for `email` or `dashboard` stays
`pending` forever until the backend moves it.

---

## 1. The three tables you read

Full column reference: [DATABASE.md](DATABASE.md) §13–16. What matters here:

### `notifications` — one row per (message, recipient)

```
id              UUID   -- the public reference; the recipient sees it in Discord
batch_id        UUID   -- groups one broadcast; NULL for a one-off
kind            TEXT   -- official | service | guild | service_guild
author          TEXT   -- moddy | guild | staff  (who wrote the words)
source_service  TEXT   -- 'welcome_dm', 'tickets', 'global_sanctions'… or NULL
source_guild_id BIGINT -- the server it was sent on behalf of, or NULL
actor_id        BIGINT -- the human who triggered it, or NULL
recipient_type  TEXT   -- discord_user | discord_guild | all_users | all_guilds | segment | email
recipient_id    BIGINT -- Discord id when the recipient is one
recipient_ref   TEXT   -- non-Discord recipient (segment name, email address)
content_hash    TEXT   -- FK -> notification_contents(hash)
variables       JSONB  -- the values substituted for THIS row
platforms       TEXT[] -- what it targets, default {discord}
reportable      BOOLEAN
locale          TEXT   -- the locale the bot rendered it in
created_at      TIMESTAMPTZ
```

### `notification_contents` — the wording, once

```
hash          TEXT PRIMARY KEY   -- SHA-256 of the canonical template (§4)
payload       JSONB              -- the template, placeholders UNRESOLVED
uses          BIGINT             -- how many notifications used this wording
first_seen_at TIMESTAMPTZ
last_seen_at  TIMESTAMPTZ
```

**This is the part that surprises people:** `payload` is a *template*. Its
strings still contain `{user}`, `{server}`, `{reason}`. Ten thousand members
receiving the same welcome DM share this single row; each `notifications.variables`
holds what was substituted for that one recipient. Render with §3 — never show
`payload` raw to a human.

### `notification_deliveries` — one row per (notification, platform)

```
notification_id UUID     -- FK, ON DELETE CASCADE
platform        TEXT     -- discord | email | dashboard
status          TEXT     -- pending | sent | failed | skipped
channel_id      BIGINT   -- Discord only
message_id      BIGINT   -- Discord only
error           TEXT     -- max 500 chars, free text
updated_at      TIMESTAMPTZ
PRIMARY KEY (notification_id, platform)
```

`status` and `platform` are `CHECK`-constrained: an unknown value is a rejected
insert, not a silently stored typo.

---

## 2. The uniform payload

`notification_contents.payload` always has these keys (nullable, never absent):

| Key | Type | Discord | Mail | Dashboard |
|---|---|---|---|---|
| `title` | string | `### <icon> title` | **subject** | card title |
| `body` | string, markdown | container text | text body | markdown |
| `sections` | `[{title, body}]` | `**title**` + body | labelled blocks | blocks |
| `links` | `[{label, url}]` | link buttons | link list | buttons |
| `footer` | string \| null | `-# footer` | last line | footer |
| `icon` | string | custom emoji | **strip it** | emoji or your own asset |
| `accent_color` | int \| null | container accent | header colour | accent |
| `template_id` | string \| null | — | — | grouping / analytics key |

Example row (`payload`, verbatim — note the unresolved placeholders):

```json
{
  "title": "{server}",
  "body": "Welcome {user} on **{server}**!",
  "icon": "<:waving_hand:1519789691711393982>",
  "accent_color": 5793266,
  "sections": [{"title": "Rules", "body": "Read {channel}"}],
  "links": [{"label": "Open the server", "url": "https://discord.com/channels/{guild_id}"}],
  "footer": "Sent by {server}",
  "template_id": "welcome_dm.wdm_a1b2"
}
```

With `variables = {"server":"Moddy","user":"<@7>","channel":"#rules","guild_id":"42"}`:

```json
// mail shape
{
  "subject": "Moddy",
  "text": "Welcome <@7> on **Moddy**!\n\nRules\nRead #rules\n\nSent by Moddy",
  "links": [{"label": "Open the server", "url": "https://discord.com/channels/42"}]
}

// dashboard shape
{
  "title": "Moddy",
  "body": "Welcome <@7> on **Moddy**!",
  "icon": "<:waving_hand:1519789691711393982>",
  "accent_color": 5793266,
  "sections": [{"title": "Rules", "body": "Read #rules"}],
  "links": [{"label": "Open the server", "url": "https://discord.com/channels/42"}],
  "footer": "Sent by Moddy",
  "template_id": "welcome_dm.wdm_a1b2"
}
```

### Rendering rules that are not negotiable

- **Strip Discord custom emojis outside Discord.** `<:done:123>` and
  `<a:spin:456>` are literal noise in an inbox. Regex:
  `/<a?:[a-zA-Z0-9_]+:\d+>/g`, then collapse runs of 2+ spaces.
- **`body` and `sections[].body` are markdown**, and they can carry
  Discord-specific syntax the bot's own callers wrote: `<@123>` (user mention),
  `<#123>` (channel), `<t:1700000000:R>` (relative timestamp), `-#` (small
  text). Decide per surface: the dashboard can resolve mentions and timestamps;
  a mail should degrade them to plain text rather than print raw syntax.
- **`accent_color` is an int**, not a CSS string: `5793266` → `#5865F2`
  (`"#%06X" % value`).
- **`links[].url` is already substituted** — it may contain placeholders before
  rendering, never after.
- **Never trust the text.** `body` frequently contains words a *server admin*
  typed (a welcome DM, a sanction reason). Escape it for your surface; a mail
  template that interpolates it into HTML unescaped is an injection.

---

## 3. Placeholder substitution — the exact algorithm

Must match the bot character for character, or a staff member comparing the
dashboard with the Discord DM will find two different messages.

`notifications/models.py::substitute()`:

1. Pattern: `\{([a-zA-Z0-9_]+)\}` — letters, digits, underscore only.
2. Key **present** in `variables` → replaced by `str(value)`; `None` → `""`.
3. Key **absent** → the placeholder is **left as-is**, braces included. Do not
   blank it: a visible `{oops}` is how a broken template gets noticed.
4. Everything else is copied verbatim. Never use a format/template engine that
   throws on an unknown or malformed brace — this text is arbitrary and a lone
   `{` must not break a render.

```python
import re

_PLACEHOLDER = re.compile(r"\{([a-zA-Z0-9_]+)\}")

def substitute(text: str | None, variables: dict) -> str:
    if not text:
        return ""
    if not variables:
        return text
    def _replace(m):
        key = m.group(1)
        if key in variables:
            value = variables[key]
            return "" if value is None else str(value)
        return m.group(0)
    return _PLACEHOLDER.sub(_replace, text)
```

Apply it to `title`, `body`, every `sections[].title` / `sections[].body`,
every `links[].label` / `links[].url`, and `footer`. **Not** to `icon`,
`accent_color` or `template_id`.

---

## 4. The content hash

Only needed if the backend ever **writes** a notification itself (it should not
— see §6). Reading never requires recomputing it.

`hash = sha256(canonical_json(payload))` where `canonical_json` is:

- JSON of the eight payload keys,
- `sort_keys=True` — recursively, so `{"title","body"}` inside `sections[]` is
  sorted too,
- `ensure_ascii=False` (real UTF-8, not `\uXXXX`),
- separators `,` and `:` — **no spaces**,
- encoded UTF-8.

```
{"accent_color":5793266,"body":"Welcome {user} on **{server}**!","footer":"Sent by {server}","icon":"<:waving_hand:1519789691711393982>","links":[{"label":"Open the server","url":"https://discord.com/channels/{guild_id}"}],"sections":[{"body":"Read {channel}","title":"Rules"}],"template_id":"welcome_dm.wdm_a1b2","title":"{server}"}
```

→ `398513a4c1c0e3ec2bf56a22fad587f30dccbb544a80d74fc510b5a043f1352b`

The hash is over the **template**, never over the rendered text. That is the
whole point: it is what makes ten thousand identical DMs one row.

---

## 5. What the backend must implement

### 5.1 The delivery loop (mail, dashboard)

A notification targeting your platform is a `notification_deliveries` row with
`status = 'pending'`. Claim it, deliver it, mark it. One query:

```sql
-- Claim a batch, skipping rows another worker holds.
WITH claimed AS (
    SELECT d.notification_id
    FROM notification_deliveries d
    WHERE d.platform = $1          -- 'email' | 'dashboard'
      AND d.status = 'pending'
    ORDER BY d.updated_at
    FOR UPDATE SKIP LOCKED
    LIMIT $2
)
SELECT n.id, n.recipient_type, n.recipient_id, n.recipient_ref, n.locale,
       n.variables, n.kind, n.author, n.source_service, n.source_guild_id,
       n.created_at, c.payload
FROM claimed
JOIN notifications n ON n.id = claimed.notification_id
JOIN notification_contents c ON c.hash = n.content_hash;
```

Then, per row:

```sql
INSERT INTO notification_deliveries
    (notification_id, platform, status, error, updated_at)
VALUES ($1, $2, $3, $4, now())
ON CONFLICT (notification_id, platform) DO UPDATE
   SET status = EXCLUDED.status,
       error = EXCLUDED.error,
       updated_at = now();
```

| Use | When |
|---|---|
| `sent` | the platform accepted it |
| `failed` | it was attempted and refused — put the reason in `error` (≤ 500 chars) |
| `skipped` | deliberately not attempted: no email on file, user opted out, unsupported recipient type. **Not** an error |

Leave `channel_id` / `message_id` `NULL` — they are Discord's.

Retries are yours to decide; the schema does not count attempts. If you need
one, add a backend-side table rather than a column here — the bot rewrites this
row on its own schedule for Discord.

> **Today, in practice.** Every bot caller currently sends with the default
> `platforms = {discord}`, so there are **no** pending `email` / `dashboard`
> rows yet. Build the loop anyway: the moment a caller opts a notification into
> another platform, its row appears with no further code change on the bot side.
> The dashboard *inbox* below does not wait for this.

### 5.2 The dashboard inbox

Independent of `platforms`: a user's notification history is simply their rows.

```sql
SELECT n.id, n.kind, n.author, n.source_service, n.source_guild_id,
       n.locale, n.variables, n.created_at, c.payload,
       d.status AS discord_status, d.message_id
FROM notifications n
JOIN notification_contents c ON c.hash = n.content_hash
LEFT JOIN notification_deliveries d
       ON d.notification_id = n.id AND d.platform = 'discord'
WHERE n.recipient_type = 'discord_user'
  AND n.recipient_id = $1
ORDER BY n.created_at DESC
LIMIT $2 OFFSET $3;
```

`idx_notifications_recipient` on `(recipient_id, created_at DESC)` covers it.

Render each row with §2 + §3. In Discord the origin is one greyed line at the
bottom of the card (`-# Sent by [**Server**](link) (`id`)`, plus the
verification check when the server has one). Show the same thing, so the two
surfaces agree:

- `source_service` → a service label (the bot's own names live in
  `locales/<locale>.json` → `notifications.services.<id>`),
- `source_guild_id` → the server (name/icon from your own guild cache),
- `kind = 'official'` → Moddy itself; show no "report" affordance at all,
- `reportable = false` → no report affordance either, and if you explain why,
  match the bot's two reasons: the wording is Moddy's, or the server is an
  official Moddy server.

> **The dashboard is now the only place a report can be filed.** The flag
> button that used to sit under a DM was removed; the pipeline behind it is
> intact (`notification_reports`, the staff review panel in Discord, the
> outcome DM) and only lacks a trigger. That trigger cannot be an `INSERT`
> from the backend — filing a report also posts the review panel and logs it,
> both bot-side. It needs the `notification_send`-style task of §6, extended
> with a `notification_report` type. Until that exists, `reportable` is
> information you display, not an action you can offer.

### 5.3 Campaign status

A broadcast is one `batch_id` across many rows:

```sql
SELECT d.platform, d.status, COUNT(*) AS total
FROM notifications n
JOIN notification_deliveries d ON d.notification_id = n.id
WHERE n.batch_id = $1
GROUP BY d.platform, d.status;
```

`idx_notifications_batch` covers the lookup. `recipient_ref` on those rows
carries the segment the campaign targeted (`all`, `PREMIUM`, …).

---

## 6. Backend → bot: asking for a Discord send

**Not implemented.** There is no task type today that makes the bot send a
notification, and the backend must not insert `notifications` rows itself: the
uuid has to exist *before* the DM is sent, because the attribution and report
buttons carry it in their `custom_id`. A row written by the backend would
describe a message nobody received.

Two consequences worth knowing:

- The existing `send_announcement` task
  ([`bot.py::_process_task`](../bot.py)) predates this system. It posts raw text
  to `guild.system_channel` and is **not** recorded, **not** attributed and
  **not** reportable. Treat it as legacy; do not build on it.
- Anything the team wants to send today goes out through `/com send` (Discord,
  staff-side).

When this is wired up it will be a `moddy:tasks` entry like every other critical
task — signed, deduplicated, replayable
([TASK_SIGNATURE.md](TASK_SIGNATURE.md)) — with roughly this payload:

```jsonc
// type: "notification_send"   — PROPOSED, NOT IMPLEMENTED
{
  "target": {"type": "user", "id": "123456789012345678"},   // or guild / segment
  "content": {"title": "…", "body": "…", "links": [], "footer": null},
  "variables": {},
  "platforms": ["discord", "dashboard"],
  "source": {"kind": "service", "service_id": "moddy", "author": "staff",
             "actor_id": "987654321098765432"},
  "locale": "fr"
}
```

Do not implement against that shape until it lands in the bot — it is written
here so the two sides design it once, not so it can be shipped early.

---

## 7. Abuse reports

`notification_reports` is **read-only for the backend**. A decision is not a
column update: accepting or declining a report also edits the review panel in
Discord, writes the report log channel, and DMs the reporter — all bot-side.
Writing `status` directly desynchronises the three.

Read it freely (a staff dashboard listing open reports is a good idea):

```sql
SELECT r.id, r.notification_id, r.reporter_id, r.reason, r.status,
       r.claimed_by, r.decided_by, r.decision_note, r.created_at,
       n.source_guild_id, n.source_service, n.recipient_id, c.payload, n.variables
FROM notification_reports r
JOIN notifications n ON n.id = r.notification_id
JOIN notification_contents c ON c.hash = n.content_hash
WHERE r.status IN ('pending', 'claimed')
ORDER BY r.created_at;
```

`status` is `pending` → `claimed` → `accepted` | `refused`.
`UNIQUE (notification_id, reporter_id)`: one report per person per notification,
so a count of rows is a count of distinct reporters.

---

## 8. Invariants

Breaking one of these does not raise; it produces a wrong message or a dead
button in someone's DMs.

1. **Never make a notification more reportable than it was recorded.**
   `reportable` is frozen at send time. The bot only ever tightens it at click
   time (a server marked `OFFICIAL` later). Anything you display must follow
   the same direction.
2. **Never mutate `notification_contents.payload`.** Its hash is its primary
   key and thousands of rows point at it; editing it rewrites history for every
   one of them. A new wording is a new hash — that is automatic on the bot side.
3. **Never mutate `notifications.variables`** after the fact: it is the record
   of what a specific person was actually shown.
4. **Never delete rows.** `notification_deliveries` and `notification_reports`
   cascade from `notifications`; a delete erases the evidence behind an open
   abuse report. Add a retention job only with a deliberate policy, and never
   delete a notification that has a report.
5. **The uuid is safe to display.** It is the reference staff look a
   notification up by (`/mod notif`), and the natural id for a dashboard
   report control. It is not a secret and must not be treated as one — but the
   Discord DM does not show it, so a user who has only seen the DM cannot
   quote it: identify their notification by recipient and date.
6. **`recipient_id` is a Discord snowflake in a BIGINT.** JavaScript loses
   precision above 2^53 — serialise it as a string in every API response, like
   everywhere else in Moddy.
7. **Localise from `locale`, not from the reader.** The row carries the locale
   the message was rendered in; re-rendering a French DM's chrome in English
   next to French body text is the mistake this column exists to prevent.

---

## 9. Checklist

- [ ] Read path: `notifications` ⋈ `notification_contents` ⋈ `notification_deliveries`
- [ ] Substitution implemented exactly as §3 (unknown key left visible, `None` → empty)
- [ ] Custom emojis stripped for mail; markdown escaped per surface
- [ ] `accent_color` int → hex
- [ ] Snowflakes serialised as strings
- [ ] Delivery loop claims with `FOR UPDATE SKIP LOCKED` and marks
      `sent` / `failed` / `skipped` (never leaves a row `pending` after handling it)
- [ ] Dashboard inbox shows source + verification exactly like Discord, and no
      report affordance when `kind = 'official'` or `reportable = false`
- [ ] `notification_reports` treated as read-only
- [ ] No writes to `notifications`, `notification_contents`, or Discord delivery rows

---

## 10. Related

- [NOTIFICATIONS.md](NOTIFICATIONS.md) — the system itself, and the Discord side
- [DATABASE.md](DATABASE.md) §13–16 — full column reference
- [BACKEND-INTEGRATION.md](BACKEND-INTEGRATION.md) — Redis, streams, shared DB rules
- [TASK_SIGNATURE.md](TASK_SIGNATURE.md) — signing `moddy:tasks` entries, for §6
