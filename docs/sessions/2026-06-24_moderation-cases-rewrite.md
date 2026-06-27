# 2026-06-24 — Moderation cases system rewrite

## What was done

Replaced the legacy single-table moderation cases system with a new, fully
decoupled **case / sanction / event** model, and built a scalable service so
that *every* sanction in Moddy flows through cases.

### New model
- A case is a folder decoupled from Discord: `subject` / `issuer` / `scope` are
  each a `(*_type, *_id)` couple (extensible without migration).
- A case carries N sanctions (each with its own lifecycle) + a chronological
  event timeline (comments / notes / evidence / system events).
- Status is binary (`open`/`closed`), auto-derived from active sanctions, with a
  `status_locked` flag so manual open/close wins over auto-recompute.
- Public `reference` = 6-char code (alphabet without O/0, I/1), retry on collision.
- UUIDs generated in Python (no pgcrypto dependency).

### Scalable sanction service (key requirement)
- `services/case_service.py` — single entry point. Subsystems name a *source*;
  a `CaseSource` maps that source onto the case model. Adding a sanction kind =
  one `register_source(...)` call.
- Two sources ship: `global` (Moddy-team blacklists + global sanctions; a `ban`
  here = full bot blacklist) and `guild` (per-server sanctions).
- `record_sanction` de-duplicates onto an existing open case of the same
  `(subject, type, scope)`; `revoke_sanction` lifts active sanctions.

### Auto-sync from Discord
- `cogs/case_sync.py` listens to `on_audit_log_entry_create` and auto-opens
  guild cases on ban / kick / timeout (mute) — even when the action didn't go
  through Moddy — capturing the real moderator + reason. Unban / timeout-cleared
  revoke the matching sanction (case then auto-closes).

### Staff commands (`/mod case`)
- Rewrote create / view / list / edit / close / note; added `sanction` and
  `revoke`. `view` renders a Linear-style sidebar + timeline. `create` is
  driven by the source registry. New permission `case_sanction`.

### Periodic expiry
- `bot.py::case_expiry` (every 2 min) expires due temporary sanctions, logs the
  event and recomputes case status.

## Files modified / added
- Added: `services/case_service.py`, `cogs/case_sync.py`,
  `staff/commands/mod/case/sanction.py`, `staff/commands/mod/case/revoke.py`,
  `docs/MODERATION_CASES.md`.
- Rewrote: `utils/moderation_cases.py`, `db/repositories/moderation.py`,
  `utils/case_management_views.py`, `cogs/cases_user.py`,
  `staff/commands/mod/case/{_shared,create,view,list,edit,close,note}.py`.
- Schema: `db/base.py` (ENUM types + `cases` / `case_sanctions` / `case_events`
  + indexes; drops legacy `moderation_cases`).
- Wiring: `bot.py` (`self.cases`, expiry loop), `cogs/blacklist_check.py` and
  `modules/interserver.py` (read sanctions via the new model),
  `utils/emojis.py` (action/type emojis), `utils/staff_role_permissions.py`,
  `locales/{fr,en-US}.json`, `CLAUDE.md`.

## Decisions
- **Dropped the legacy `moderation_cases` table** (incompatible schema; the old
  migration already truncated on type change). No data migration.
- Implemented `status_locked` (recommended in the spec) for manual overrides.
- Kept `ON DELETE CASCADE`; no delete command (history preserved).
- The expiry job runs on the bot (asyncio loop) rather than the backend, since
  this repo is the bot and owns the schema.

## Verification
- Validated against a throwaway PostgreSQL 16 cluster: schema init, create case +
  sanction, read timeline, `has_active_sanction`, add/revoke sanction, auto-close
  recompute, manual lock, expiry job, list/count, plus the service's link/dedup
  and revoke paths — all pass. All changed modules import; all 8 staff case
  commands register; i18n keys resolve in `fr` and `en-US`.

## Follow-ups / known issues
- Guild auto-sync needs **View Audit Log** in each guild; it currently applies to
  all guilds (could be gated behind a module/config later if it's too noisy).
- Backend/dashboard read paths (internal API) not added here — the tables are
  ready for them.
- `network` / `external` sources are modelled but not registered yet (add via
  `register_source` when needed).
