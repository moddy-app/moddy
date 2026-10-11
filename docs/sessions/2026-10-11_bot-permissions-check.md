# Bot permissions check (2026-10-11)

- Root causes: baseline (send/view/history/emojis) never checked; several
  modules under-declared (interserver, logs, auto_restore_roles, automod
  attach_files); channel-level checks only ran at save time, one at a time.
- Added `utils/bot_permissions.py`, `ModuleBase.required_permissions()`,
  `channel_requirements()`, `SENDS_MESSAGES`, `BASELINE_OPTIONAL`;
  `check_bot_perms` now reports guild + channel gaps in one message.
- Save-time validators use `channel_validation_error` (all gaps at once).
- Tests: `tests/test_bot_permissions.py`.
