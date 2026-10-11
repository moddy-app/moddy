# 2026-10-11 - Unknown interaction (10062) / Unknown Message (10008) investigation

## Done
- Added `utils/loop_monitor.py` (heartbeat lag warning, watchdog thread dumping the blocked main-thread stack, `observe_interaction_age` from `bot.on_interaction`).
- Expired-interaction errors (10062/10008/10015) are expected: warning + counter + sampled Sentry, no error entry / ErrorView (`cogs/error_handler.py`, `cogs/token_detector.py`).
- Sanction check on the interaction path is bounded (1 s, fail-open, single-flight, stale "none" served) - `utils/global_sanctions.py`, `bot.py`.
- Default-incognito lookup cached + bounded (`utils/incognito.py`), used by all slash commands.
- Off-loop CPU: ticket transcript JSON+zstd(19) (`services/ticket_transcript_service.py`), embedding dot products (`automod/embeddings.py`).
- Ack-first in preferences, saved messages (library back), reminder delete, announcement translation buttons.
- Tests: `tests/test_interaction_latency.py`.

## Follow-ups
- Offenders in files owned by other agents are listed in the coordinator report.
- Read the new loop-lag / late-interaction warnings in production before deciding on a larger DB pool.
