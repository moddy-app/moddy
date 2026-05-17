# Session — 2026-05-16 — Token Secrets: encrypted DB persistence for Invalidate button

## What was done

Follow-up to the 2026-05-09 session. The "Invalidate Token" button now survives bot restarts.

Previously: after a restart, `peek_alert_with_db_fallback` recovered metadata from `token_alerts`
but `data["token"]` was always `""`, so `UserConfirmInvalidateButton` would fall into the
"Token Already Cleared" branch even though the token had never been invalidated.

### Changes

**New table: `token_secrets`** (separate from `token_alerts`)
- `ck` (PK), `encrypted_token` (BYTEA), `user_id_hmac` (BYTEA), `week_number` (INTEGER), `created_at`
- Stores only the encrypted token. Metadata stays in `token_alerts` as before.

**Encryption scheme (AES-256-GCM):**
- `alert_key = HMAC-SHA256(TOKEN_DETECTOR_KEY, f"{ck}_{week_number}")`
- Encrypted blob = `nonce[12] + ciphertext + GCM tag` stored as BYTEA
- Double-lock: `ck` (in button custom_id) + `TOKEN_DETECTOR_KEY` are both required to decrypt

**New repository: `db/repositories/token_secrets.py`**
- `save_token_secret(ck, token, week_number, alert_key, master_key)` — encrypts + inserts
- `get_token_secret(ck, master_key)` — reads row, re-derives alert_key from stored week_number, decrypts
- `delete_token_secret(ck)` — explicit deletion after successful invalidation
- `cleanup_old_secrets(max_age_days=7)` — DELETE WHERE created_at < NOW() - 7 days

**`db/base.py`:**
- Imported `TokenSecretRepository`, added to `ModdyDatabase` MRO
- Added `token_secrets` table creation + index in `_init_tables`

**`cogs/token_detector.py`:**
- Added `import hashlib`, `import hmac`
- Added `_derive_alert_key(ck, week_number) -> bytes` helper
- `_alert_user` / `_alert_bot`: save token secret after `cache_alert()`, only when `TOKEN_DETECTOR_KEY` is set
- `peek_alert_with_db_fallback`: after loading from `token_alerts`, attempts `get_token_secret` and injects `data["token"]` if found
- `UserConfirmInvalidateButton.callback`: calls `delete_token_secret` after successful invalidation
- `cog_load`: `asyncio.create_task(_cleanup_token_secrets())` — fire-and-forget, logs result

## Files modified

| File | Change |
|---|---|
| `db/repositories/token_secrets.py` | **New** — `TokenSecretRepository` |
| `db/base.py` | Import + MRO + `token_secrets` table |
| `cogs/token_detector.py` | `_derive_alert_key`, secret save/fetch/delete, cleanup |
| `CLAUDE.md` | Added `token_alerts.py, token_secrets.py` to repo listing |
| `docs/DATABASE.md` | Documented `token_secrets` table |

## Decisions made

- **`TOKEN_DETECTOR_KEY` guard**: secret is only saved/fetched when the env var is set. If it's missing, the ephemeral Fernet key is used (same as before) and the DB path is skipped silently — no regression.
- **`get_token_secret` reads `week_number` from DB**: the caller doesn't need to know it. The method derives `alert_key` internally from the stored `week_number` + `master_key`.
- **Delete on invalidation**: `delete_token_secret` is called in `UserConfirmInvalidateButton` after a successful `/auth/logout`. If the API call fails, the secret stays (user can retry).
- **7-day cleanup**: matches the 7-day rotation window implied by `week_number`. Rows accumulate only for the small subset of alerts that were neither invalidated nor expired.
- **No change to `token_alerts`**: that table is untouched.
