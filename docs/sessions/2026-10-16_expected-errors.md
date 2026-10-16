# Expected errors in the central handler

- Added `utils/errors.py` (ModdyError family, `build_error_card`, discord.py error mapping).
- `cogs/error_handler.py`: `report_expected_error`, wired into `report_interaction_error` and `on_command_error`; inline permission/cooldown/not-found views and embeds removed.
- `create_error_message` / `create_warning_message` delegate to the new builder.
- i18n: `errors.expected.*` in 5 locales. Tests: `tests/test_expected_errors.py`. Docs: ERROR_HANDLING.md.
- Migrated: `cogs/moderation_commands.py::_make_error_view`, `cogs/cases_user.py::_not_found`.
- Follow-up: `cogs/token_detector.py` alert cards (security-specific) and non-error embeds left as is.
