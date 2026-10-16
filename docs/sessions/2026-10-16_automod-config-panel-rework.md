# Automod /config panel rework

## What was done
- Renamed "Automod AI" -> "Automod" in every user string (5 locales, `MODULE_NAME`, `modules.automod_ai.name` for the breadcrumb); ids unchanged.
- Removed "AI guidance" (screen, view, modal, nav option, locale keys, tests, docs, `validate_rules` stub).
- Generic module state replaces `enabled` / `dry_run` (`SUPPORTS_SIMULATION`, `effective_state`, `dry_run` is now `is_simulation`); legacy flags migrated on load, dropped on next save. Turn on/off button and simulation toggle removed; home uses `module_state_row`.
- Wizard: Skip on every step = Balanced level + simulation.
- Home: fine-tune select and red "Delete configuration" (confirmation view) outside the container.
- Main sub-views use drafts + Save/Cancel (`SectionView`, `load_working`/`stash`); the infraction screen and the custom-infraction editor edit the `sanctions` draft.
- Sanctions: effective values, bullet list, multi-action select with conflict explanation, timeout Modal V2, approval and escalation toggles.

## Decisions
- Drafts are in memory (guild, user, section) with a 30 min TTL: unsaved edits are lost on restart, everything else survives. A draft stores only the keys its section owns and is merged onto the fresh stored config on Save.
- Picking actions switches the entry to `mode: "perso"` (one plan for all severities); Reset returns to the ladder.

## Follow-ups
- `ModuleManager.get_module_state/statuses` do not know the legacy flags: a not-yet-resaved legacy config shows as active on the /config main list.
