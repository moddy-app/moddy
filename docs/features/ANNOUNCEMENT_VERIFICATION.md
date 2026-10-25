# Announcement Verification

> Support-server only. A Manager or Developer of the Moddy team authenticates an
> announcement; every copy Discord relays into a server that follows the
> channel then gets a confirmation card with a **Translate** button.

---

## Why

Servers follow Moddy's announcement channel (`utils/announcement_setup.py`
sets it up). Discord relays each published announcement as a webhook message
whose author name is whatever the follow webhook is called, so members cannot
tell a real Moddy announcement from a look-alike. The confirmation card answers
that, and the Translate button lets anyone read it in their own language.

---

## Flow

1. A message is posted in a watched channel of the support server
   (`ANNOUNCEMENT_VERIFICATION_CHANNEL_IDS`). Moddy replies with an
   **Authenticate** card (server language). Moddy's own messages are ignored.
2. A Manager or Developer presses **Authenticate** (`can_verify`: `StaffRole.MANAGER`
   or `StaffRole.DEV`, re-checked on every click; anyone else gets an ephemeral
   refusal). Moddy re-fetches the announcement, translates it once into every
   language but its own (the same `_translate_all` as
   [ANNOUNCEMENT_TRANSLATION.md](ANNOUNCEMENT_TRANSLATION.md), stored in
   `announcement_translations`), records a SHA-256 of its text in
   `announcement_verifications`, and turns the card into "Authenticated by …".
3. The announcement is published (the usual Discord **Publish** button; Moddy
   does not publish it). In every following server Discord posts a copy.
   Moddy recognises a copy by:
   - `flags.is_crossposted` (`IS_CROSSPOST`), which only Discord sets;
   - a `webhook_id`;
   - a `reference` to the support server **and** a watched channel.
4. Each copy is recorded in `announcement_crossposts` (dedupe). If the
   announcement is authenticated and the copy's text has the same hash, Moddy
   replies under it with the confirmation card, in the **server language**,
   with a **Translate** button when translations exist.
5. A copy that arrived *before* authentication stays recorded and unanswered;
   pressing Authenticate starts a background pass that answers them
   (`answer_backlog`, 0.25 s between replies).
6. **Translate** (public) shows, ephemerally, the stored translation in the
   reader's language (`i18n.get_user_locale` → `fr`/`en`/`es`/`pt`/`de`). A
   reader already in the announcement's language is told so.

### Edits

Editing an authenticated announcement in the support server withdraws its
authentication (row deleted) and posts a new Authenticate card marked as
edited. Copies that arrive afterwards (or that arrived with a different text)
are answered once it is re-authenticated. Confirmations already posted are
**not** removed — Discord updates the copies' text, the card stays. Only
the team can post or edit in that channel.

---

## Scope

| | |
|---|---|
| Source guild | `MODDY_TEAM_GUILD_ID` |
| Source channels | `ANNOUNCEMENT_VERIFICATION_CHANNEL_IDS` — defaults to `1410338969107042515` (the followed channel) |
| Who can authenticate | Moddy team Managers and Developers (super-admin and Discord dev team included) |
| Languages | `fr`, `en`, `es`, `pt`, `de` (DeepL via `bot.gateway.translation`) |

A server where Moddy cannot send in the updates channel simply gets no
confirmation (logged at INFO, copy marked answered — no retry).

---

## Files

| File | Role |
|---|---|
| `cogs/announcement_verification.py` | Listeners (posts, copies, edits), cards, `VerifyAnnouncementButton` / `TranslateAnnouncementButton` dynamic items, backlog |
| `db/repositories/announcement_verifications.py` | Verification rows + relayed copies |
| `db/base.py` | `announcement_verifications`, `announcement_crossposts` tables |
| `config.py` | `ANNOUNCEMENT_VERIFICATION_CHANNEL_IDS` |
| `locales/*.json` | `announcement_verification.*` |
| `tests/test_announcement_verification.py` | Relay detection, role gate, dedupe/backlog, edits, translate button, i18n |

---

## Storage

```sql
CREATE TABLE announcement_verifications (
    message_id   BIGINT      PRIMARY KEY,   -- the support-server announcement
    guild_id     BIGINT      NOT NULL,
    channel_id   BIGINT      NOT NULL,
    content_hash TEXT        NOT NULL,      -- sha256 of the stripped text
    verified_by  BIGINT      NOT NULL,
    verified_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE announcement_crossposts (
    message_id        BIGINT      PRIMARY KEY,  -- the copy in a following server
    source_message_id BIGINT      NOT NULL,
    guild_id          BIGINT      NOT NULL,
    channel_id        BIGINT      NOT NULL,
    answered_at       TIMESTAMPTZ,              -- NULL = waiting for authentication
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- partial index on (source_message_id) WHERE answered_at IS NULL
```

---

## Persistence

Both buttons are `DynamicItem`s carrying the announcement's message id
(`moddy:annver:verify:<id>`, `moddy:annver:translate:<id>`), registered by
`AnnouncementVerificationView` in `utils/persistent_views.py`. Nothing lives
in memory: the role check, the translations and the verification are re-read
on every click.
