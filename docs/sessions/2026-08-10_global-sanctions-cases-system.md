# 2026-08-10 — Global sanctions moved to the cases system

## What was done

Moddy-team sanctions (global sanctions) were only ever applied through a
`BLACKLISTED` attribute. They now live entirely in the cases system, with three
levels, on **users and servers**, and the old attribute system is gone.

### The three levels

| Level | `sanction_action` | Effect |
|---|---|---|
| Warn | `warn` | Informational only |
| Limited | `restrict` | No premium, no **new** module can be configured, automod AI off |
| Suspended | `ban` | No access to any Moddy service (replaces "blacklisted") |

Levels are ordered; a subject sits at the highest one it holds. `restrict` and
`ban` accept a duration, so a limitation or suspension can be **temporary** and
is swept by `bot.case_expiry` like any other sanction.

### New pieces

- **`utils/global_sanctions.py`** — the single resolver. `get_user_level`,
  `get_guild_level`, `get_context_level`, `is_limited`, `is_suspended`, plus an
  in-memory cache whose TTL is bounded by the soonest sanction expiry. Fails
  open on a DB error.
- **`db.list_active_global_actions()`** — active global sanctions of a subject
  (action + expiry).
- **`db.migrate_legacy_blacklist_attributes()`** — one-shot, idempotent
  migration of every remaining `BLACKLISTED` attribute into a global `ban`
  case, then drops the attribute. Runs in `bot.setup_database`.
- **`global_guild` case source** — Moddy-team sanctions against a server.
  `_manual_sources(subject_type)` now filters sources by subject nature, so a
  user target and a guild target each see only what applies.
- **`docs/GLOBAL_SANCTIONS.md`** — the reference for all of this.
- **`tests/test_global_sanctions.py`** — 21 tests (ladder, context resolution,
  cache invalidation, temporary-sanction TTL).

### Enforcement

| Point | File |
|---|---|
| Slash commands | `bot.py::_global_sanction_check` (renamed from `_global_blacklist_check`) |
| Components | `bot.py::_check_suspension_and_respond` |
| Prefix commands | `cogs/blacklist_check.py` (rewritten) |
| Bot added to a server | `bot.py::on_guild_join` |
| Premium | `utils/subscription.py` — `is_subscribed` / `is_guild_premium` |
| New modules | `modules/module_manager.py::_blocked_as_new_module` + `/config` |
| Automod AI | `modules/automod_ai.py::on_message` |

Every check covers **both** the acting user and the server they act in.

### Vocabulary fix

`restrict` is a platform-scope sanction and was leaking into server-facing UIs.
`/cases` (guild-scoped) now only ever offers guild actions, including in its
filter modal. Conversely, global cases no longer display raw action names:
`get_action_label_key()` swaps them for `global_sanctions.level.*`, so a global
`ban` reads "Suspended" and a global `restrict` reads "Limited" — in `/mod case`
panels and in `/mycases`.

## Files modified

- New: `utils/global_sanctions.py`, `docs/GLOBAL_SANCTIONS.md`,
  `tests/test_global_sanctions.py`
- `bot.py`, `cogs/blacklist_check.py`, `cogs/config.py`
- `services/case_service.py`, `modules/module_manager.py`, `modules/automod_ai.py`
- `db/repositories/moderation.py`, `db/base.py`
- `utils/components_v2.py`, `utils/subscription.py`, `utils/moderation_cases.py`,
  `utils/case_management_views.py`, `utils/cases_views.py`, `utils/tech_logger.py`
- `staff/commands/mod/case/_shared.py`
- 8 × `modules/configs/*_config.py` (pass `actor_id` to `save_module_config`)
- `locales/{fr,en-US,es-ES,pt-BR,de}.json` (`global_sanctions.*`,
  `staff.mod.case.no_source`)
- Docs: `CLAUDE.md`, `MODERATION_CASES.md`, `DATABASE.md`,
  `BACKEND-INTEGRATION.md`, `PREMIUM.md`, `AUTOMOD_AI.md`

## Decisions

- **No schema change.** The three levels reuse existing `sanction_action`
  values, and a guild subject reuses `subject_type = discord_guild`. Nothing
  was added to the DB.
- **Cache TTL bounded by expiry.** A 120 s TTL would let a lapsed temporary
  sanction linger; the entry now never outlives the sanction that produced it.
- **Fail open.** A DB error resolves to "no sanction" rather than locking
  everyone out of the bot.
- **"New module" = never configured.** A deleted config (`{}`) counts as new;
  anything already set up stays fully editable under a limitation.
- **The user's own level counts too.** A limited user cannot set up new modules
  anywhere, not just in a limited server. Config panels pass `actor_id`.
- **Full clear on staff add/revoke sanction.** Those flows only hold a case id,
  not the subject; staff sanctions are rare, so clearing the whole cache beats
  an extra query.

## Follow-ups

- The backend must publish `{"type": "refresh", "guild_id": ...}` on
  `moddy:blacklist:updates` for guild-level sanctions (user_id already
  supported). Documented in `GLOBAL_SANCTIONS.md` §6 and
  `BACKEND-INTEGRATION.md`.
- No DM is sent when a global sanction is issued or lifted; the subject only
  finds out on their next interaction. Worth adding if the team wants it.
- `services/case_service.manual_sources_for()` is now unused
  (`utils/case_management_views._manual_sources` covers the same need with the
  `requires_scope_id` filter). Left in place as a public helper.
