# 2026-10-23 — Fixes from the production logs

## What was done

Went through a Railway log export (startup + ~12 min of runtime) and fixed
every problem that comes from the code:

- **OpenAI `gpt-4.1-nano` shut down (2026-10-23)** — `/fix`, the automod
  nano decider, image OCR, rules check and ban-reason suggestions all got a
  404. Replaced by `gpt-5.6-luna` (OpenAI's named replacement) everywhere.
  GPT-5-family models reject `max_tokens` and any non-default `temperature`,
  so `gateway/adapters/openai.py::_adapt_params` translates the body for
  reasoning models: `max_tokens` → `max_completion_tokens`, `temperature`
  dropped, `reasoning_effort="none"` unless set. Callers did not change.
  Rate-limit key and cost table updated ($0.20 / $1.20 per 1M).
- **Gateway log buffer spam without Redis** — `Failed to pop log buffer from
  Redis` every 5 s. `GatewayLogger` now buffers in memory (bounded deque,
  10 000 rows) when `redis` is `None`, so `api_calls` keeps being written.
- **`case_sync` crash** — `entry.target` raised `TypeError` on audit entries
  with no target id (channel actions). The listener now returns early for
  actions other than ban / unban / member_update and guards the access.
  Same guard in `serverlogs/audit.py::target_matcher`, which also read a
  non-existent `entry.target_id` (now `_target_id`).
- **Missing translations** `modules.{logs,tickets,social_notifications}.name`
  added to the 5 locales; the hard-coded fallback in
  `modules/configs/_common.py::crumb` removed.
- **Version fetch 404** — `fetch_version` read releases of the old
  `juthing/MODDY` repo. It now uses Railway's `RAILWAY_GIT_COMMIT_SHA`.

## Not fixed in code (configuration)

- Redis: `invalid username-password pair or user is disabled` → `REDIS_URL`
  credentials on Railway.
- Better Stack heartbeat HTTP 404 → `BETTERSTACK_HEARTBEAT_URL` points to a
  monitor that no longer exists.
- PyNaCl / davey warnings: voice is not used; harmless.

## Files

`gateway/adapters/openai.py`, `gateway/logger.py`, `gateway/config.py`,
`gateway/clients/ai.py`, `gateway/spec.py`, `gateway/__init__.py`,
`automod/constants.py`, `cogs/text_tools.py`, `cogs/moderation_commands.py`,
`cogs/case_sync.py`, `serverlogs/audit.py`, `services/ocr_service.py`,
`services/automod_image_service.py`, `db/base.py`, `bot.py`,
`modules/configs/_common.py`, `locales/*.json`, docs (AUTOMOD, OCR,
TEXT_TOOLS, API_GATEWAY), `tests/gateway/test_openai_and_logger.py`.

## Follow-ups

- `gpt-4.1-mini` and `gpt-4o-mini` shut down on **2027-01-14**
  (replacement `gpt-6-luna`).
- `tests/test_translate_button.py::TestDefaultEnabled` already fails on the
  base branch (module made opt-in in #426, test not updated).
- `OPENAI_NANO_RPM` default kept at 500 — raise it to the account tier's
  limit for `gpt-5.6-luna`.
