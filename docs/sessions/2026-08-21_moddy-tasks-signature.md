# 2026-08-21 — `moddy:tasks` HMAC signature

## What was done

Implemented the bot side of the `moddy:tasks` signature contract. Every entry
of the Redis stream is now HMAC-SHA256 verified before it is executed; unsigned,
forged, stale or replayed entries are skipped and logged instead of running with
the bot's Discord permissions.

- New `utils/task_signature.py`: canonicalization identical to the backend
  (double JSON serialization, `separators=(",", ":")`, `sort_keys=True`,
  `ensure_ascii=True`, `signature` excluded), `hmac.compare_digest`, freshness
  window (`REPLAY_WINDOW=300`, `CLOCK_SKEW=60`) and `SET NX EX 600` anti-replay
  keyed on `task_id`.
- `bot.py::_consume_task_stream` calls `verify_task()` before `_process_task`.
- `config.py`: `TASK_STREAM_SECRET` + `TASK_STREAM_ALLOW_UNSIGNED`, with a boot
  error when the secret is missing or shorter than 32 chars, and a warning while
  the escape hatch is on.
- `tests/test_task_signature.py`: 31 tests, all passing.
- Docs: new `docs/TASK_SIGNATURE.md`, plus updates to `CLAUDE.md`,
  `docs/RAILWAY.md`, `docs/BACKEND-INTEGRATION.md`, `docs/REDIS_COMMUNICATION.md`.

## Files modified

| File | Change |
|---|---|
| `utils/task_signature.py` | **new** — verification helpers (`verify_task`, `sign_fields`, `TaskRejected`) |
| `bot.py` | Verification in the stream consumer + resume point always advances |
| `config.py` | `TASK_STREAM_SECRET`, `TASK_STREAM_ALLOW_UNSIGNED`, boot diagnostics |
| `tests/test_task_signature.py` | **new** — contract test suite |
| `docs/TASK_SIGNATURE.md` | **new** — full contract |
| `CLAUDE.md`, `docs/RAILWAY.md`, `docs/BACKEND-INTEGRATION.md`, `docs/REDIS_COMMUNICATION.md` | Cross-references, env vars, key inventory |

## Decisions

- **Fail closed without a secret.** `verify_task` raises `no_secret` rather than
  falling back to executing tasks: an unverifiable task is exactly what the
  contract exists to stop. The boot-time error in `config.py` makes the
  misconfiguration loud.
- **`TASK_STREAM_ALLOW_UNSIGNED` escape hatch.** The contract forbids tolerating
  unsigned entries, but §6 describes a window where the bot ships before the
  backend signs. The flag defaults to `false`, only covers *absent* signatures
  (a wrong signature is always rejected), and warns at boot while enabled.
- **The resume point now always advances.** The consumer previously left
  `moddy:tasks:last_id` untouched when `_process_task` raised, so a failing
  entry was re-read in a tight loop forever. That is also the behaviour the
  contract requires for rejected entries (rule 3: skip and log, never replay),
  so both paths now advance the cursor.
- **The dedup marker is only burned for otherwise-valid entries** (signature and
  freshness first), so an attacker cannot flood the `task:seen:*` keyspace with
  garbage.
- **Tests load the module by path** rather than through `utils/__init__.py`,
  which imports discord.py — the suite stays runnable with stdlib + pytest only.
  One test recomputes a signature straight from the spec snippet rather than via
  the helper, so a drift in canonicalization is caught rather than mirrored.

## Follow-ups

- Deployment: set `TASK_STREAM_SECRET` (same value) on both Railway services
  **before** deploying, ship the bot first, then the backend, then make sure
  `TASK_STREAM_ALLOW_UNSIGNED` is off. See `docs/TASK_SIGNATURE.md` §6.
- The backend must start signing; until it does, tasks are rejected unless the
  escape hatch is enabled.
