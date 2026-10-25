# 2026-10-25 — Announcement verification: read Components V2 text

## What was done
- The first real announcement (posted by a webhook as Components V2, `content`
  empty) was authenticated "without translation" and its copies got the
  reaction but no Translate button: the cog only read `message.content`.
- New `announcement_text()` reads `content`, else every `TextDisplay`, else
  embed title/description/fields. Used for the translation, the content hash,
  copies and edits.
- Edit detection now reads the full message of the update (`payload.message`)
  instead of `payload.data["content"]`, which is empty for V2 messages and
  would have withdrawn the authentication on any update.
- A copy with no readable text is still answered (IS_CROSSPOST + reference).

## Files
- `cogs/announcement_verification.py`, `tests/test_announcement_verification.py`,
  `docs/features/ANNOUNCEMENT_VERIFICATION.md`

## Follow-ups
- The already-published announcement `1563729198147698789` was backfilled with
  a one-off `/dev jsk` snippet (translate + send the buttons).
- Its stored verification hash is the hash of an empty text: a later update of
  that message will withdraw its authentication and re-post the button.
- Custom emoji tags inside the text go through DeepL as-is.
