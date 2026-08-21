# AltGuard ↔ bot — exact wire contract

> Debugging reference. Everything below describes **what `moddy-bot` actually
> sends and expects**, byte for byte, so the AltGuard side can be diffed
> against it. The functional overview lives in [ALTGUARD.md](ALTGUARD.md); this
> file is the plumbing.
>
> Bot implementation: [`services/altguard_client.py`](../services/altguard_client.py).

---

## 0. Environment (bot side)

| Variable | Default | Used for |
|---|---|---|
| `ALTGUARD_API_URL` | `https://verify.moddy.app` | base of both HTTP endpoints. Trailing `/` is stripped at import time. |
| `ALTGUARD_BOT_TOKEN` | *(empty)* | `Authorization: Bearer <token>` on both endpoints |
| `REDIS_URL` / `REDIS_PASSWORD` | — | the bot's existing Redis; AltGuard must point at the **same instance** |

**If `ALTGUARD_BOT_TOKEN` is empty, no HTTP call is ever made** — the member
gets `Verification unavailable` with code `not_configured` and nothing appears
in the AltGuard logs. That is the single most common cause of the symptom.

The URL is built as plain concatenation: `ALTGUARD_API_URL + "/altguard/token"`.
With the default that is exactly `https://verify.moddy.app/altguard/token`.

---

## 1. `POST /altguard/token` — bot → service

Sent once per consent-modal submission.

### Request

```http
POST /altguard/token HTTP/1.1
Host: verify.moddy.app
Authorization: Bearer <ALTGUARD_BOT_TOKEN>
Content-Type: application/json

{
  "discord_user_id": "987654321098765432",
  "guild_id": "123456789012345678",
  "consent_version": "2026-08",
  "consent_at": "2026-08-18T10:00:00.123456Z"
}
```

| Field | Type on the wire | Notes |
|---|---|---|
| `discord_user_id` | **string** | snowflake as a decimal string, never an int |
| `guild_id` | **string** | idem |
| `consent_version` | string | `CONSENT_VERSION` in `services/altguard_client.py` — currently `"2026-08"` |
| `consent_at` | string | ISO 8601 **UTC**, `+00:00` rewritten as `Z`. Includes microseconds. Always ≤ now, taken at modal submit, a fraction of a second before the request |

- Header is `Content-Type: application/json` (aiohttp `json=` payload).
- No other header is sent — no `User-Agent` override, no API version header,
  no cookie.
- Timeout: **15 s total**. Anything slower is a `network_error` for the bot.
- The bot does **not** retry: one submit, one call.

### Expected response

`200` with a JSON object:

```json
{
  "authorization_url": "https://discord.com/oauth2/authorize?...&state=...",
  "expires_at": "2026-08-18T10:20:00Z"
}
```

- `authorization_url` — required. Rendered as a link button. If the key is
  missing or empty, the bot still shows the card but **without a button**
  (silent-looking failure — worth checking if members report "nothing to click").
- `expires_at` — optional. Parsed with `datetime.fromisoformat` after replacing
  a trailing `Z` with `+00:00`; a naive timestamp is assumed UTC. Unparseable →
  the countdown line is simply omitted, no error.
- Any other key in the body is ignored.
- The response **must** be JSON. A `200` with an HTML body (a proxy error page,
  a redirect landing) raises `network_error` at `response.json()`.

### Status → what the member sees

| Status | `AltGuardError.code` | Card shown | Retried? |
|---|---|---|---|
| `200` | — | the link | — |
| `400` | `bad_request` | *Verification unavailable* | never (it is a caller bug) |
| `401` | `unauthorized` | *Verification unavailable* | never |
| `429` | `rate_limited` | *Too many attempts* + `Retry-After` | member retries manually |
| `5xx` | `service_unavailable` | *Verification unavailable* | member retries manually |
| other `3xx`/`4xx` | `unexpected_status` | *Verification unavailable* | — |
| connection refused, DNS, TLS, timeout | `network_error` | *Verification unavailable* | — |
| `ALTGUARD_BOT_TOKEN` empty | `not_configured` | *Verification unavailable* | — |

The code is printed under the card (`Technical code: …`) and logged in full:

```
[AltGuard] Token request failed for <user_id> in guild <guild_id>: <code> (<message>)
```

For `bad_request` the message carries the first 500 characters of the service's
response body — that is where a validation error from AltGuard shows up.

### Reproducing a failure by hand

```bash
curl -i -X POST "$ALTGUARD_API_URL/altguard/token" \
  -H "Authorization: Bearer $ALTGUARD_BOT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"discord_user_id":"987654321098765432",
       "guild_id":"123456789012345678",
       "consent_version":"2026-08",
       "consent_at":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'"}'
```

Run it from the bot's own host: a service reachable from a laptop but not from
Railway (private networking, IP allowlist, egress rules) is exactly the
`network_error` case.

### Checklist for the AltGuard side

- [ ] Route registered at **`/altguard/token`** (not `/token`, not `/api/...`).
- [ ] Accepts `discord_user_id` / `guild_id` as **strings** — a schema typed
      `int` rejects the bot's payload with `400`.
- [ ] Accepts `consent_at` **with microseconds** and a `Z` suffix.
- [ ] Bearer scheme, not `X-Api-Key`, not Basic.
- [ ] Answers JSON on `200`, with `authorization_url`.
- [ ] `429` carries `Retry-After` in seconds.
- [ ] TLS certificate valid for the host in `ALTGUARD_API_URL` (the bot never
      disables verification).

---

## 2. `POST /altguard/membership/resync` — bot → service

Sent 2 minutes after startup, then hourly, once per guild that has AltGuard
configured **and** whose member cache is complete.

```http
POST /altguard/membership/resync
Authorization: Bearer <ALTGUARD_BOT_TOKEN>
Content-Type: application/json

{"guild_id": "123456789012345678",
 "active_discord_user_ids": ["987654321098765432", "..."]}
```

- Ids are **strings**, bots excluded, and the list is the **complete** current
  membership — not a diff.
- Expected: `200` with `{"reconciled": true}`. The bot only reads that boolean.
- Same error mapping as above; failures are logged
  (`[AltGuard] Resync failed for guild <id>: …`) and retried on the next tick.
- A guild whose cache is incomplete (`len(guild.members) < guild.member_count`)
  is **skipped**, so a service that sees no resync for a large guild should
  check the bot's member intent/chunking rather than the endpoint.

---

## 3. `altguard:verdict` — service → bot (Redis Pub/Sub)

Channel name is exactly `altguard:verdict`, on the **same Redis** as
`moddy:*`. The bot subscribes in `bot._listen_pubsub` at startup, alongside
`moddy:bot`, `moddy:subscription:updates` and `moddy:blacklist:updates`.

The published value must be a **JSON string** (the bot connects with
`decode_responses=True` and calls `json.loads` on `message["data"]`):

```json
{"verification_id": "3f6c…-uuid", "guild_id": 123456789012345678,
 "discord_user_id": 987654321098765432, "verdict": "passed",
 "score": 62, "reasons": ["cookie_match", "gpu_match"], "enforced": true,
 "matches": [{"discord_user_id": 456789123, "score": 62,
              "reasons": ["cookie_match", "gpu_match"]}]}
```

| Field | Accepted | Behaviour when absent/invalid |
|---|---|---|
| `verification_id` | any non-empty string | **message dropped** |
| `verdict` | `passed` \| `flagged` \| `blocked` | **message dropped** |
| `guild_id`, `discord_user_id` | int **or** numeric string (both are `int()`-ed) | **message dropped** |
| `score` | int or numeric string | stored as `NULL` |
| `reasons` | list of strings | stored as `[]` |
| `enforced` | bool | **defaults to `false`** → logged, nothing applied |
| `matches` | list of `{discord_user_id, score, reasons}`, up to 5, most-linked first, always `[]` on a `passed` | stored as `[]`; malformed entries are dropped individually |

> **`matches` is audit data for the log channel, nothing else.** It names the
> accounts a verification matched with, so a moderator can understand a
> `blocked` verdict and undo it if it's wrong — rendered on the log card, see
> [docs/ALTGUARD.md](ALTGUARD.md). It is
> **never** sent to the verified member (DM or ephemeral reply): same reason as
> `score` and `reasons` — it would tell them exactly which signal to change to
> pass next time. Read with `payload.get("matches", [])` — the field is always
> present on the wire, but a bot deployed ahead of the service must not break
> on its absence.

> **`enforced: true` is what applies the roles.** This is the single most
> common reason a verification "works" end to end and yet the member keeps the
> unverified role: the verdict arrives, the log card says *shadow mode*, and
> nothing moves. The bot never infers enforcement — a guild that has not turned
> it on must not be sanctioned by accident — so the service has to send the
> field, set to `true`, for every guild whose gate is live.
>
> The bot tells the two cases apart:
> - field **absent** → `WARNING` in the bot log (*"carries no 'enforced' field"*)
>   and a log card saying the service did not send it. Service-side defect.
> - field present and `false` → the ordinary *shadow mode* card. Deliberate.

Two consequences worth checking when a verdict "does nothing":

1. **`enforced` must be explicitly `true`** for roles to move. Omitting it is
   read as shadow mode by design.
2. **`verification_id` is the idempotency key.** Re-publishing the same id (a
   retry, a replay after reconnect) inserts nothing into
   `altguard_verifications` and stops there — the first message wins.

A verdict for a guild where the module is not configured (or not enabled — it
needs channel + both roles) is logged at debug level and ignored.

---

## 4. `altguard:membership` — bot → service (Redis Pub/Sub)

```json
{"event": "membership", "guild_id": 123456789012345678,
 "discord_user_id": 987654321098765432,
 "state": "active", "occurred_at": "2026-08-18T10:00:00Z"}
```

- `event` is always the literal `"membership"`.
- `guild_id` / `discord_user_id` are **ints** here (they are Redis-internal,
  not an HTTP schema).
- `state` ∈ `active` | `left` | `kicked` | `banned`.
- `occurred_at` is always present, ISO 8601 UTC with `Z`.
- Published only for guilds where the AltGuard module is enabled, and never for
  bots.
- Nothing is published when Redis is down; the hourly resync is the repair
  path.

---

## 5. Symptom → cause table

| What the member/moderator sees | Look at |
|---|---|
| *Verification unavailable*, code `not_configured` | `ALTGUARD_BOT_TOKEN` missing in the bot's environment |
| code `unauthorized` | token mismatch between bot and service |
| code `bad_request` | payload rejected — the bot's log line carries the service's own message; usually a schema expecting ints instead of strings, or a stricter `consent_at` format |
| code `network_error` | DNS/TLS/timeout/egress; reproduce with the curl above **from the bot host** |
| code `service_unavailable` | service returned 5xx |
| code `unexpected_status` | a redirect or an unusual 4xx — often a wrong path returning `404`, or a proxy answering `405` |
| Card with no button | `200` returned without `authorization_url` |
| Verdict published, log card says *shadow mode*, roles unchanged | `enforced` is `false` or missing — see the callout in §3. The card names which of the two it is |
| Verdict published, no log card at all | same `verification_id` already processed (idempotency), or the module is not enabled on that guild |
| Verdict enforced, role still not applied | the member left, or the bot's role sits below the verified role in the hierarchy — the bot logs both |
| No membership events reaching the service | module disabled on the guild, Redis down, or a different Redis instance on each side |
