# `moddy:tasks` signature (backend ⇄ bot contract)

> **Breaking change.** The bot rejects every unsigned entry on the
> `moddy:tasks` stream. The backend refuses to produce tasks without
> `TASK_STREAM_SECRET`. Read [§6 Deployment order](#6-deployment-order) before
> shipping anything to production.

## 1. Why

The `moddy:tasks` stream carries actions with Discord side effects: bot
avatar/bio changes, announcements, panel updates, dashboard sanctions. Anyone
with write access to Redis could inject an arbitrary task and have the bot
execute it with the bot's own permissions. The backend now signs every entry
with HMAC-SHA256; the bot refuses anything it cannot verify.

## 2. The secret

| | |
|---|---|
| Variable | `TASK_STREAM_SECRET` |
| Minimum length | **32 characters** (the backend refuses to sign below that) |
| Generation | `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| Scope | shared backend ⇄ bot, **identical on both sides** |

**Never** reuse `REDIS_PASSWORD`: the threat model is precisely an attacker who
already has Redis access.

## 3. Stream entry fields

Six fields, all strings:

| Field | Content |
|---|---|
| `type` | task type (`bot_customization_update`, `send_announcement`, `update_panel`, `case_add_sanction`, `social_*`…) |
| `guild_id` | decimal guild id, `"0"` when there is no guild |
| `payload` | the business payload, JSON-serialized compact and sorted |
| `task_id` | UUID v4, unique per task — the deduplication key |
| `issued_at` | Unix timestamp in seconds, UTC |
| `signature` | lowercase hex HMAC-SHA256 of the five fields above |

## 4. Canonicalization

This is the most fragile part of the contract: the slightest serialization
mismatch fails 100% of verifications.

```python
payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)

fields = {
    "type":      task_type,
    "guild_id":  str(guild_id),
    "payload":   payload_json,
    "task_id":   str(uuid.uuid4()),
    "issued_at": str(int(time.time())),
}

canonical = json.dumps(fields, separators=(",", ":"), sort_keys=True)
signature = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
```

Rules, identical on both sides:

- **Double serialization.** `payload` is a JSON *string* inside the canonical
  object, so its quotes are escaped in `canonical`.
- `separators=(",", ":")` — no whitespace.
- `sort_keys=True` at both levels. The key order of `canonical` is therefore
  always `guild_id, issued_at, payload, task_id, type`.
- `ensure_ascii=True` (the Python default) — non-ASCII characters are escaped
  as `\uXXXX`. A bot written in another language must reproduce that escaping.
- The `signature` field is **excluded** from the computation.
- UTF-8 encoding for both the secret and the canonical string.

## 5. Verification on the bot side

Implemented in [`utils/task_signature.py`](../utils/task_signature.py), called
from `bot.py::_consume_task_stream` before every `_process_task`.

```python
REPLAY_WINDOW = 300   # 5 min: maximum accepted age
CLOCK_SKEW    = 60    # tolerance when the backend clock runs ahead
DEDUP_TTL     = 600   # > REPLAY_WINDOW, otherwise a replay becomes possible again
```

Order of checks (`verify_task`), each raising `TaskRejected(code)`:

| Step | Code on failure |
|---|---|
| Secret configured | `no_secret` |
| Signature present | `unsigned` |
| All five signed fields present | `missing_field` |
| `hmac.compare_digest(signature, expected)` | `bad_signature` |
| `now - REPLAY_WINDOW <= issued_at <= now + CLOCK_SKEW` | `expired` / `future` |
| `SET task:seen:{task_id} 1 NX EX 600` returns fresh | `replay` |

Non-negotiable rules:

1. **An entry without `signature` is rejected**, never tolerated "for
   compatibility". That is exactly the hole the signature closes.
2. **`hmac.compare_digest`**, never `==` — `==` leaks the signature through
   timing.
3. A rejected entry is skipped (the `moddy:tasks:last_id` resume point still
   advances) and logged at `warning`, never retried: otherwise an attacker
   fills the stream with invalid entries and blocks the consumer. The same
   applies to an entry whose execution raises.
4. Deduplication is keyed on `task_id`, not on content: two identical
   legitimate announcements must be able to go out in a row.
5. The dedup marker is only burned for an otherwise-valid entry, so garbage
   cannot flood the `task:seen:*` keyspace.
6. No secret ⇒ **fail closed**: nothing can be verified, so nothing is
   executed. `config.py` logs an explicit error at boot.

## 6. Deployment order

Order matters, in both directions.

1. Generate the secret and set it on **both** Railway services (bot and
   backend) before deploying any code.
2. Deploy **the bot first**, in verification mode. It already accepts signed
   entries; there are none yet, so nothing changes — set
   `TASK_STREAM_ALLOW_UNSIGNED=true` for that window if unsigned tasks must
   keep flowing.
3. Deploy the backend next. It starts signing.
4. Once the backend is in production and tasks are going through, remove
   `TASK_STREAM_ALLOW_UNSIGNED` (or set it to `false`) so unsigned entries are
   rejected.

The reverse order (backend first) is functionally harmless — the bot ignores
extra fields — but leaves a window with no protection.

`TASK_STREAM_ALLOW_UNSIGNED` only covers *absent* signatures; a wrong or forged
signature is always rejected. It defaults to `false`, and `config.py` warns at
boot while it is on.

**If `TASK_STREAM_SECRET` is missing on the backend:** the service still boots,
logs an explicit error, and every route producing a task returns `503`. The
affected features are `bot_customization`, staff announcements
(`POST /staff/bot/announce`) and `update_panel` on modules.

**If `TASK_STREAM_SECRET` is missing on the bot:** the consumer keeps running
but rejects every entry with `no_secret`, logged as a warning per task.

## 7. What the backend does not do

Anti-replay is **entirely the consumer's responsibility**. The backend supplies
the material (`task_id`, `issued_at`) but keeps no registry: it never sees what
gets consumed. Without §5, an attacker with Redis access can still copy a
legitimate signed task and have it re-executed.

## 8. Environment variables

| Variable | Service | Default | Purpose |
|---|---|---|---|
| `TASK_STREAM_SECRET` | bot + backend | — | Shared HMAC secret, 32 chars minimum |
| `TASK_STREAM_ALLOW_UNSIGNED` | bot | `false` | Deployment window only: accept entries with no signature |

## 9. Tests

```bash
python3 -m pytest tests/test_task_signature.py -q
```

Covers canonicalization (key order, whitespace, nested payload escaping,
non-ASCII), tampering with each signed field, wrong secret, the freshness
window edges, replay, dedup by `task_id` rather than content, fail-closed
without a secret, and the `allow_unsigned` escape hatch. One test recomputes a
signature straight from the spec snippet in §4 rather than through the helper,
so a drift in the canonicalization is caught.
