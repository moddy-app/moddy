# Advanced server logs: documentation, emoji cleanup and embed size guard

Follow-up session on the advanced server logs system (previous commit
`7171dc2 "Add the advanced server logs system"`, branch
`claude/advanced-logging-system-jc4mjs`). The system itself — 163 events over
18 categories, `serverlogs/`, `modules/logs.py`, `modules/configs/logs_config.py`,
`cogs/logs.py` — already existed and was not reworked. This session closed the
documentation gap and the three loose ends left behind in the code.

## What was done

### Documentation (the standing CLAUDE.md rule)

- **`docs/LOGS.md` (new)** — the reference for the whole system: what a server
  sees, the layer-by-layer architecture and who is allowed to decide what, the
  18 categories, the `/config` panel, the **exact stored schema** of
  `guilds.data.modules.logs` with the backend/dashboard contract, how to add an
  event (registry + 2 i18n keys + 1 builder) and how to change what a log says
  (locale JSON only), webhook delivery and behaviour under a raid, audit-log
  correlation, and a *Known gaps* section that names every deliberate hole
  rather than leaving it to be rediscovered.
- **`CLAUDE.md`** — `serverlogs/` added to the project tree (with its six
  modules and the `listeners/` package), plus `cogs/logs.py`,
  `modules/logs.py`, `modules/configs/logs_config.py`, `tests/test_logs.py`
  and `tests/test_logs_i18n.py`; `docs/LOGS.md` added to the documentation
  index.
- **`docs/PERSISTENT_VIEWS.md` → "Deliberate exclusions"** — `LogsCategoryView`
  documented with its three `DynamicItem` templates, why the wrapper has
  nothing to register, and why that is also the reason the category screen
  applies changes immediately instead of behind a Save button.

### Code

- **`serverlogs/registry.py`** — the 18 category emojis were hardcoded strings;
  they now come from `utils/emojis.py` (CLAUDE.md rule 3). `utils/emojis.py`
  has no imports of its own, so pulling it into a module that is imported
  during module discovery introduces no cycle. A new test asserts every
  category emoji is one of the constants, so a hardcoded one cannot come back.
- **`modules/logs.py` / `modules/configs/logs_config.py`** — the module icon was
  `HISTORY`, already used by `modules/auto_restore_roles.py`: two modules with
  the same icon in the `/config` picker. Switched to `NOTE`, which nothing else
  uses, and the root config panel follows.
- **`serverlogs/renderer.py`** — `MAX_EMBED_TOTAL` was declared and never used.
  It is now enforced by `LogEntry._fit()`: past the budget, trailing fields are
  dropped first, the description is shortened only if that is not enough, and
  the reader is told the log was shortened
  (`modules.logs.values.size_limit`, added to the five locales).

## Decisions made and why

- **Enforce `MAX_EMBED_TOTAL` rather than delete it.** Individual values were
  already capped, but Discord rejects an over-budget message *as a whole* — a
  permission diff spanning a dozen roles would simply never arrive, which is
  the one failure mode a log system cannot afford. Fields are dropped before
  the description because the description carries the facts and the fields
  carry the detail.
- **`on_external_config_change()` stays unimplemented** for the logs module,
  and `docs/LOGS.md` says so explicitly. The hook exists for modules whose
  configuration is *visible* in Discord (a panel to re-post, overwrites to
  re-apply); the logs module posts nothing and holds no Discord-side state, so
  the standard config reload is the whole of the change. Documented as a
  decision so it does not read as an oversight — and with the condition that
  would reverse it.
- **An unbound channel's `Moddy Logs` webhook is left in place**, and that is
  documented rather than automated. Moddy cannot distinguish its own leftover
  webhook from one an admin has since repurposed, and deleting a webhook is not
  reversible. The leftover is inert: it is never reused for another channel.
- **The `moderation` events with no source are kept in the registry.**
  `case_delete`, `mass_case_delete`, `kick_remove`, `report_create`,
  `reports_ignore`, `reports_accept`, `user_note_add`, `user_note_remove` are
  served by `log_case_event()` and called by nobody, because Moddy cannot
  delete a case and has no report system. They were left declared (so they
  light up the day the feature lands) and documented with their natural
  hook-ups — from `CaseService`, never from the DB repository, since
  `_log_to_server` is where the guild-scope check and the "a log never breaks
  the moderation action" guarantee live. `case_update` is half-wired already
  (the `restrict` / `revoke_access` sanctions reach it; a reason edit does not).

## Files modified

| File | Change |
|---|---|
| `docs/LOGS.md` | **new** — full system reference |
| `docs/sessions/2026-08-23_advanced-server-logs.md` | **new** — this file |
| `CLAUDE.md` | project tree + documentation index |
| `docs/PERSISTENT_VIEWS.md` | `LogsCategoryView` exclusion |
| `serverlogs/registry.py` | category emojis from `utils/emojis.py` |
| `serverlogs/renderer.py` | `LogEntry._fit()` enforces `MAX_EMBED_TOTAL` |
| `modules/logs.py` | `MODULE_EMOJI = NOTE` |
| `modules/configs/logs_config.py` | root panel icon follows |
| `locales/{fr,en-US,es-ES,pt-BR,de}.json` | `modules.logs.values.size_limit` |
| `tests/test_logs.py` | category-emoji test + two embed-budget tests |

## Known issues / follow-ups

- **Nothing has run against a real Discord server.** Still to check on a test
  guild: webhook creation and reuse, the `manage_webhooks` fallback, batching
  under a burst of deletions, the 1–2 s audit-correlation windows, and the
  rendering of at least one event per category. Listed in `docs/LOGS.md`.
- **`channel_voice_status_update`** is matched on the raw audit action id `192`
  (`cogs/logs.py::_VOICE_STATUS_ACTION`) — Discord ships no gateway event and
  discord.py has no enum member. Unconfirmed live; if the id is wrong the event
  is inert while the config panel still advertises it.
- **Premium is an open product decision.** Logs are not gated today; the
  natural knobs (`is_guild_premium`, number of bindable categories,
  `MAX_CHANNELS_PER_CATEGORY`) are documented, not chosen.
- **`MAX_CHANNELS_PER_CATEGORY = 3`, `MAX_IGNORED_CHANNELS/ROLES = 25`** remain
  working values rather than researched ones.

## Tests

`python3 -m pytest -q` → **1019 passed** (1016 before, +3 new in
`tests/test_logs.py`); `python3 -m pytest tests/test_persistent_views.py -q`
green.
