# 2026-10-11 - Interaction guard + full error path for expired interactions

## Done
- Reverted the silencing of 10062/10008/10015: they go through the full error path again
  (code, Sentry, DB, error channel) via the new `report_interaction_error`
  (`cogs/error_handler.py`), used by `BaseView`, `BaseModal`, the app-command handler,
  `report_component_error` and `cogs/token_detector.py`. Details now carry the interaction
  age, loop lag, guard state and `user_reached`.
- Delivery chain (`deliver_view`): response -> followup -> edit original -> DM fallback through
  `bot.notifications` (1 DM / user / 5 min). Error cards localized in 5 locales; new
  `InfoNoticeView`; unknown commands now answer instead of staying silent.
- `utils/interaction_guard.py`: acknowledges any component / modal / command left unanswered
  ~2 s after the interaction snowflake, with `InteractionResponse` shims (send_message ->
  followup, edit_message -> edit_original_response, defer -> no-op, send_modal ->
  `InteractionTooSlow`). Unclaimed components get an "inactive" card. Throttled ops notice
  to the error channel on every activation.
- `utils/loop_monitor.py`: stalls > 2.5 s are reported to the error channel with the stack.
- Docs: `docs/ERROR_HANDLING.md` (new section), `CLAUDE.md` tree.
- Tests: `tests/test_interaction_guard.py` (+ updated `tests/test_interaction_latency.py`).

## Decisions
- Public command replies become ephemeral on the slow path (the guard defers ephemerally):
  never risk publishing ephemeral content.
- Components are deferred invisibly (update), not "thinking", so panel edits keep working.

## Follow-ups
- Watch the `InteractionGuard` notices in production: each one names a handler that should
  defer earlier itself.
