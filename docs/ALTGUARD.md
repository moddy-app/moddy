# AltGuard — anti multi-account verification

AltGuard holds every joining human behind a verification gate until an external
service (`verify.moddy.app`) decides whether their account looks like a second
account of someone already on the server.

The bot **never** sees a personal datum in that process — no fingerprint, no
email, no IP, no guild list. Everything crossing the boundary is Discord ids
(already public on the server), statuses, and category labels. That is the
central guarantee of the design, not an implementation detail: anything that
would move personal data into the bot is a compliance regression.

| | |
|---|---|
| Module id | `altguard` |
| Module | [modules/altguard.py](../modules/altguard.py) |
| Config UI | [modules/configs/altguard_config.py](../modules/configs/altguard_config.py) |
| Views | [utils/altguard_views.py](../utils/altguard_views.py) |
| Service client | [services/altguard_client.py](../services/altguard_client.py) |
| Cog (verdicts, events, `/altguard`) | [cogs/altguard.py](../cogs/altguard.py) |
| Staff commands | [staff/commands/mod/altguard/](../staff/commands/mod/altguard/) |
| Repository | [db/repositories/altguard.py](../db/repositories/altguard.py) |
| Tests | [tests/test_altguard.py](../tests/test_altguard.py) |

---

## Member journey

1. **Join.** `cogs/module_events.py` dispatches `on_member_join` to AltGuard
   **before** Auto Role. The member gets the **unverified role** and a
   `pending` row in `altguard_members`. Bots are never gated — they cannot
   click a button.
2. **The panel.** One permanent message in the verification channel, with one
   button. Its wording ships with Moddy; a server picks only the language.
3. **Consent.** The button opens a Modal V2 stating exactly what is collected
   (browser characteristics, technical cookie, Discord email, server list, IP
   address), that the data is encrypted, that neither the Moddy team nor the
   server's moderators can read it, and linking the data notice
   (`https://moddy.app/AltGuard-data`), the terms and the privacy policy. Two
   checkboxes must be ticked — a `CheckboxGroup` with `min_values=2` and
   `required=True`, since a bare `Checkbox` cannot be made mandatory
   ([docs/MODALS_V2.md](MODALS_V2.md)). Closing the modal sends nothing
   anywhere.
4. **Token.** On submit, the bot calls `POST /altguard/token` with the Discord
   ids, `consent_version` and `consent_at`, and answers **ephemerally** with the
   personal authorization URL: single use, 20 minutes.
5. **Verification.** The member authorizes on Discord's own screen, the service
   runs its checks in the browser. The bot has nothing to do here.
6. **Verdict.** The service publishes on `altguard:verdict`. The bot applies it:
   `passed` → verified role (+ Auto Role hand-off), `flagged` / `blocked` → the
   member stays behind the gate. Everything is written to the optional log
   channel.

### What the member never sees

`score` and `reasons` are audit data. They appear in the guild's log channel and
in `/mod altguard refusal`, never in anything the verified member receives. The
service deliberately withholds them from the browser so a bypass attempt gets no
oracle; reproducing them bot-side would break the same guarantee.

---

## Configuration (`/config` → AltGuard)

Stored in `guilds.data.modules.altguard`:

| Key | Type | Required | Meaning |
|---|---|---|---|
| `channel_id` | int | ✅ | Verification channel — the only channel the unverified role may see |
| `unverified_role_id` | int | ✅ | Given on join, holds the member back |
| `verified_role_id` | int | ✅ | Given when the verification passes |
| `log_channel_id` | int | — | Verdicts and manual decisions |
| `panel_locale` | str | — | `fr`, `en-US`, `es-ES`, `pt-BR`, `de` (default `en-US`) |
| `message_id` | int | — | Bookkeeping: id of the posted panel |

The module is `enabled` only when the channel and **both** roles are set.

**The panel wording is not configurable.** Every server states the same thing
about the same data processing; only the language changes. The admin-facing
choice is where it lives, not what it says.

**The panel is re-posted on every save**: the old message is deleted and a fresh
one sent. That is what repairs a panel someone deleted, and the only way a
language or channel change reaches the message.

**Permissions to set up by hand:** the unverified role must have access to *no*
channel except the verification channel — Discord permissions are the server's
own job, the module only warns. Moddy needs **Manage Roles**, with its own role
above both gate roles.

### Link with Auto Role

`modules/auto_role.py` asks AltGuard before handing out anything
(`_altguard_holds_back`): a human who has not passed the gate gets no auto role,
or the gate would be pointless. Bots are exempt. The roles are applied later by
`AltGuardModule._run_auto_role`, the moment the gate opens (verdict `passed` or
a manual verification). A lookup failure **fails closed** — the roles wait.

---

## Service contract

Two transports, four messages. `ALTGUARD_BOT_TOKEN` and `ALTGUARD_API_URL` live
in the environment ([docs/RAILWAY.md](RAILWAY.md)); the Redis is the one the bot
already uses ([docs/REDIS_COMMUNICATION.md](REDIS_COMMUNICATION.md)).

### `POST /altguard/token` (bot → service)

```json
{"discord_user_id": "987...", "guild_id": "123...",
 "consent_version": "2026-08", "consent_at": "2026-08-18T10:00:00Z"}
```

→ `{"authorization_url": "...", "expires_at": "..."}`.

`401` invalid token · `400` malformed call (never retried automatically) ·
`429` quota (5 calls / 10 min / user, `Retry-After` honoured). The URL is never
reused: an expired link means a new consent screen and a new call.

`CONSENT_VERSION` in `services/altguard_client.py` must be bumped whenever the
consent wording in `locales/*.json → modules.altguard.consent` changes
materially — the service stores it as proof of what was agreed to.

### `POST /altguard/membership/resync` (bot → service)

```json
{"guild_id": "123...", "active_discord_user_ids": ["987...", "654..."]}
```

The **complete** set of current members, not a diff. Sent hourly and two
minutes after startup. A guild whose member cache is incomplete is *skipped*
rather than reconciled against a lie: an under-filled list would mark real
members as `left`.

### `altguard:verdict` (service → bot)

```json
{"verification_id": "uuid", "guild_id": 123, "discord_user_id": 987,
 "verdict": "passed | flagged | blocked", "score": 62,
 "reasons": ["cookie_match"], "enforced": true}
```

Routed by `bot._handle_altguard_verdict` → `AltGuard.handle_verdict`.

- `parse_verdict` refuses a payload without a usable `verification_id`,
  `verdict` or ids, and **defaults `enforced` to `false`**: an ambiguous message
  logs, it never sanctions.
- `enforced=false` is the service's shadow mode — the verdict is logged and
  nothing is applied: no role change, no message, no state write.
- **Idempotency** is the `verification_id` primary key of
  `altguard_verifications`: a replayed message (Redis reconnect, service retry)
  inserts nothing, and processing stops there.

### `altguard:membership` (bot → service)

```json
{"event": "membership", "guild_id": 123, "discord_user_id": 987,
 "state": "active | left | kicked | banned", "occurred_at": "..."}
```

`active` on join (including a return), `banned` on `on_member_ban`, and on
`on_member_remove` the audit log tells a kick from a voluntary leave — a
departure already reported as a ban is skipped rather than sent twice. Events
about accounts AltGuard has never verified are ignored on its side, so nothing
is filtered here. A ban is only undone by an explicit later `active` (the
account rejoining) — a resync never resurrects a banned account.

---

## Database

`altguard_verifications` — one row per verdict (audit + idempotency key + the
"refusal id" staff look up):

| Column | Notes |
|---|---|
| `id` (PK) | the service's `verification_id` |
| `guild_id`, `user_id` | Discord ids |
| `verdict` | `passed` / `flagged` / `blocked` |
| `score`, `reasons` | audit only — never shown to the member |
| `enforced` | false = shadow mode |
| `created_at` | |

`altguard_members` — the gate state the bot acts on:

| Column | Notes |
|---|---|
| `guild_id`, `user_id` (PK) | |
| `status` | `pending` / `verified` / `flagged` / `blocked` |
| `source` | `service` / `server_staff` / `moddy_staff` |
| `verification_id`, `decided_by` | what/who decided |
| `created_at`, `updated_at` | |

---

## Commands

### Server side — `/altguard` (requires **Manage Roles**)

| Command | Effect |
|---|---|
| `/altguard verify <member>` | Let a member through manually (`source=server_staff`), then hand them to Auto Role |
| `/altguard unverify <member>` | Send them back behind the gate |

### Moddy staff — `/mod altguard` (permission node `altguard_manage`)

| Command | Effect |
|---|---|
| `/mod altguard verify <user> <guild>` | Same, from outside the server (`source=moddy_staff`) |
| `/mod altguard unverify <user> <guild>` | Idem |
| `/mod altguard refusal <verification_id>` | The only place a decision is explained: verdict, score, signals, enforcement, current state |

---

## Views

| View | Persistent | Auth |
|---|---|---|
| `AltGuardPanelView` | ✅ | public — the clicker is the subject of the verification |
| `AltGuardConfigView` | ✅ | guild permissions, re-checked on every interaction |
| `AltGuardConsentModal` | modal | documented exclusion ([docs/PERSISTENT_VIEWS.md](PERSISTENT_VIEWS.md)) |

The ephemeral link card and the log cards are plain `LayoutView`s with no
interactive children beyond a link button — nothing to restore.

---

## Tests

```bash
pytest tests/test_altguard.py tests/test_persistent_views.py -q
```

Covers verdict parsing (including the shadow-mode default), the membership
payload, gate-role idempotency, the join gate, shadow mode changing nothing,
the Auto Role hold-back (and its fail-closed path), and the fact that the
member-facing link card carries no score.
