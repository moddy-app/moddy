# Notifications — backend implementation guide

> Everything the **backend** (and the **dashboard** behind it) has to build to
> take part in Moddy's centralized notification system: the exact schema, the
> exact rendering algorithm, the delivery worker, the read models, the API
> shapes, the security rules and the acceptance tests.
>
> Functional overview (the bot's side): [NOTIFICATIONS.md](NOTIFICATIONS.md).
> Bot implementation: [`notifications/`](../notifications/),
> [`db/repositories/notifications.py`](../db/repositories/notifications.py),
> DDL in [`db/base.py`](../db/base.py)`::_init_tables`.
> General bot ↔ backend rules: [BACKEND-INTEGRATION.md](BACKEND-INTEGRATION.md).

The bot and the backend **share the same PostgreSQL database**, so this
integration is a *table contract*, not an HTTP API. Nothing here requires a call
to the bot, and the bot never calls the backend.

**Contents**

| § | |
|---|---|
| [0](#0-mental-model) | Mental model — what a notification is |
| [1](#1-who-owns-what) | Ownership matrix |
| [2](#2-the-schema-verbatim) | The schema, verbatim |
| [3](#3-every-legal-value) | Every legal value (enums + service registry) |
| [4](#4-the-uniform-payload) | The uniform payload |
| [5](#5-placeholder-substitution--the-exact-algorithm) | Placeholder substitution — the exact algorithm |
| [6](#6-rendering-to-mail-and-to-the-dashboard) | Rendering to mail and to the dashboard |
| [7](#7-the-content-hash) | The content hash |
| [8](#8-read-models--the-queries-you-will-actually-run) | Read models — the queries you will actually run |
| [9](#9-the-delivery-worker) | The delivery worker |
| [10](#10-the-dashboard-inbox) | The dashboard inbox |
| [11](#11-abuse-reports-read-only) | Abuse reports (read-only) |
| [12](#12-suggested-http-api-shapes) | Suggested HTTP API shapes |
| [13](#13-backend--bot-asking-for-a-discord-send) | Backend → bot: asking for a Discord send |
| [14](#14-performance) | Performance |
| [15](#15-security-and-privacy) | Security and privacy |
| [16](#16-observability) | Observability |
| [17](#17-invariants) | Invariants |
| [18](#18-test-vectors) | Test vectors |
| [19](#19-implementation-checklist) | Implementation checklist |
| [20](#20-faq) | FAQ |
| [21](#21-related) | Related documents |

---

## 0. Mental model

Everything Moddy says to a human — a Discord DM, a server-wide notice, a mail,
a card on the dashboard — is **one notification**: a row with a uuid, a
template, the variables that were substituted into it for that one recipient,
and one delivery row per target platform.

Three facts explain every design decision below:

1. **The stored content is a *template*, not a finished message.** Its strings
   still contain `{user}`, `{server}`, `{reason}`. Ten thousand members of the
   same server receiving the same welcome DM share **one** content row; each
   `notifications.variables` holds what was substituted for that one recipient.
   Rendering is your job (§5–6).
2. **`platforms` is intent, `notification_deliveries` is fact.** The bot writes
   one `pending` delivery row per targeted platform and then only ever touches
   the `discord` one. An `email` / `dashboard` row stays `pending` **forever**
   until your worker moves it.
3. **The uuid exists before the message is sent.** It is the reference staff
   look up (`/mod notif`), the anchor of an abuse report, and the natural id of
   a dashboard card. That is why the backend must never insert `notifications`
   rows itself (§13).

```
                    ┌──────────────────────────┐
   bot feature ───► │ NotificationService      │
   (welcome_dm,     │  record() → uuid         │
    tickets, …)     └───────────┬──────────────┘
                                │ writes
              ┌─────────────────┼──────────────────────────┐
              ▼                 ▼                          ▼
   notification_contents   notifications        notification_deliveries
     (template, hashed)   (1 per recipient)     discord=pending → bot
                                                email   =pending → BACKEND
                                                dashboard=pending → BACKEND
```

---

## 1. Who owns what

| Thing | Owner | The other side may |
|---|---|---|
| Creating a notification row | **bot** | read |
| `notification_contents` | **bot** (insert-on-conflict, bumps `uses`) | read |
| `notifications` | **bot** | read |
| `notification_deliveries` where `platform = 'discord'` | **bot** | read |
| `notification_deliveries` where `platform IN ('email','dashboard')` | **backend** | bot only creates the row, `pending` |
| `notification_reports` | **bot** | read only — see §11 |
| Rendering for mail / dashboard | **backend** | — |
| Deciding an abuse report | **bot** (Discord staff panel) | read the outcome |

One sentence to keep: **the bot writes the message, the backend delivers the
non-Discord half of it.**

---

## 2. The schema, verbatim

Created by the bot at startup (`db/base.py::_init_tables`). The backend runs no
migration on these tables — if a column is missing in your environment, the bot
has not booted against that database yet.

```sql
CREATE TABLE IF NOT EXISTS notification_contents (
    hash          TEXT PRIMARY KEY,                    -- SHA-256 of the canonical template (§7)
    payload       JSONB NOT NULL,                      -- the template, placeholders UNRESOLVED
    uses          BIGINT NOT NULL DEFAULT 0,           -- how many notifications used this wording
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS notifications (
    id              UUID PRIMARY KEY,                  -- the public reference
    batch_id        UUID,                              -- groups one broadcast; NULL for a one-off
    kind            TEXT NOT NULL
        CHECK (kind IN ('official','service','guild','service_guild')),
    author          TEXT NOT NULL DEFAULT 'moddy'
        CHECK (author IN ('moddy','guild','staff')),
    source_service  TEXT,                              -- service id (§3.5) or NULL
    source_guild_id BIGINT,                            -- the server it was sent for, or NULL
    actor_id        BIGINT,                            -- the human who triggered it, or NULL
    recipient_type  TEXT NOT NULL,                     -- §3.3
    recipient_id    BIGINT,                            -- Discord id when the recipient is one
    recipient_ref   TEXT,                              -- segment name / email address
    content_hash    TEXT NOT NULL REFERENCES notification_contents(hash),
    variables       JSONB NOT NULL DEFAULT '{}'::jsonb,
    platforms       TEXT[] NOT NULL DEFAULT ARRAY['discord'],
    reportable      BOOLEAN NOT NULL DEFAULT FALSE,
    locale          TEXT,                              -- the locale the bot rendered it in
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_notifications_recipient ON notifications (recipient_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_guild     ON notifications (source_guild_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_batch     ON notifications (batch_id);
CREATE INDEX IF NOT EXISTS idx_notifications_content   ON notifications (content_hash);

CREATE TABLE IF NOT EXISTS notification_deliveries (
    notification_id UUID NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
    platform        TEXT NOT NULL
        CHECK (platform IN ('discord','email','dashboard')),
    status          TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','sent','failed','skipped')),
    channel_id      BIGINT,                            -- Discord only
    message_id      BIGINT,                            -- Discord only
    error           TEXT,                              -- ≤ 500 chars, free text
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (notification_id, platform)
);
CREATE INDEX IF NOT EXISTS idx_notification_deliveries_status ON notification_deliveries (platform, status);

CREATE TABLE IF NOT EXISTS notification_reports (
    id                UUID PRIMARY KEY,
    notification_id   UUID NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
    reporter_id       BIGINT NOT NULL,
    reason            TEXT NOT NULL,                   -- truncated to 1000 chars by the bot
    status            TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','claimed','accepted','refused')),
    claimed_by        BIGINT,
    claimed_at        TIMESTAMPTZ,
    decided_by        BIGINT,
    decided_at        TIMESTAMPTZ,
    decision_note     TEXT,
    review_channel_id BIGINT,                          -- the Discord review panel
    review_message_id BIGINT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT notification_reports_unique UNIQUE (notification_id, reporter_id)
);
CREATE INDEX IF NOT EXISTS idx_notification_reports_status ON notification_reports (status, created_at DESC);
```

Notes that save debugging time:

- `kind`, `author`, `platform`, `status` are **`CHECK`-constrained**: an unknown
  value is a rejected insert, not a silently stored typo. `recipient_type` is
  *not* constrained in SQL — validate it in code against §3.3.
- `content_hash` is a real FK: a notification can never point at a wording that
  does not exist, and `notification_contents` rows must never be garbage
  collected while a notification references them.
- Both child tables cascade from `notifications`. Deleting a notification
  destroys its delivery history **and** any abuse report filed against it (§17).
- Every timestamp is `TIMESTAMPTZ`, stored UTC. Serialise as ISO-8601 with a `Z`.

Column reference in prose: [DATABASE.md](DATABASE.md) §13–16.

---

## 3. Every legal value

### 3.1 `kind` — what sort of actor is behind the message

| Value | Meaning | Attribution shown in Discord |
|---|---|---|
| `official` | Moddy speaking as an institution (account suspension, leaked-token alert) | **none** |
| `service` | a Moddy feature acting alone (reminder, appeal outcome) | `Sent by **Reminders**` |
| `guild` | a server's own words, through Moddy (welcome DM) | `Sent by [**Server**](link) (`id`)` |
| `service_guild` | a Moddy feature acting for a server (tickets, AltGuard, automod) | same as `guild` — the **server** is the origin |

### 3.2 `author` — who actually wrote the words

| Value | Meaning | Consequence |
|---|---|---|
| `moddy` | Moddy wrote the wording (even when acting for a server) | not reportable |
| `guild` | a server admin typed this text | **reportable** (unless the server is an official Moddy server) |
| `staff` | a Moddy-team broadcast (`/com send`) | not reportable |

`reportable` is computed from `author` (plus the guild's `OFFICIAL` attribute)
and **frozen on the row at send time**. Never recompute it.

### 3.3 `recipient_type`

| Value | `recipient_id` | `recipient_ref` |
|---|---|---|
| `discord_user` | the user id | the segment, on a broadcast row |
| `discord_guild` | the guild id | the segment, on a broadcast row |
| `all_users` / `all_guilds` / `segment` | — | the audience label |
| `email` | — | the email address |

A broadcast is **exploded into one row per recipient** sharing a `batch_id`, so
in practice a delivered row is `discord_user` or `discord_guild`; the other
three describe the audience a campaign was aimed at.

### 3.4 `platform` / `status`

`platform` ∈ `discord` | `email` | `dashboard`.

| `status` | Meaning | Who sets it |
|---|---|---|
| `pending` | targeted, not attempted yet | bot, at creation |
| `sent` | the platform accepted it | whoever delivered |
| `failed` | attempted and refused — reason in `error` | whoever delivered |
| `skipped` | deliberately not attempted (no email on file, opted out, unsupported recipient type) — **not** an error | whoever delivered |

### 3.5 The service registry

`source_service` is one of these ids (`notifications/models.py::SERVICES`). The
human label lives in `locales/<locale>.json` → `notifications.services.<id>` —
mirror those strings rather than inventing your own:

| id | Label (en-US) | Sender |
|---|---|---|
| `moddy` | Moddy | Moddy itself / staff broadcasts (`/com send`) |
| `welcome_dm` | Welcome message | `modules/welcome_dm.py` |
| `moderation` | Moderation | manual sanctions |
| `automod_ai` | Automod AI | `modules/automod_ai.py` |
| `appeals` | Appeals | `services/appeal_service.py` |
| `altguard` | AltGuard | `modules/altguard.py` |
| `tickets` | Tickets | `services/ticket_service.py` |
| `reminder` | Reminders | `cogs/reminder.py` |
| `interserver` | Inter-server | `modules/interserver.py` |
| `token_detector` | Token detector | `cogs/token_detector.py` |
| `global_sanctions` | Global sanctions | `services/global_sanction_service.py` |
| `expirations` | Sanction expiry | `services/expiration_notifier.py` |

**Treat this list as open.** A new sender is one line in `SERVICES`; an id you
do not know must degrade to a generic "Moddy" label, never to an error.

### 3.6 `locale`

The locale the message was rendered in (`fr`, `en-US`, `es-ES`, `pt-BR`, `de`,
or a raw Discord locale string). It may be `NULL` — fall back to `en-US`.
**Localise your chrome from this column, not from the reader's browser** (§17.7).

---

## 4. The uniform payload

`notification_contents.payload` always has these eight keys (nullable, never
absent):

| Key | Type | Discord | Mail | Dashboard |
|---|---|---|---|---|
| `title` | string | `### <icon> title` | **subject** | card title |
| `body` | string, markdown | container text | text body | markdown |
| `sections` | `[{title, body}]` | `**title**` + body | labelled blocks | blocks |
| `links` | `[{label, url}]` | link buttons (max 5) | link list | buttons |
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
// mail shape  (NotificationContent.to_email)
{
  "subject": "Moddy",
  "text": "Welcome <@7> on **Moddy**!\n\nRules\nRead #rules\n\nSent by Moddy",
  "links": [{"label": "Open the server", "url": "https://discord.com/channels/42"}]
}

// dashboard shape  (NotificationContent.to_dashboard)
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

`template_id` is a stable id of the wording's origin (`welcome_dm.wdm_a1b2`,
`global_sanctions.notice`). Two different `content_hash` values can share a
`template_id` — it is the right key for "how does this template perform",
whereas `content_hash` is "this exact wording".

---

## 5. Placeholder substitution — the exact algorithm

This must match the bot character for character, or a staff member comparing the
dashboard with the Discord DM will find two different messages.

`notifications/models.py::substitute()`:

1. Pattern: `\{([a-zA-Z0-9_]+)\}` — letters, digits, underscore only.
2. Key **present** in `variables` → replaced by `str(value)`; `None`/`null` → `""`.
3. Key **absent** → the placeholder is **left as-is**, braces included. Do not
   blank it: a visible `{oops}` is how a broken template gets noticed.
4. Everything else is copied verbatim. Never use a format/template engine that
   throws on an unknown or malformed brace — this text is arbitrary and a lone
   `{` must not break a render. No recursion: a substituted value containing
   `{x}` is **not** substituted again.

Apply it to `title`, `body`, every `sections[].title` / `sections[].body`,
every `links[].label` / `links[].url`, and `footer`. **Not** to `icon`,
`accent_color` or `template_id`.

**Reference implementation (Python — this is the bot's):**

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

**Reference implementation (TypeScript):**

```ts
const PLACEHOLDER = /\{([a-zA-Z0-9_]+)\}/g;

export function substitute(text: string | null | undefined,
                           variables: Record<string, unknown>): string {
  if (!text) return "";
  if (!variables || Object.keys(variables).length === 0) return text;
  return text.replace(PLACEHOLDER, (whole, key: string) => {
    if (!Object.prototype.hasOwnProperty.call(variables, key)) return whole;
    return pythonStr(variables[key]);
  });
}

// str() semantics — the bot renders with Python, so match it.
function pythonStr(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "boolean") return value ? "True" : "False";   // NOT "true"
  return String(value);
}
```

> **The `variables` type footgun.** `variables` is JSONB, so a value can be a
> string, a number, a boolean or `null`. The bot renders it with Python's
> `str()`: `True` → `"True"`, `None` → `""`, `1.0` → `"1.0"`. A naive
> JavaScript `String(v)` gives `"true"` and `"1"`. In practice every current
> caller passes strings, but implement `pythonStr` anyway — it costs six lines
> and prevents a silent divergence the day someone passes a boolean.

---

## 6. Rendering to mail and to the dashboard

### 6.1 Rules that are not negotiable

- **Strip Discord custom emojis outside Discord.** `<:done:123>` and
  `<a:spin:456>` are literal noise in an inbox. Regex:
  `/<a?:[a-zA-Z0-9_]+:\d+>/g`, then collapse runs of 2+ spaces/tabs into one
  and trim. (Country-flag Unicode emojis stay — they are not custom emojis.)
- **`body` and `sections[].body` are markdown**, and they can carry
  Discord-specific syntax the bot's own callers wrote: `<@123>` (user mention),
  `<@&123>` (role), `<#123>` (channel), `<t:1700000000:R>` (relative
  timestamp), `-#` (small text), `||spoiler||`. Decide per surface: the
  dashboard can resolve mentions and timestamps against its own caches; a mail
  should degrade them to plain text (`@user`, a formatted date) rather than
  print raw syntax.
- **`accent_color` is an int**, not a CSS string: `5793266` → `#5865F2`
  (`"#%06X" % value`). `null` → your surface's default accent.
- **`links[].url` is already substituted** — it may contain placeholders before
  rendering, never after. Reject/skip a link whose URL, after substitution, is
  not `https://` (a broken template can leave `https://…/{guild_id}`).
- **Never trust the text.** `body` frequently contains words a *server admin*
  typed (a welcome DM, a sanction reason). Escape it for your surface; a mail
  template that interpolates it into HTML unescaped is an injection (§15).

### 6.2 The mail shape — exact assembly

`NotificationContent.to_email()` builds the text body like this, and your mail
must match if the two are ever compared:

1. `blocks = [body]`
2. for each section: `f"{title}\n{body}".strip()`
3. if `footer`: append it
4. `text = "\n\n".join(b for b in blocks if b)`
5. `subject = strip_custom_emojis(title)`, `text = strip_custom_emojis(text)`
6. `links` are handed over resolved, as a list — render them as a button block
   or a plain list, your call.

```python
def to_email(payload: dict, variables: dict) -> dict:
    r = render(payload, variables)                    # §5 applied to every field
    blocks = [r["body"]]
    for s in r["sections"]:
        blocks.append(f"{s.get('title') or ''}\n{s.get('body') or ''}".strip())
    if r["footer"]:
        blocks.append(r["footer"])
    text = "\n\n".join(b for b in blocks if b)
    return {
        "subject": strip_custom_emojis(r["title"]),
        "text": strip_custom_emojis(text),
        "links": r["links"],
    }
```

```python
_CUSTOM_EMOJI = re.compile(r"<a?:[a-zA-Z0-9_]+:\d+>")

def strip_custom_emojis(text: str) -> str:
    return re.sub(r"[ \t]{2,}", " ", _CUSTOM_EMOJI.sub("", text or "")).strip()
```

### 6.3 The dashboard shape

`to_dashboard()` is simply the resolved payload with `icon` kept as the raw
custom-emoji string. Two options, both acceptable:

- map `<:name:id>` to `https://cdn.discordapp.com/emojis/<id>.webp` (append
  `?animated=true` for `<a:…>`), or
- map the emoji **name** to your own icon set and drop the id.

Do not print the raw `<:name:id>` string.

### 6.4 Reproducing the attribution line

In Discord the origin is one greyed line inside the card:

```
-# Sent by [**Server name**](https://discord.com/channels/1421493239579676682) (`1421493239579676682`)
```

Reproduce the same information so the two surfaces agree:

| Row state | What to show |
|---|---|
| `source_guild_id` present | the server (name + icon from your own guild cache), its id, and the verification check when the guild carries `VERIFIED`, `VERIFIED_ORG`, `PARTNER` or `OFFICIAL` in `guilds.attributes` |
| no guild, `source_service` present | the service label from `notifications.services.<id>` |
| `kind = 'official'` | **nothing** — Moddy speaking as an institution has no third party to name |

The guild link is `https://discord.com/channels/{guild_id}`.

---

## 7. The content hash

Only needed if the backend ever **writes** a notification itself (it should
not — see §13) or wants to de-duplicate identical wordings in its own analytics.
Reading never requires recomputing it.

`hash = sha256(canonical_json(payload))` where `canonical_json` is:

- JSON of the eight payload keys,
- `sort_keys=True` — **recursively**, so `{"title","body"}` inside `sections[]`
  is sorted too,
- `ensure_ascii=False` (real UTF-8, not `\uXXXX`),
- separators `,` and `:` — **no spaces**,
- encoded UTF-8.

```
{"accent_color":5793266,"body":"Welcome {user} on **{server}**!","footer":"Sent by {server}","icon":"<:waving_hand:1519789691711393982>","links":[{"label":"Open the server","url":"https://discord.com/channels/{guild_id}"}],"sections":[{"body":"Read {channel}","title":"Rules"}],"template_id":"welcome_dm.wdm_a1b2","title":"{server}"}
```

→ `398513a4c1c0e3ec2bf56a22fad587f30dccbb544a80d74fc510b5a043f1352b`

The hash is over the **template**, never over the rendered text. That is the
whole point: it is what makes ten thousand identical DMs one row.

> Note the difference with [TASK_SIGNATURE.md](TASK_SIGNATURE.md), which uses
> `ensure_ascii=True`. These are two different canonicalizations for two
> different purposes — do not share one helper between them.

---

## 8. Read models — the queries you will actually run

Every query below joins `notification_contents` for the template and, where
useful, `notification_deliveries` for the outcome. **Never** select
`notifications` alone and try to display it: without the payload you have no
message, and without `variables` you have a message full of `{braces}`.

### 8.1 One notification, fully hydrated

```sql
SELECT n.*, c.payload, c.uses AS content_uses,
       COALESCE(
         json_agg(json_build_object(
           'platform', d.platform, 'status', d.status,
           'channel_id', d.channel_id, 'message_id', d.message_id,
           'error', d.error, 'updated_at', d.updated_at
         ) ORDER BY d.platform) FILTER (WHERE d.platform IS NOT NULL),
         '[]'::json) AS deliveries
FROM notifications n
JOIN notification_contents c ON c.hash = n.content_hash
LEFT JOIN notification_deliveries d ON d.notification_id = n.id
WHERE n.id = $1
GROUP BY n.id, c.payload, c.uses;
```

### 8.2 A user's inbox

```sql
SELECT n.id, n.kind, n.author, n.source_service, n.source_guild_id,
       n.locale, n.variables, n.reportable, n.created_at, c.payload,
       d.status AS discord_status, d.message_id
FROM notifications n
JOIN notification_contents c ON c.hash = n.content_hash
LEFT JOIN notification_deliveries d
       ON d.notification_id = n.id AND d.platform = 'discord'
WHERE n.recipient_type = 'discord_user'
  AND n.recipient_id = $1
ORDER BY n.created_at DESC, n.id DESC
LIMIT $2;
```

`idx_notifications_recipient` on `(recipient_id, created_at DESC)` covers it.
For pagination use a **keyset**, not `OFFSET` (§14):

```sql
  AND (n.created_at, n.id) < ($3::timestamptz, $4::uuid)
```

### 8.3 A server's outbox (what this server sent through Moddy)

```sql
SELECT n.id, n.source_service, n.recipient_id, n.locale, n.variables,
       n.reportable, n.created_at, c.payload, d.status
FROM notifications n
JOIN notification_contents c ON c.hash = n.content_hash
LEFT JOIN notification_deliveries d
       ON d.notification_id = n.id AND d.platform = 'discord'
WHERE n.source_guild_id = $1
ORDER BY n.created_at DESC
LIMIT $2;
```

`idx_notifications_guild` covers it. This is the query behind a server-admin
view of "what Moddy sent on our behalf" — useful and safe to expose to guild
admins, since these are their own words.

### 8.4 Campaign status

A broadcast is one `batch_id` across many rows:

```sql
SELECT d.platform, d.status, COUNT(*) AS total
FROM notifications n
JOIN notification_deliveries d ON d.notification_id = n.id
WHERE n.batch_id = $1
GROUP BY d.platform, d.status;
```

`idx_notifications_batch` covers the lookup. `recipient_ref` on those rows
carries the segment the campaign targeted (`all`, `PREMIUM`, …), and
`actor_id` the staff member who launched it.

Recent campaigns:

```sql
SELECT n.batch_id, MIN(n.created_at) AS started_at, COUNT(*) AS recipients,
       MIN(n.recipient_ref) AS segment, MIN(n.actor_id) AS actor_id,
       MIN(n.content_hash) AS content_hash
FROM notifications n
WHERE n.batch_id IS NOT NULL
GROUP BY n.batch_id
ORDER BY started_at DESC
LIMIT 50;
```

### 8.5 Wording analytics

```sql
-- Most-sent wordings, with the template they came from.
SELECT c.hash, c.uses, c.payload->>'template_id' AS template_id,
       c.first_seen_at, c.last_seen_at
FROM notification_contents c
ORDER BY c.uses DESC
LIMIT 50;

-- Delivery success per service over the last 7 days.
SELECT n.source_service, d.status, COUNT(*)
FROM notifications n
JOIN notification_deliveries d
  ON d.notification_id = n.id AND d.platform = 'discord'
WHERE n.created_at > now() - interval '7 days'
GROUP BY n.source_service, d.status
ORDER BY n.source_service;
```

---

## 9. The delivery worker

### 9.1 What it is for

A notification targeting your platform is a `notification_deliveries` row with
`status = 'pending'` and `platform IN ('email','dashboard')`. Claim it, render
it (§5–6), deliver it, mark it. The row is the queue — there is no Redis stream
for this, deliberately: the queue and the record are the same object, so a
delivered mail can never disappear from the history.

> **Today, in practice.** Every bot caller currently sends with the default
> `platforms = {discord}`, so there are **no** pending `email` / `dashboard`
> rows yet. Build the loop anyway: the moment a caller opts a notification into
> another platform, its row appears with no further code change on the bot side.
> The dashboard *inbox* (§10) does not wait for this — it reads the history
> directly and is independent of `platforms`.

### 9.2 Claiming

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

`FOR UPDATE SKIP LOCKED` is what makes multiple workers safe. The lock lives
only as long as the transaction, so either:

- do the whole thing in one transaction (claim → deliver → mark), which holds a
  row lock across an SMTP call — acceptable at low volume, or
- **preferred**: claim *and immediately mark* the rows into an in-flight state
  in the same transaction, commit, then deliver outside it. Since the schema has
  no `in_flight` status, model it your own way: a backend-side
  `notification_delivery_attempts` table keyed on `(notification_id, platform)`
  holding `claimed_at`, `attempts`, `next_attempt_at`. **Do not add columns to
  the shared tables** — the bot owns their shape.

### 9.3 Marking

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

Leave `channel_id` / `message_id` `NULL` — they are Discord's. Truncate `error`
yourself: the column is unbounded `TEXT`, but the bot truncates to 500 and staff
panels assume that length.

**Never leave a row `pending` after handling it.** A row you looked at and
decided not to deliver is `skipped`, with the reason in `error`.

### 9.4 Retries

The schema does not count attempts — that is deliberate, so the bot can rewrite
the Discord row on its own schedule without fighting you. Keep retry state in
your own table:

```
notification_delivery_attempts(
    notification_id UUID, platform TEXT, attempts INT,
    claimed_at TIMESTAMPTZ, next_attempt_at TIMESTAMPTZ, last_error TEXT,
    PRIMARY KEY (notification_id, platform))
```

Suggested policy: exponential backoff `1m, 5m, 30m, 2h, 12h`, 5 attempts, then
`failed` with the last error. Distinguish:

| Failure | Action |
|---|---|
| hard bounce, unknown address, unsubscribed | `skipped` or `failed` immediately, no retry |
| 4xx from your provider (bad payload) | `failed` immediately — retrying will not help; alert |
| 5xx / timeout / rate limit | retry with backoff |

### 9.5 Idempotency

`(notification_id, platform)` is the natural idempotency key — pass it to your
mail provider as the message id / dedup key. A worker that crashes after sending
but before marking will otherwise re-send on the next claim.

### 9.6 Ordering

Order within one recipient is `created_at` ascending. Nothing guarantees a
worker processes them in that order, so if ordering matters for your surface
(a thread of messages), sort at read time, not at delivery time.

---

## 10. The dashboard inbox

Independent of `platforms`: a user's notification history is simply their rows
(§8.2). It requires **no** delivery loop and can ship first.

What the card must show, to agree with Discord:

- the rendered `title` / `body` / `sections` / `links` / `footer` (§5–6),
- the origin exactly as §6.4 describes,
- the date (`created_at`, in the reader's timezone),
- optionally the Discord delivery state (`discord_status`): a member whose DMs
  were closed sees "we could not DM you this" — which is precisely why a
  dashboard inbox is worth building.

What it must **not** show:

- a report affordance when `kind = 'official'` or `reportable = false`. If you
  explain why, match the bot's two reasons: the wording is Moddy's
  (`report_block = "moddy_authored"`), or the server is an official Moddy server
  (`report_block = "official_guild"`).
- the raw `payload` — it is a template, full of braces.

### Authorisation

A user may read a notification only when they are its recipient:

- `recipient_type = 'discord_user'` and `recipient_id` = the session's Discord id, or
- `recipient_type = 'discord_guild'` and the session has **Manage Server** on
  `recipient_id` (that is the bot's own `may_report()` rule).

A guild admin may read their server's **outbox** (§8.3) — those are their own
words — but never another server's, and never another user's inbox.

> **The dashboard is the only realistic place a report can be filed.** The flag
> button that used to sit under a DM was removed; the pipeline behind it is
> intact (`notification_reports`, the staff review panel in Discord, the
> outcome DM) and only lacks a trigger. That trigger **cannot** be an `INSERT`
> from the backend — filing a report also posts the review panel and logs it,
> both bot-side. It needs the `notification_send`-style task of §13, extended
> with a `notification_report` type. Until that exists, `reportable` is
> information you display, not an action you can offer.

---

## 11. Abuse reports (read-only)

`notification_reports` is **read-only for the backend**. A decision is not a
column update: accepting or declining a report also edits the review panel in
Discord, writes the report log channel, and DMs the reporter — all bot-side.
Writing `status` directly desynchronises the three.

Read it freely (a staff dashboard listing open reports is a good idea):

```sql
SELECT r.id, r.notification_id, r.reporter_id, r.reason, r.status,
       r.claimed_by, r.claimed_at, r.decided_by, r.decided_at, r.decision_note,
       r.created_at,
       n.source_guild_id, n.source_service, n.recipient_id, n.locale,
       c.payload, n.variables
FROM notification_reports r
JOIN notifications n ON n.id = r.notification_id
JOIN notification_contents c ON c.hash = n.content_hash
WHERE r.status IN ('pending', 'claimed')
ORDER BY r.created_at;
```

Lifecycle: `pending` → `claimed` → `accepted` | `refused`. A report can also go
straight from `pending` to a decision (the reviewer decided without claiming).

`UNIQUE (notification_id, reporter_id)`: one report per person per notification,
so a count of rows is a count of distinct reporters. "Which servers get reported
most" is therefore an honest metric:

```sql
SELECT n.source_guild_id, COUNT(*) AS reports,
       COUNT(*) FILTER (WHERE r.status = 'accepted') AS upheld
FROM notification_reports r
JOIN notifications n ON n.id = r.notification_id
WHERE r.created_at > now() - interval '30 days'
GROUP BY n.source_guild_id
ORDER BY reports DESC;
```

`review_channel_id` / `review_message_id` point at the Discord review panel —
useful to deep-link a staff dashboard to the thread where the decision happens
(`https://discord.com/channels/<guild>/<channel>/<message>`).

---

## 12. Suggested HTTP API shapes

Not prescriptive — but if the dashboard front-end and the backend agree on these
now, both sides stop guessing. **Every snowflake is a string** (§17.6).

```jsonc
// GET /api/notifications?limit=25&before=<created_at>,<id>
{
  "items": [
    {
      "id": "0f2a…-uuid",
      "created_at": "2026-08-26T10:14:03Z",
      "locale": "fr",
      "kind": "guild",
      "author": "guild",
      "reportable": true,
      "source": {
        "service_id": "welcome_dm",
        "service_label": "Message de bienvenue",
        "guild_id": "1421493239579676682",
        "guild_name": "Moddy",
        "verified": true,
        "official": false
      },
      "content": {                      // RESOLVED — never the raw template
        "title": "Moddy",
        "body": "Bienvenue <@7> sur **Moddy** !",
        "icon_url": "https://cdn.discordapp.com/emojis/1519789691711393982.webp",
        "accent_color": "#5865F2",
        "sections": [{"title": "Règles", "body": "Lis #rules"}],
        "links": [{"label": "Ouvrir le serveur", "url": "https://discord.com/channels/1421493239579676682"}],
        "footer": "Envoyé par Moddy",
        "template_id": "welcome_dm.wdm_a1b2"
      },
      "delivery": {"discord": {"status": "sent", "message_id": "154…"}}
    }
  ],
  "next": {"before": "2026-08-26T10:14:03Z,0f2a…-uuid"}
}
```

```jsonc
// GET /api/guilds/:guild_id/notifications   — a server's outbox (Manage Server)
// GET /api/staff/notifications/:id          — full hydration, staff only (§8.1)
// GET /api/staff/notification-reports?status=pending
// GET /api/staff/campaigns/:batch_id        — §8.4 counts
```

Rules for all of them:

- return **resolved** content, never the template;
- return `accent_color` as a CSS hex string, computed from the int;
- return timestamps as ISO-8601 UTC with `Z`;
- 404 rather than 403 for a notification the session may not read, so the
  endpoint cannot be used to probe whether a uuid exists.

---

## 13. Backend → bot: asking for a Discord send

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

```jsonc
// type: "notification_report"  — PROPOSED, NOT IMPLEMENTED
// The dashboard's report button. The bot files the report, posts the staff
// review panel and logs it; the backend gets the report uuid back by polling
// notification_reports (it is read-only there).
{
  "notification_id": "0f2a…-uuid",
  "reporter_id": "123456789012345678",
  "reason": "free text typed by the reporter, ≤ 1000 chars"
}
```

Do not implement against those shapes until they land in the bot — they are
written here so the two sides design them once, not so they can be shipped
early.

---

## 14. Performance

- **Never `OFFSET`** an inbox. Keyset-paginate on `(created_at, id)`; the
  `idx_notifications_recipient` index makes it O(log n) at any depth.
- **Cache `notification_contents` aggressively.** The hash *is* the cache key
  and the row is immutable — a `Map<hash, payload>` with no invalidation is
  correct forever. On an inbox page of 25 rows from one server you will
  typically fetch 2–3 distinct payloads.
- **Batch the joins.** Fetch the page of `notifications`, collect the distinct
  `content_hash` values, then one `WHERE hash = ANY($1)`. That is faster than
  the join when your cache already holds most of them, and it avoids shipping
  the same 4 KB payload 25 times.
- **The delivery worker should claim in batches** of 50–200, not one row at a
  time; `idx_notification_deliveries_status` on `(platform, status)` makes the
  claim cheap even with millions of `sent` rows behind it.
- **Broadcast bursts.** `broadcast_users` paces at `BROADCAST_DELAY = 0.35s` per
  recipient, so a campaign inserts roughly 3 rows/second — your worker will
  never see a thundering herd from the bot, but a 10 000-recipient campaign does
  add 10 000 rows over ~1 hour. Size retention accordingly (§15).
- Expect `notifications` to be the largest table of the four by orders of
  magnitude, `notification_contents` to stay in the thousands.

---

## 15. Security and privacy

1. **Body text is user-generated content.** A welcome DM or a sanction reason
   was typed by a server admin. Escape it for HTML, strip/allow-list the
   markdown you render, and never `dangerouslySetInnerHTML` the raw string. In a
   mail, prefer the plain-text part; if you build an HTML part, escape first and
   convert markdown from the escaped text.
2. **Links are attacker-controllable.** `links[].url` comes from the same place.
   Allow only `https://`, render with `rel="noopener noreferrer"`, and consider
   an interstitial for hosts outside Discord/Moddy.
3. **The uuid is not a secret, but it is not an authorisation token either.**
   Authorise every read by recipient identity (§10), not by knowledge of the id.
4. **Never expose `actor_id` outside staff surfaces.** It names the Moddy staff
   member who triggered a send.
5. **Never expose one user's `recipient_id` to another.** A server outbox view
   for guild admins is legitimate; it still shows *their members*, so treat it
   as personal data.
6. **Report reasons are personal data** and often accusatory. Staff surfaces
   only.
7. **Retention.** There is no retention job today, and adding one is a policy
   decision, not a cleanup task. If you add one: never delete a notification
   that has a report (the cascade would erase the evidence), keep the
   `notification_contents` row while anything references it, and prefer
   anonymising `recipient_id` over deleting rows.
8. **The database is shared.** Use a role that cannot write the tables the bot
   owns (§1) — the cheapest possible guarantee that a backend bug cannot
   rewrite Moddy's message history.

---

## 16. Observability

Metrics worth having from day one:

| Metric | Query / source |
|---|---|
| `notifications_pending{platform}` | count of `notification_deliveries` where `status='pending'` and `platform` is yours — **alert if it grows monotonically for 15 min** |
| `notifications_delivery_age_seconds{platform}` | `now() - min(updated_at)` over the same set |
| `notifications_delivered_total{platform,status}` | counter from your worker |
| `notifications_render_errors_total` | any exception in §5–6 — should be flat at zero |
| `notification_reports_open` | `notification_reports` where `status IN ('pending','claimed')` |
| `notifications_unknown_service_total{service_id}` | a `source_service` your label map does not know — tells you a new sender shipped |

A `pending` row older than an hour on your platform is a bug in your worker, not
a slow provider: mark or skip, never leave.

---

## 17. Invariants

Breaking one of these does not raise; it produces a wrong message or a dead
button in someone's DMs.

1. **Never make a notification more reportable than it was recorded.**
   `reportable` is frozen at send time. The bot only ever tightens it at click
   time (a server marked `OFFICIAL` later). Anything you display must follow
   the same direction.
2. **Never mutate `notification_contents.payload`.** Its hash is its primary
   key and thousands of rows point at it; editing it rewrites history for every
   one of them. A new wording is a new hash — that is automatic on the bot side.
   (`uses`, `last_seen_at` are the bot's counters; do not touch them either.)
3. **Never mutate `notifications.variables`** after the fact: it is the record
   of what a specific person was actually shown.
4. **Never delete rows.** `notification_deliveries` and `notification_reports`
   cascade from `notifications`; a delete erases the evidence behind an open
   abuse report. See §15.7 before writing any retention job.
5. **The uuid is safe to display.** It is the reference staff look a
   notification up by (`/mod notif`), and the natural id for a dashboard
   report control. It is not a secret and must not be treated as one — but the
   Discord DM does not show it, so a user who has only seen the DM cannot
   quote it: identify their notification by recipient and date.
6. **`recipient_id`, `source_guild_id`, `actor_id`, `channel_id`, `message_id`
   are Discord snowflakes in a `BIGINT`.** JavaScript loses precision above
   2^53 — serialise them as strings in every API response, like everywhere else
   in Moddy.
7. **Localise from `locale`, not from the reader.** The row carries the locale
   the message was rendered in; re-rendering a French DM's chrome in English
   next to French body text is the mistake this column exists to prevent.
8. **Never add a column to these four tables.** The bot owns their shape and
   recreates them at boot; backend-side state (retry counters, read receipts,
   mail provider ids) belongs in backend-owned tables keyed on
   `(notification_id, platform)`.
9. **An unknown enum value is not an error.** A new `source_service`, or a
   `kind` you have not seen, must degrade to a generic render — the bot ships
   new senders without coordinating a backend deploy.

---

## 18. Test vectors

Assert these in your own suite; they are the exact values the bot produces.

**Substitution**

| `text` | `variables` | Result |
|---|---|---|
| `Hi {user}` | `{"user":"Bob"}` | `Hi Bob` |
| `Hi {user}` | `{}` | `Hi {user}` (left visible) |
| `Hi {user}` | `{"user":null}` | `Hi ` |
| `Hi {user}` | `{"user":true}` | `Hi True` (Python `str`) |
| `100% {of} it` | `{"of":"of"}` | `100% of it` |
| `a { b } c` | `{"b":"x"}` | `a { b } c` (spaces are not part of the pattern) |
| `{a-b}` | `{"a-b":"x"}` | `{a-b}` (hyphen is not in `[a-zA-Z0-9_]`) |
| `{{x}}` | `{"x":"1"}` | `{1}` (the inner `{x}` matches) |
| `` (empty) | anything | `` |

**Emoji stripping**

| Input | Output |
|---|---|
| `<:done:123> Saved` | `Saved` |
| `<a:spin:456>Loading` | `Loading` |
| `A <:x:1> <:y:2> B` | `A B` |
| `🇫🇷 France` | `🇫🇷 France` (unchanged) |

**Colour**: `5793266` → `#5865F2`; `null` → your default.

**Hash**: the payload of §7 → `398513a4c1c0e3ec2bf56a22fad587f30dccbb544a80d74fc510b5a043f1352b`.

**Mail assembly**: the §4 example → subject `Moddy`, text
`Welcome <@7> on **Moddy**!\n\nRules\nRead #rules\n\nSent by Moddy`.

The bot's own suite for the same behaviour is
[`tests/test_notifications.py`](../tests/test_notifications.py) — read it when
in doubt about an edge case; it is the executable version of this document.

---

## 19. Implementation checklist

**Read path**

- [ ] `notifications` ⋈ `notification_contents` ⋈ `notification_deliveries`, never `notifications` alone
- [ ] Substitution implemented exactly as §5 (unknown key left visible, `null` → empty, Python `str` semantics)
- [ ] Custom emojis stripped for mail; markdown escaped per surface
- [ ] `accent_color` int → hex; `icon` → CDN url or your own icon set
- [ ] Snowflakes serialised as strings; timestamps ISO-8601 UTC
- [ ] Unknown `source_service` / `kind` degrade instead of throwing
- [ ] Keyset pagination; `notification_contents` cached by hash

**Delivery worker (email / dashboard)**

- [ ] Claims with `FOR UPDATE SKIP LOCKED`, in batches
- [ ] Marks `sent` / `failed` / `skipped` — never leaves a row `pending` after handling it
- [ ] `error` truncated to 500 chars; `channel_id` / `message_id` left `NULL`
- [ ] Idempotency key `(notification_id, platform)` passed to the provider
- [ ] Retry state in a **backend-owned** table, with backoff and a hard cap

**Dashboard**

- [ ] Inbox authorised by recipient identity (user, or Manage Server for a guild row)
- [ ] Origin reproduced as §6.4, verification badge included
- [ ] No report affordance when `kind = 'official'` or `reportable = false`
- [ ] Server outbox scoped to `source_guild_id`, Manage Server only
- [ ] `actor_id` and report reasons never leave staff surfaces

**Safety**

- [ ] `notification_reports` treated as read-only
- [ ] No writes to `notifications`, `notification_contents`, or `discord` delivery rows
- [ ] No `ALTER TABLE` on any of the four tables
- [ ] DB role without write access to the bot-owned tables

---

## 20. FAQ

**Why is the stored body full of `{braces}`?**
Because it is a template shared by every recipient of the same wording. See §0
and §7. Render it with §5 before showing it to anyone.

**A notification has no `notification_deliveries` row for my platform. Why?**
Because it was not targeted at your platform — `platforms` did not contain it.
Only the bot decides that, at send time.

**Can I re-send a notification?**
No. Flip your delivery row back to `pending` and your own worker will retry the
*same* notification — that is a retry, not a re-send. A genuinely new message is
a new notification, and only the bot can create one (§13).

**Can I fix a typo in a message that was already sent?**
No. `payload` is immutable and shared (§17.2). The message that was delivered is
the record of what was delivered.

**Two notifications have the same `content_hash`. Is that a bug?**
No — that is the design. Compare `variables` to see what each recipient actually
read, and `c.uses` to see how many share it.

**The same user has two rows with the same `batch_id`. Is that a bug?**
It would be — a broadcast writes one row per recipient. If you see it, report it
with both uuids; it means a campaign was run twice against overlapping segments.

**Who do I ask when a row does not make sense?**
`/mod notif <uuid>` in Discord shows the bot's own view of the exact same row:
origin, reportability and why, per-platform delivery with message ids, the
resolved content, the template hash and its use count, plus every report filed.
Comparing that panel with your render is the fastest way to find a divergence.

---

## 21. Related

- [NOTIFICATIONS.md](NOTIFICATIONS.md) — the system itself, and the Discord side
- [DATABASE.md](DATABASE.md) §13–16 — full column reference
- [BACKEND-INTEGRATION.md](BACKEND-INTEGRATION.md) — Redis, streams, shared DB rules
- [REDIS_COMMUNICATION.md](REDIS_COMMUNICATION.md) — when a feature needs a channel or a stream
- [TASK_SIGNATURE.md](TASK_SIGNATURE.md) — signing `moddy:tasks` entries, for §13
