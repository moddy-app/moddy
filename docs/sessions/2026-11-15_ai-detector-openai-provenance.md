# 2026-11-15 — AI detector: OpenAI provenance check (SynthID)

## What was done
- Integrated OpenAI's Content Provenance API (`POST /v1/content_provenance_checks`)
  into the AI detector. It detects OpenAI's C2PA manifest **and** its invisible
  SynthID watermark, which survives screenshots / re-saves (where the local
  C2PA check goes silent).
- New verdict kind `ai_confirmed` (template 6), ranked right after `ai_full`,
  governed by the `ai_full` category.
- `/ai-check` always asks OpenAI when the local checks found nothing; the
  details button does on a cache miss; the module only in servers that opt in
  (`openai_check`, new select in `/config`), for images ≥ 256 px.
- Two local rate-limit budgets on the same OpenAI account
  (`content_provenance`, `content_provenance_auto`), env-configurable.

## Files
- New: `aidetector/provenance.py`, `gateway/clients/provenance.py`,
  `tests/test_ai_detector_openai.py`.
- Changed: `gateway/adapters/openai.py` (multipart op; session-wide
  `Content-Type` removed — `json=` sets it per request), `gateway/config.py`,
  `gateway/executor.py`, `gateway/__init__.py`, `aidetector/analysis.py`,
  `aidetector/manifest.py`, `services/ai_detector_service.py`,
  `modules/ai_detector.py`, `modules/configs/ai_detector_config.py`,
  `utils/ai_detector_views.py`, `cogs/ai_detector.py`, 5 locales,
  `tests/test_ai_detector.py`, docs (AI_DETECTOR §9, API_GATEWAY, RAILWAY, CLAUDE.md).

## Decisions
- **Automatic check is opt-in per server**: the module is default-enabled, and
  sending every image of every server to a third party by default is a privacy
  decision a server should make. OpenAI's limits are also "strict".
- Only asked when the local checks give no verdict: never pay for what C2PA proves.
- A 400 is returned as data (`rejected`) so a few odd files cannot trip the
  circuit breaker; failures read `unavailable` and never break the reply.
- Google / other providers: no public image-verification API found (see
  AI_DETECTOR §9), so nothing else was integrated.

## Follow-ups
- OpenAI publishes no rate-limit numbers or pricing: set the
  `OPENAI_PROVENANCE_*` env vars from the org's *Limits* page. A `404` means
  the org has no access yet.
- Pre-existing failure unrelated to this work:
  `tests/test_translate_button.py::TestDefaultEnabled::test_runs_where_nothing_is_stored_and_can_be_paused`.
