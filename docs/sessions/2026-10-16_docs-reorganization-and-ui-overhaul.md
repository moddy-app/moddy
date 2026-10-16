# Docs reorganization and UI overhaul (2026-10-16)

Summary of the whole day on branch `claude/sleepy-hamilton-u9g461`.

## What was done
- **Generic module state** (active / paused, plus simulation for Automod) with per-module command sync: commands of a module that is not active on a guild are not synced to it. `/config` shows a breadcrumb, an accent colour and a state row on every module screen; voice transcription and social notifications use the generic pause state.
- **Expected-error system**: `utils/errors.py` (`ModdyError` family, `build_error_card`, discord.py error mapping) rendered by the central handler with its own severity, accent and single icon. See `docs/guides/ERROR_HANDLING.md`.
- **Accent colours**: every Components V2 container outside and inside `/config` gets an accent (blurple default, green only for real success, yellow warning/simulation, red error/destructive, gray paused).
- **Command casing rule** across locales, enforced by a test.
- **Automod runtime**: multi-action plans (delete / warn / timeout / kick / ban), exact timeouts, moderator approval (`automod_pending_approvals`, persistent alert card), hierarchy protection, apply-then-verify-then-announce so announcements are truthful, "AI guidance" removed.
- **Automod `/config` rework**: wizard can be skipped (Balanced + simulation), section drafts with Save/Cancel, multi-action sanctions editor, exact-timeout Modal V2, delete-configuration flow; module renamed "Automod" in every user string (ids unchanged).
- **CLAUDE.md**: missing icons are reported instead of approximated.
- **Docs reorganization** (this session): flat `docs/` split into `guides/` (17), `features/` (19), `integration/` (8), `infra/` (6) next to `archive/`, `sessions/`, `public/`; moves done with `git mv`; `AUTOMOD_AI.md` -> `features/AUTOMOD.md`, `AUTOMOD_AI_CONFIG.md` -> `integration/AUTOMOD_CONFIG.md`; every reference in CLAUDE.md, docs, code comments and tests updated; new `docs/README.md`; CLAUDE.md Documentation Index regrouped by folder.

## Files modified (main areas)
`utils/errors.py`, `cogs/error_handler.py`, `modules/module_manager.py`, `modules/configs/*`, `automod/*`, `modules/automod_ai.py`, `utils/automod_approval_views.py`, `db/repositories/automod_approvals.py`, `locales/**`, `CLAUDE.md`, `docs/**`. Detail in the other `2026-10-16_*` logs.

## Decisions
- Folders by purpose (rules / feature / contract / infra) so a contributor finds a doc by asking "what do I need?"; `public/` untouched because external pages may link to it; no doc was deleted or merged (no true duplicates found; each pair of overview + `*_INTEGRATION` covers a different audience).
- Doc file names follow the feature name ("Automod"), not the old "AI" qualifier.

## Known issues and follow-ups
- `serverlogs/renderer.py`, `cogs/auto_restore_roles_commands.py`, `cogs/console_logger.py`, `cogs/dev_logger.py` and `utils/staff_logger.py` still use `discord.Embed`.
- A kick creates no case (no `SanctionAction.KICK`).
- Automod section drafts live in memory and are lost on restart (30 min TTL).

- Adaptive slowmode request from the owner is still pending.
- Icons wired: `labs` (simulation state), `pending` hourglass (automod moderator approval pending).
