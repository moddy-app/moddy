# Moddy — OCR (`/ocr` + the Transcribe menu on images)

> Read this before touching `cogs/ocr.py`, `services/ocr_service.py` or
> `utils/ocr_views.py`. The automod's own image OCR is a different thing — see
> [AUTOMOD_AI.md §4.2](AUTOMOD_AI.md).

## What it does

| Surface | Where | Result |
|---|---|---|
| `/ocr image:<attachment> [incognito]` | everywhere (servers, DMs, user installs) | ephemeral by default (the requester's text) |
| **Transcribe** message context menu, on a message with an image and no audio | everywhere | public card (like a voice transcription), in the server language |

Discord caps apps at **five** message context menus and all five are taken
(`Save Message`, `Get Emojis`, `Translate`, `AI text tools`, `Transcribe`), so
`Transcribe` covers images too instead of a sixth "OCR" menu. Audio always wins
when a message carries both.

## Engine: Google Cloud Vision `DOCUMENT_TEXT_DETECTION`

Chosen over the automod's gpt-4.1-nano vision because a person asking for the
text wants it verbatim with its layout; dense-document OCR does that best.

- Through `bot.gateway.vision.document_text` (never a provider SDK), call type
  `ocr_command`.
- Images above ~7 MB are re-encoded (JPEG, ≤ 3000 px) first — Vision's JSON
  body caps near 10 MB once base64-encoded. Files above 25 MB are refused.
- The extracted text is shown in a code block (layout preserved); beyond
  3500 characters the card keeps the beginning and the full text is attached as
  `ocr.txt`. Nothing extracted can ping (`AllowedMentions.none()`).

## Limits

| Limit | Where | Default |
|---|---|---|
| Google free tier — **1000 images / calendar month for all of Moddy** | gateway rule `google_vision/document_text` `rpmo` (Pacific month, **fail-closed**) | `GOOGLE_VISION_OCR_MONTHLY=1000` |
| per user / day | `quota_limits` `ocr_command` user | 10 |
| per server / day | `quota_limits` `ocr_command` guild | 100 |

Past the monthly allowance every call is refused with a clear "monthly capacity
reached" message until the 1st. Raise the env var only if the Cloud billing
account accepts the extra cost (1,50 $ / 1000 after the free tier).

## Errors (`ocr.errors.<code>`)

`no_image`, `too_large`, `unavailable` (no `GOOGLE_VISION_API_KEY` / provider
down), `quota` (daily bucket), `monthly_cap`, `download_failed`, `unreadable`,
`empty`, `failed`.

## Stats

`ocr.used` (global, dimension `source` = `slash` | `context`).
