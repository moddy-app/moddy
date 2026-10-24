# 2026-10-24 — /image buttons: "Invalid state (no ConnectionState provided)"

## What was done
- Error `4614E367`: clicking an `/image` button (e.g. *Remove background*) on a
  Components V2 card failed. The image is re-derived from the card's components
  (`CardImage`), whose `UnfurledMediaItem` carries no `ConnectionState`, so
  `Asset.read()` raised `DiscordException`.
- `MediaToolsService.read_attachment` now downloads card media through
  `read_url` (CDN-host check, size limit, public resolver) when the object is
  flagged `from_components`; real `discord.Attachment`s keep using `read()`.

## Files modified
- `services/media_tools_service.py`, `utils/tools_media_views.py`
- `tests/test_tools_media.py` (regression test with a real parsed V2 card)

## Decisions
- Kept the routing in the service rather than giving `CardImage` a client
  reference: the CDN download path (`read_url`) already exists there with its
  host whitelist and size cap.
