# Inspection tools — `/exif`, `/sauce`, "Find source"

Part of the utility tools family (design contract: [TOOLS.md](TOOLS.md)).

| | |
|---|---|
| Cog | `cogs/tools_inspect.py` |
| Cards + persistent view | `utils/tools_inspect_views.py` |
| Pure logic | `tools/metadata.py` (file metadata), `tools/sauce.py` (parsers, engine URLs) |
| Service | `services/sauce_service.py` (gateway orchestration) |
| Gateway providers | `saucenao`, `tracemoe` (`gateway/adapters/`, `gateway/clients/`) |
| Tests | `tests/test_tools_inspect.py` |

---

## `/exif file`

Always **ephemeral, no incognito option**: a photo can carry the GPS position of
someone's home, so the answer is never posted in a channel.

What is read (blocking work, in `asyncio.to_thread` behind a 2-slot semaphore;
the file is downloaded through the attachment, 25 MB maximum):

| Kind | Source | Library |
|---|---|---|
| Images (JPEG, PNG, WebP, TIFF, GIF, HEIC) | EXIF (camera, lens, exposure, dates, software, author), GPS, XMP (creator tool, edit history, digital source type), PNG text chunks (Stable Diffusion / ComfyUI parameters), JPEG comment, ICC profile, DPI | Pillow (+ `pillow-heif`, lazy) |
| PDF | Info dictionary (author, title, producer, dates, pages, encryption) | `pypdf` |
| Office / OpenDocument | `docProps/core.xml` / `app.xml` / `meta.xml` (author, last modified by, company, revision, editing time…) | `zipfile` + `xml.etree` |
| Audio / video | ID3, Vorbis comments, MP4 atoms, QuickTime `©xyz` GPS of phone videos | `mutagen` |

No pixel is decoded, so a decompression bomb costs nothing; XML documents that
declare a DTD / entities are refused.

### The card

```
### <frame_inspect> File metadata
`photo.jpg` · Image · JPEG · 2.1 MB
**This file contains a GPS position.** Remove it before sharing the original.   (accent: warning)
**<location_on> Location**   Coordinates · `48.856667, 2.294444`
**Author** …   **Camera** …   **Dates** …   **Software** …
-# Computed by Moddy itself, nothing is sent elsewhere
[ Open in OpenStreetMap ]  [ All tags ]
```

* Sections follow `tools.metadata.GROUP_ORDER` (location and author first: the
  sensitive ones). Fields are cut to ~2 700 characters / 40 lines with a
  "N more" hint.
* Hints (location, a person's name, "declares AI", "has Content Credentials —
  run `/ai-check`") come from the report flags.
* **Nothing found** (only bare image properties): neutral card, honest wording —
  Discord often strips metadata from images at upload, not always, and some
  files never had any.
* **Open in OpenStreetMap** is a link button (only when there are coordinates).
* **All tags** is a persistent `DynamicItem` (`moddy:tools:exif:raw:<token>`):
  the full dump is stored with `save_state` (Redis, 24 h; the ephemeral card
  cannot re-derive it) and sent back as `metadata-<name>.txt`. After expiry it
  answers with the shared "expired" card.
* Local only: footer `computed_locally`; nothing leaves the process.

Errors (`tools.exif.errors.*`): `unsupported`, `unreadable`, `download`,
`timeout`; `too_large` falls back to the shared string (25 MB).

---

## `/sauce [image] [message]` and the **Find source** message menu

Three ways in, one pipeline (`services/sauce_service.py`):

* `image:` attachment (PNG, JPEG, WebP, GIF, 20 MB max);
* `message:` link to a message of **this server** — only read when the requester
  can see the channel and its history (same guard as `/ai-check`);
* the **Find source** message context menu (installable on users, works in DMs;
  the message's first image attachment, else an embed image/thumbnail).

Visibility: `incognito` option (user preference, ephemeral by default); the menu
follows the same preference. A public card speaks the server language and shows
`requested by`. Errors are always private.

### Search

SauceNAO (illustrations, manga) and trace.moe (anime frame -> title, **episode**,
**timestamp**) are queried **in parallel** through the gateway, handed the image
**URL** (nothing is downloaded by Moddy; if a provider answers 400 "cannot fetch
the image" and the image is a Discord attachment, it is uploaded once instead).
One search counts once against the `sauce` quota whatever the number of
providers (**15 / day / user**, 150 / day / server, seeded in `db/base.py`).

Relevance thresholds: SauceNAO 60 %, trace.moe 87 %
(`tools/sauce.py`: `MIN_SIMILARITY`, `MIN_ANIME_SIMILARITY`). Adult-only SauceNAO
indexes and trace.moe adult titles are dropped unless the channel is
age-restricted (`is_nsfw_context`; DMs and user installs are never NSFW).

### The card

Best source first (anime block when the frame outranks SauceNAO), up to three
other matches as dim links, a quiet note when one provider failed, footer
`Found by <logo> SauceNAO & <logo> trace.moe`. Under it:

* link buttons to the result pages (and AniList for anime);
* **Google Lens / Yandex / Bing / TinEye** link buttons carrying the image URL
  (no public API: the user finishes the search in the browser). Always shown,
  also when nothing reliable was found.

Nothing interactive needs persistence here (link buttons only).

### Keys and degradation

| Variable | Effect |
|---|---|
| `SAUCENAO_API_KEY` | SauceNAO enabled (free key) |
| `TRACEMOE_API_KEY` | optional: without it trace.moe is limited per IP |

Neither provider configured -> "not available right now" card, before any
loading card. One configured -> the other is simply not asked. A provider that
fails never hides the other's answer. Quota used up -> `quota` card.

---

## i18n

`tools.exif.*` (title, hints, nothing found, `fields.*`, `groups.*`, `kinds.*`,
`errors.*`) and `tools.sauce.*` (title, anime/best/also blocks, `confidence.*`,
`errors.*`) in the 5 runtime locales. Commands: `exif`, `sauce` and the context
menu `Find source` in the 32 files of `locales/commands/`.

## Stats

`exif.used{kind}` and `sauce.used{source: slash|menu|link, found}`.

## Known limitations

* **Whether Discord strips metadata is not verified here.** The card says
  "often, not always"; a manual test with a geotagged JPEG sent normally and as
  a raw attachment should be done on Discord before tightening the wording.
* trace.moe and SauceNAO fetch the image URL themselves: Discord CDN URLs are
  signed and expire, so a search on an old attachment URL can fail (the
  upload fallback covers attachments of the current command only).
* Google Lens has no stable URL scheme; the button may land on a Lens upload
  page rather than results. Links longer than 512 characters are dropped.
* `/exif` reads the file Discord serves, which may already be recompressed.
