# Session: Automod images, Moddy team labeling queue, /ocr

**Date:** 2026-09-26
**Agent:** Claude Code

## Summary

The automod only ever read text: an image-only message was dropped before any
detector ran, which is exactly how crypto-scam raids (fake MrBeast "crypto
casino" + fake "Withdrawal Success! +5 600 USDT" screenshots) and NSFW images
get through. This session added:

1. **`image_scam`** — known perceptual hash → sanction on sight; otherwise free
   pre-rules decide whether to OCR (gpt-4.1-nano vision), and the OCR text goes
   through the normal text funnel with an `image_ocr` origin.
2. **`image_nsfw`** — Google SafeSearch behind a smoothed monthly budget (the
   free tier is 1000 calls/month for all of Moddy), newcomers first, a per-guild
   daily share and a per-hash cache.
3. **A Moddy team labeling queue** — every sanction and every doubt is copied to
   a team channel; labels teach the bot (hash DB, embedding references,
   blocklist terms, server precedents, eval corpus) and "not sanctionable" on a
   bot sanction revokes it. A label never applies a sanction.
4. **`/ocr`** (Google Vision DOCUMENT_TEXT_DETECTION) + the Transcribe menu on
   images.

## Changes Made

- `gateway/adapters/google_vision.py`, `gateway/clients/vision.py` — new provider.
- `gateway/ratelimit.py` — `RateRule(calendar="month", fail_closed=True)`.
- `gateway/adapters/openai.py`, `gateway/clients/ai.py` — `vision` operation
  (image on `CallSpec.binary`, never in the logged payload).
- `automod/image_hash.py`, `automod/scam_anchors.py`, `automod/image_policy.py` — pure core.
- `automod/{schemas,engine,nano,blocklist,embeddings,constants,bareme}.py` —
  content origin, scam anchors routing step, doubt detection, learned terms and
  references, `contenu_nsfw` category, origin-scoped verdict cache.
- `automod/data/references.json`, `automod/blocklist.py` — `arnaque_scam` coverage.
- `automod/eval/*` — 4 scam golden cases (one is a real noisy OCR sample).
- `modules/automod_ai.py` — `ImageScamFeature`, `ImageNsfwFeature`, judged-text
  helper, image on the alert card, cross-post deletion, labeling hooks.
- `modules/configs/automod_ai_config.py` — two new toggles, shared exemptions.
- `services/automod_image_service.py`, `services/automod_label_service.py`,
  `services/ocr_service.py` — new services (`bot.automod_images`,
  `bot.automod_labels`, `bot.ocr`).
- `utils/automod_label_views.py`, `utils/ocr_views.py`, `utils/sanction_reversal.py`.
- `db/base.py` + `db/repositories/automod_learning.py` — 4 new tables, quota seeds.
- `cogs/ocr.py`, `cogs/voice_transcription.py`, `staff/commands/mod/automod.py`.
- `stats/registry.py` — `automod.image`, `automod.label`, `ocr.used`.
- Locales (5) + `locales/commands/*.json` (32) for `/ocr`.
- Tests: `tests/automod/test_images_core.py`, `tests/test_automod_images.py`,
  `tests/test_ocr.py`, `tests/gateway/test_google_vision.py`, persistent views.
- Docs: AUTOMOD_AI (§1, §4.2, §4.3, §5, §6, §9), AUTOMOD_AI_CONFIG, API_GATEWAY,
  OCR (new), VOICE_TRANSCRIPTION, RAILWAY, STAFF_SYSTEM, CLAUDE.md.

## Decisions & Rationale

- **OCR of scam images on gpt-4.1-nano vision**, not a local engine: RAM is
  ~93 % of the Railway bill; Tesseract/EasyOCR would cost resident memory and a
  system package, and Tesseract's output is much noisier (the sample OCR in the
  request came out as "BANK CARO", "Mrseast"…). Cost ≈ $0.0003 per image.
- **Hashing with Pillow only** (DCT written out): imagehash pulls numpy/scipy.
- **`arnaque_scam` had no routing at all** (no blocklist, no references) — added,
  plus an OCR-tolerant anchor scorer that routes like a regex hit.
- **SafeSearch decides NSFW deterministically** (likelihood scale), no LLM.
- **Doubts are narrow** (grounding rejection, low-confidence sanction, refused
  heavy confirmation, cleared anchor hit, SafeSearch possible): the default
  "low" confidence of a cleared message would otherwise flood the queue.
- **Text duplicates are merged per server only** (one label revokes every
  merged sanction; the same words can be banter elsewhere). Images are global.
- **The Transcribe menu reads images**: Discord's 5 message context menus are taken.
- **Images are always spoilered** on the team card and the alert card; they are
  never stored in the DB (only as the card attachment).

## Known Issues / Follow-ups

- [ ] Set `GOOGLE_VISION_API_KEY` and `MODDY_AUTOMOD_LABEL_CHANNEL_ID` on Railway
      (create the team channel first).
- [ ] Privacy policy / ToS must disclose the image analysis (Google, OpenAI) and
      the copies sent to the Moddy team for labeling.
- [ ] Only attachments are analysed — not images in embeds / link previews.
- [ ] `scan_all` has no UI toggle yet (ops/backend-set).
- [ ] Measure SafeSearch false positives and the pre-rules' recall on real
      traffic, then tune `SCAM_RISK_THRESHOLD` / the anchors.
- [ ] A dashboard view of the labeling queue could replace the Discord channel later.
