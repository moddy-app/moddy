# Tickets ↔ backend — transcripts, ratings and settings

> Implementation reference for the backend and the dashboard. Everything below
> describes **what `moddy-bot` actually writes**, column by column, so the other
> side can be built against it rather than guessed at. The functional overview
> lives in [TICKETS.md](TICKETS.md); this file is the data contract.
>
> Bot implementation: [`services/ticket_transcript_service.py`](../services/ticket_transcript_service.py),
> [`db/repositories/ticket_transcripts.py`](../db/repositories/ticket_transcripts.py),
> [`db/repositories/ticket_ratings.py`](../db/repositories/ticket_ratings.py).

---

## 0. What is new, in one paragraph

Closing a ticket now produces an **archive** of its conversation
(`ticket_transcripts`, one row per closure, body compressed), optionally a
**rating** left by the member who opened it (`ticket_ratings`), and a public
link the member is given in their DM. The backend owns exactly one thing here:
serving `GET /transcripts/<key>` and rendering the archive in the dashboard.
Everything else it only reads.

### Ownership

| Table | Written by | Read by |
|---|---|---|
| `ticket_transcripts` | **bot only** | backend, dashboard |
| `ticket_transcript_authors` | **bot only** | backend, dashboard |
| `ticket_ratings` | **bot only** | backend, dashboard |
| `guilds.data.modules.tickets.settings` | bot **and** dashboard | both |

The backend must never insert into or update the first three. The bot is the
only writer; a dashboard that needs a transcript deleted should delete the row
(and let the `ON DELETE CASCADE` clear its authors), never rewrite one.

---

## 1. `ticket_transcripts`

One row per **closure**, not per channel: a ticket reopened and closed again
produces a second row, and both stay readable. `channel_id` is therefore not
unique.

```sql
CREATE TABLE ticket_transcripts (
    id             BIGSERIAL PRIMARY KEY,
    key            UUID NOT NULL UNIQUE,
    guild_id       BIGINT NOT NULL,
    channel_id     BIGINT NOT NULL,
    ticket_number  INTEGER NOT NULL,
    panel_id       TEXT NOT NULL,
    category_id    TEXT NOT NULL,
    category_name  TEXT NOT NULL,
    owner_id       BIGINT NOT NULL,
    participants   BIGINT[] NOT NULL DEFAULT '{}',
    claimed_by     BIGINT,
    closed_by      BIGINT NOT NULL,
    close_reason   TEXT,
    opened_at      TIMESTAMPTZ NOT NULL,
    closed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    message_count  INTEGER NOT NULL DEFAULT 0,
    truncated      BOOLEAN NOT NULL DEFAULT FALSE,
    codec          TEXT NOT NULL CHECK (codec IN ('zstd','zlib')),
    payload        BYTEA NOT NULL,
    payload_size   INTEGER NOT NULL,
    log_channel_id BIGINT,
    log_message_id BIGINT
);
```

| Column | Meaning |
|---|---|
| `key` | **the public handle.** What appears in the URL and in the DM button's custom_id. A UUID rather than `id` so the archive space cannot be walked by incrementing a number. |
| `ticket_number` | the human reference — "ticket #42" — unique per guild. This is the id shown when the ticket opens and in the ticket log. |
| `category_name` | a **snapshot**. The category can be renamed or deleted from the config afterwards; the archive keeps the name it was closed under. |
| `owner_id` | who opened the ticket. Also the authorisation subject — see §4. |
| `participants` | everyone who had access, the opener included. |
| `claimed_by` / `closed_by` | who took the ticket in charge, and who closed it. `closed_by` is never NULL. |
| `truncated` | the conversation was longer than 20 000 messages and only the **most recent** ones were kept. Show this to the reader; the beginning is genuinely gone. |
| `payload_size` | the **uncompressed** size in bytes. `octet_length(payload)` gives the stored size; the ratio is normally 10–20×. |
| `log_channel_id` / `log_message_id` | where the bot posted its closing card, so it can edit it when a rating lands. Of no use to the backend. |

### Why the duplication with `tickets`

`guild_id`, `panel_id`, `category_id`, `owner_id`, `claimed_by`, `opened_at`
and `participants` also exist in `tickets`. That is **deliberate and load
bearing**: the `tickets` row is `DELETE`d the moment its Discord channel
disappears (`cogs/tickets.py::on_guild_channel_delete`). An archive joined to
`tickets` would die with the channel it archives. **Never add that foreign
key, and never resolve a transcript through `tickets`.**

### Reading the body

```python
payload = row["payload"]          # BYTEA
if row["codec"] == "zstd":
    import zstandard
    raw = zstandard.ZstdDecompressor().decompress(payload)
else:                              # 'zlib'
    import zlib
    raw = zlib.decompress(payload)
body = json.loads(raw.decode("utf-8"))
```

Both codecs occur in the same table. `zstd` is the default; `zlib` appears for
rows written by a deployment where the `zstandard` wheel was unavailable. Switch
on `codec`, never on which library you happen to have — and support both.

The reference implementation is [`utils/compression.py`](../utils/compression.py).

---

## 2. The body schema (`v: 1`)

Keys are short on purpose and **empty fields are omitted entirely**: a plain
text message costs about forty bytes before compression. Treat any missing key
as "not present", never as an error.

```jsonc
{
  "v": 1,
  "messages": [
    {
      "i":  "1234567890123456789",  // message id, as a string
      "a":  789,                     // author id, an int — see §3
      "t":  1757000000,              // created_at, unix seconds
      "c":  "the text",              // optional, capped at 4000 chars
      "ed": 1757000060,              // optional, edited_at, unix seconds
      "f":  [                        // optional, attachments
        {"n": "a.png", "u": "https://cdn…", "s": 1234, "ct": "image/png"}
      ],
      "e":  [                        // optional, embeds
        {"t": "title", "d": "description", "u": "url",
         "fl": [{"n": "field name", "v": "field value"}]}
      ],
      "r":  [{"e": "👍", "c": 3}],    // optional, reactions
      "p":  "1234567890123456780",   // optional, the message this replies to
      "s":  "pin_add"                // optional, discord.MessageType name
    }
  ],
  "staff_thread": { "messages": [ … ] }   // optional — see the warning below
}
```

- `messages` is ordered **oldest first**.
- `"s"` is present only for non-default message types (`pin_add`,
  `thread_created`, …). Those usually carry no `"c"`.
- **Attachments are references, never files.** `"u"` is Discord's CDN url; it
  expires and is not re-signed by the bot. A dashboard that wants durable
  attachments has to mirror them itself, at its own cost.
- `"v"` is the schema version. Reject a body whose `v` you do not know rather
  than parsing it optimistically; the bot bumps it when a key changes meaning.

### ⚠️ `staff_thread` is staff-only content

The private staff thread of a ticket is archived under its own key precisely so
it can be withheld. **The dashboard must not show `staff_thread` to the ticket's
own author, nor to anyone who is not staff of that guild.** Merging it into
`messages` would hand a member the thread the staff discussed them in. If your
renderer cannot make that distinction, drop the key.

---

## 3. `ticket_transcript_authors`

```sql
CREATE TABLE ticket_transcript_authors (
    transcript_id BIGINT NOT NULL REFERENCES ticket_transcripts(id) ON DELETE CASCADE,
    author_id     BIGINT NOT NULL,
    username      TEXT   NOT NULL,
    display_name  TEXT   NOT NULL,
    avatar_url    TEXT,
    is_bot        BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (transcript_id, author_id)
);
```

Authors are lifted out of the payload for two reasons: a name stored once
instead of once per message, and a transcript **listing** that can answer "who
spoke in this ticket" without decompressing a single byte.

`"a"` in the body joins to `author_id` here. Everything is a snapshot taken at
closing time — the person may since have changed their name, their avatar, or
left the server. Render what is stored; that is what the conversation looked
like.

```sql
SELECT t.key, t.ticket_number, t.category_name, t.closed_at, t.message_count,
       array_agg(a.display_name) AS speakers
FROM ticket_transcripts t
LEFT JOIN ticket_transcript_authors a ON a.transcript_id = t.id
WHERE t.guild_id = $1
GROUP BY t.id
ORDER BY t.closed_at DESC
LIMIT 25;
```

---

## 4. `GET /transcripts/<key>` — the backend's one job

The bot hands this url to two audiences:

- the **ticket log channel** card, seen by the guild's staff;
- the **closing DM**, seen by the member who opened the ticket.

So the route has to authorise both, and nobody else:

```
allow if  viewer.id == transcript.owner_id
      or  viewer is staff of transcript.guild_id
deny otherwise (404, not 403 — do not confirm that a key exists)
```

`key` is a UUID and the only credential in the link. Treat the url as
share-sensitive: anyone holding it plus a matching identity can read the
conversation. A 404 for an unknown key and a 404 for an unauthorised viewer must
be indistinguishable.

The bot builds the url as:

```python
f"{MODDY_DASHBOARD_URL}/transcripts/{key}"      # config.py:64
```

`MODDY_DASHBOARD_URL` defaults to `https://dashboard.moddy.app`. If the
dashboard serves transcripts from a different path, that environment variable is
the single place to change — do not ask for a code change.

---

## 5. `ticket_ratings`

```sql
CREATE TABLE ticket_ratings (
    id             BIGSERIAL PRIMARY KEY,
    guild_id       BIGINT NOT NULL,
    channel_id     BIGINT NOT NULL,
    transcript_id  BIGINT REFERENCES ticket_transcripts(id) ON DELETE SET NULL,
    ticket_number  INTEGER NOT NULL,
    category_id    TEXT   NOT NULL,
    rated_staff_id BIGINT,
    rated_by       BIGINT NOT NULL,
    score          SMALLINT NOT NULL CHECK (score BETWEEN 1 AND 5),
    comment        TEXT,
    trigger        TEXT NOT NULL CHECK (trigger IN ('close_request','self_close','dm_button')),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ticket_ratings_once UNIQUE (transcript_id)
);
```

| Column | Meaning |
|---|---|
| `score` | 1 to 5. **The member never saw a number** — the modal offers five adjectives (`Pas top`, `Moyen`, `Correct`, `Très bien`, `Excellent` and their translations). Render adjectives too; "3/5" is not what was asked. |
| `rated_staff_id` | defaults to `claimed_by`, else `closed_by`, and the member may change it in the modal. **NULL means "nobody in particular"** — a real answer, not a missing one. Exclude NULLs from per-staff aggregates; keep them in per-guild ones. |
| `rated_by` | always the ticket's opener. |
| `trigger` | where the rating was collected: `close_request` (the staff offered the closure and the member accepted), `self_close` (the member closed it themselves), `dm_button` (they came back to it from the closing DM, possibly days later). |
| `transcript_id` | `ON DELETE SET NULL`: a purged transcript does not erase the rating it carried. `ticket_number` and `category_id` are kept here for that reason. |

One rating per **closure**, enforced by `UNIQUE (transcript_id)`. A ticket
reopened and closed again is a new interaction and can be rated again.

### Aggregates the dashboard will want

```sql
-- One staffer, last 30 days
SELECT COUNT(*)::int AS ratings,
       AVG(score)::float AS average,
       COUNT(*) FILTER (WHERE score <= 2)::int AS negative
FROM ticket_ratings
WHERE guild_id = $1 AND rated_staff_id = $2
  AND created_at >= now() - interval '30 days';

-- The team, ordered by volume (never by average: one 5/5 must not outrank
-- fifty tickets). Flag anything under 3 reviews rather than ranking on it.
SELECT rated_staff_id, COUNT(*)::int AS ratings, AVG(score)::float AS average
FROM ticket_ratings
WHERE guild_id = $1 AND rated_staff_id IS NOT NULL
  AND created_at >= now() - interval '30 days'
GROUP BY rated_staff_id
ORDER BY ratings DESC;

-- Volume handled, read from transcripts and NOT from `tickets`: a closed
-- ticket whose channel was tidied away still counts towards its staff's work.
SELECT COALESCE(claimed_by, closed_by) AS staff_id, COUNT(*)::int AS handled
FROM ticket_transcripts
WHERE guild_id = $1 AND closed_at >= now() - interval '30 days'
GROUP BY 1;
```

`/ticket stats` in Discord runs exactly these
([`db/repositories/ticket_ratings.py`](../db/repositories/ticket_ratings.py)) —
matching them keeps the two surfaces from disagreeing.

---

## 6. Module settings

Stored in `guilds.data.modules.tickets.settings`, alongside `panels`. This is
the one object the dashboard **writes** as well as reads.

```jsonc
{
  "panels": [ … ],
  "settings": {
    "log_channel_id": null,                // BIGINT as a JSON number or string
    "transcripts_enabled": true,
    "transcript_retention_days": 0,        // 0 = keep forever, max 3650
    "closure_detection_enabled": false,
    "rating_enabled": true
  }
}
```

- **Every key is optional.** A config written before these existed loads with
  the defaults above (`modules/tickets.py::normalize_settings`), so no guild has
  to be migrated.
- For backward compatibility the bot also accepts these five keys **flat at the
  root** of the module config, not only under `settings`. New writes should use
  `settings`.
- `closure_detection_enabled` defaults to **false**: it spends AI quota, so it
  is opt-in like everything that goes through the gateway.
- `transcript_retention_days` is enforced by a daily task in the bot, not by the
  backend. Setting it to 30 deletes transcripts closed more than 30 days ago,
  on the next run, cascading to their authors.

### The new per-category permission

`TICKET_PERMISSIONS` gained a tenth entry, `stats`, granted per role per
category like the others. It gates reading the handling ratings of that
category. A dashboard permission editor has to list it; an existing role entry
without it is simply a role that cannot read ratings.

---

## 7. Storage expectations

Sizing, so nobody is surprised by the bill:

| | typical |
|---|---|
| A 30-message support ticket | ~1.5 kB uncompressed → **~300 bytes** stored |
| A 500-message escalated ticket | ~40 kB uncompressed → **~3 kB** stored |
| Hard cap | 20 000 messages, then `truncated = true` |

Three things do the work: short keys with empty fields omitted, authors stored
once in their own table, and zstd level 19 over the result. Attachments are
never copied — only their CDN url, their name and their size.

A server that wants its storage bounded sets `transcript_retention_days`.

---

## 8. Checklist for the backend

- [ ] Decompress on `codec`, supporting **both** `zstd` and `zlib`.
- [ ] Reject a body whose `"v"` you do not know.
- [ ] Treat every optional body key as absent, not as an error.
- [ ] **Withhold `staff_thread` from non-staff**, the ticket's author included.
- [ ] Serve `GET /transcripts/<key>`; authorise opener **or** guild staff; 404
      for everything else, indistinguishably.
- [ ] Join `"a"` to `ticket_transcript_authors`, and render the stored snapshot
      rather than a live profile lookup.
- [ ] Render scores as adjectives, not as `n/5`.
- [ ] Exclude `rated_staff_id IS NULL` from per-staff aggregates.
- [ ] Never insert into or update these three tables.
- [ ] Never join a transcript or a rating to `tickets`.
