# 2026-08-10 — Global sanction workflow: staff commands, groups, notices, countdown

Follow-up to `2026-08-10_global-sanctions-cases-system.md`, which moved global
sanctions into the cases system. This session builds the **workflow** on top of
it: staff commands, case groups, the grouped notice DM, the 48h appeal
countdown, and the backend Redis contract.

## What was done

### Case groups are the unit of work

One breach hits several subjects (the user *and* their server), so the whole
flow works on `cases.group_id` — the existing column, no new mechanism:

- `/mod global apply` opens one case per target sharing one fresh `group_id`.
- The subject gets **one** DM for the whole group, never one per case.
- `view` / `halt` / `lift` accept a group id **or** any case reference inside it.
- New queries: `db.list_group_cases()`, `db.get_case_group_id()`.

### Staff commands — `/mod global`

| Command | Node | Purpose |
|---|---|---|
| `apply` | `global_sanction` | Sanction user and/or servers as one group |
| `view` | `case_view` | Group status + countdown (live Halt button) |
| `halt` | `global_enforcement` | Stop the countdown — appeal filed |
| `lift` | `global_sanction` | Revoke the whole group, cancel the countdown |
| `pending` | `case_list` | The enforcement queue |

`apply` takes only the targets; a **Modals V2** form (TextDisplay + Select +
3 TextInputs = the 5-component max) collects level, reason, duration and grace
period. Both transports work (`/mod global apply` and `mod.global apply`).

Two new permission nodes registered in `utils/staff_role_permissions.py`.
`_has_node` was extracted from the staff dispatcher into
`utils/staff_permissions.has_staff_node()` so the persistent Halt button can
re-derive authorization on every click.

### The appeal countdown

New table `case_enforcements` (one row per group) +
`db/repositories/enforcements.py`. Status `pending` → `halted` / `executed` /
`cancelled`. `bot.enforcement_sweep` (5 min) claims due rows with
`UPDATE … FOR UPDATE SKIP LOCKED`, so a restart mid-sweep can never
double-execute. On execution the bot leaves the suspended servers itself and
publishes the billing/data side for the backend.

### Notices

`utils/global_sanction_views.py`: the grouped notice DM (generic English intro,
case list, effects, deadline block, Details/Appeal/Terms link buttons), the
"appeal received" DM, and the guild-join refusal DM. Every panel wears the
**accent colour of its level** — yellow / orange / red.

### What a suspended user keeps

Allowlists in `utils/global_sanctions.py`, consulted by the interaction gates
**before** any DB lookup: `/mycases`, `/moddy`, `/ping`, appeal components
(`moddy:apl:*`), the personal cases browser (`…:user` only, never the guild
one) and the `/moddy` panel.

### Refusing to join a server

`on_guild_join` now refuses three ways instead of two, with the policy as a
pure function (`global_sanctions.decide_join_refusal`):

- the **server** is suspended (a *limited* server keeps Moddy — its existing
  setup must keep working);
- the **owner** is limited **or** suspended;
- the **person who added the bot** is limited **or** suspended, resolved from
  the audit log (`AuditLogAction.bot_add`), best-effort — an unreadable audit
  log never refuses a legitimate join.

A limitation is enough to refuse a person because it freezes growth, and a new
server is growth. The refusal DM names which case applies.

### Backend events — `moddy:sanctions`

`global_sanction_applied` / `enforcement_halted` / `enforcement_executed` /
`global_sanction_lifted`, all fire-and-forget. `enforcement_executed` carries
`cancel_subscription` and `purge_guild_data`, which is what drives billing.

## Files

- New: `services/global_sanction_service.py`, `utils/global_sanction_views.py`,
  `db/repositories/enforcements.py`, `staff/commands/mod/global_sanction/`
  (5 commands + `_shared.py`), `tests/test_global_sanction_flow.py` (41 tests)
- `bot.py` (service, sweeper, allowlists, join refusal), `db/base.py` (table),
  `db/repositories/moderation.py` (group queries),
  `utils/global_sanctions.py` (accents, emojis, allowlists),
  `utils/staff_permissions.py`, `utils/staff_role_permissions.py`,
  `staff/framework/cog.py`, `utils/persistent_views.py`
- `locales/*.json` — `global_sanctions.notice/halt/join_refused/staff`,
  fully translated in the 5 supported languages
- Docs: `GLOBAL_SANCTIONS.md` (§6–11), `DATABASE.md`,
  `STAFF_COMMANDS_FRAMEWORK.md`, `CLAUDE.md`

## Decisions

- **The countdown belongs to the group, not the case.** One deadline, one DM,
  one halt — otherwise a three-case infraction would spam three notices and
  need three halts.
- **A guild-only sanction still schedules a countdown.** Otherwise Moddy would
  never leave the server it just suspended. Its notice goes to the owner.
- **Atomic claim, not select-then-update.** `FOR UPDATE SKIP LOCKED` is the
  only thing standing between a restart and a double subscription cancellation.
- **Premium is read *before* the sanction applies.** `is_subscribed` already
  returns False for a sanctioned user, so the service reads the raw
  subscription — the point is precisely to warn a paying user.
- **English notices by default.** The subject has no interaction to read a
  locale from; keys stay translatable and staff panels use the moderator's
  locale.
- **Directory named `global_sanction/`**, not `global/` — `global` is a Python
  keyword and could not be imported.
- **Warn schedules nothing.** It restricts nothing, so there is nothing to
  defer or appeal against.

## Follow-ups

- Nothing resumes a halted countdown automatically when an appeal is **refused**
  — `db.resume_enforcement()` exists but no command calls it yet. A
  `/mod global resume` would close the loop.
- The bot leaves suspended guilds but never re-joins if the sanction is lifted
  after execution; the owner has to re-invite.
- `docs/DATABASE.md` still documents a legacy `moderation_cases` table that the
  cases system replaced — pre-existing drift, untouched here.
