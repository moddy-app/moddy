# AI Detector (C2PA) module + /ai-check

## What was done
- New **AI Detector**: under every PNG / JPEG / WebP posted in a server, Moddy
  verifies the image's C2PA Content Credentials with c2pa-python 0.38 (in
  `asyncio.to_thread`) against the official C2PA trust list (Redis-cached 24 h,
  key `moddy:ai_detector:trust_list`) with the four allowed EKUs, and replies
  only when the state is `Trusted`: AI-generated, partially AI, authenticated
  photo, retouched photo. Unsigned Stable Diffusion (`parameters`) / ComfyUI
  (`prompt`, `workflow`) PNG chunks → "probably generated" card.
- Verdict card = the mock-up (container, title+sentence TextDisplay, visible
  Separator, `-#` footer; accent 27134), in the **server language**, with a
  persistent "More information" button outside the container
  (`moddy:aidet:info:<channel>:<message>:<attachment>`) opening an ephemeral
  details panel (signature, signed history, metadata, how it works, reliability,
  privacy note) in the clicker's language.
- `/ai-check` (global, incognito): an attachment or a link to a message of this
  server; always answers (verdict + details, or the reason there is none).
- **Default-enabled modules**: `ModuleBase.DEFAULT_ENABLED` + manager support
  (load, statuses, state, Manage panel, dashboard empty push, delete, limited
  sanction). `ai_detector` is the first. /config screen: announced verdict
  kinds (multi-select) + ignored channels; pause via Manage.
- Privacy: explicit whitelist in `aidetector/manifest.py`; GPS, author,
  identity, serials, certificate CN never leave it. Prompts never shown.

## Files
New: `aidetector/` (formats, png_text, manifest, verify, analysis),
`services/ai_detector_service.py`, `modules/ai_detector.py`,
`modules/configs/ai_detector_config.py`, `utils/ai_detector_views.py`,
`cogs/ai_detector.py`, `tests/test_ai_detector.py`, `tests/data/c2pa/*`,
`docs/features/AI_DETECTOR.md`.
Modified: `modules/module_manager.py`, `modules/configs/_common.py`,
`cogs/config.py`, `cogs/module_events.py`, `bot.py`, `stats/registry.py`,
`utils/emojis.py`, `utils/persistent_views.py`, `requirements.txt`, locales
(5 + 32 command files), `tests/test_persistent_views.py`, CLAUDE.md,
`docs/guides/MODULE_SYSTEM.md`, `docs/guides/EMOJIS.md`,
`docs/integration/REDIS_COMMUNICATION.md`.

## Decisions
- The request first said "disabled by default", then "enabled by default on
  every server, can be disabled or partially disabled in /config": the later
  instruction wins → `DEFAULT_ENABLED`, pause = off, verdict kinds + channels =
  partial.
- `{date}` is a Discord timestamp `<t:…:f>` (as in the mock-up): each reader
  sees it in their language/time zone, which a server-wide French date cannot.
- `computationalCapture` (Pixel and other phones) counts as a photo alongside
  `digitalCapture`; `inputTo` ingredients are not history.
- `{editeur}` = issuer of the manifest that holds the deciding action (the
  camera maker for a photo, the generator's company for an AI creation even if
  later re-saved by an editor), fallback the active manifest.
- Network fully off while reading untrusted files (no remote manifest, no OCSP,
  empty host allow-list).
- Tests sign real images at test time with c2pa-python's public test chain
  (validated as `Trusted` against the test root), plus a snapshot of the official
  trust list to check the test signer is NOT trusted in production settings.

## Follow-ups
- Real-world image(s) from the user → `tests/data/c2pa/real/` + `expected.json`.
- `ai_shield` has no `_dark` twin yet (light-theme users see the white icon in
  the ephemeral details / config screen).
- Not verified: that every Discord client upload path preserves C2PA.
