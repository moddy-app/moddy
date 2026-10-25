# 2026-10-25 — Announcement verification

## What was done
- New `cogs/announcement_verification.py`: every post in the followed
  announcement channel of the support server gets an **Authenticate** button
  (Managers/Developers only). Authenticating stores a hash of the text and
  translates it once (DeepL). Each copy Discord relays into a following server
  is answered with a confirmation card + **Translate** button (reader's
  language, stored translation).
- Copies arriving before authentication are recorded and answered afterwards;
  an edit withdraws the authentication and re-posts the button.

## Files
- `cogs/announcement_verification.py`, `db/repositories/announcement_verifications.py`,
  `db/base.py`, `config.py`, `utils/persistent_views.py`, `locales/*.json`,
  `tests/test_announcement_verification.py`,
  `docs/features/ANNOUNCEMENT_VERIFICATION.md`, `CLAUDE.md`

## Decisions
- A copy is recognised by `IS_CROSSPOST` + webhook + reference to the watched
  support-server channel: a webhook cannot set that flag itself.
- Authenticate does not publish the message; publication stays a manual step,
  and the copies table covers either order.
- Reused `_translate_all` from the announcement translation cog rather than a
  second translation path.

## Follow-ups
- Confirmations already posted are not removed if the announcement is edited.
- `tests/test_translate_button.py::TestDefaultEnabled` fails on `main` already
  (unrelated to this change).
