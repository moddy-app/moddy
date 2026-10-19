# Media tools — `/image` and `/convert`

Part of the utility tools family ([TOOLS.md](TOOLS.md) is the shared design
contract). Two global, user-installable commands, answered **incognito** by
default (`incognito` option).

| Piece | File |
|---|---|
| Commands | `cogs/tools_media.py` |
| Cards, modals, persistent components, orchestration | `utils/tools_media_views.py` |
| Engine room (downloads, CPU pool, CloudConvert path) | `services/media_tools_service.py` |
| Image operations (Pillow) | `tools/image_ops.py` |
| Background removal (u2netp) | `tools/bg_removal.py` |
| Format table, detection, converters | `tools/convert/{formats,detect,local,media}.py` |
| CloudConvert provider | `gateway/adapters/cloudconvert.py`, `gateway/clients/conversion.py` |
| Tests | `tests/test_tools_media.py` |

---

## `/image file`

Shows the picture (preview), its **format, dimensions, weight and transparency**
(plus the frame count of an animation), and four buttons:

| Button | What it does |
|---|---|
| **Resize** | Modal V2: width, height, percentage, and a `RadioGroup` for how to fit when both sides are given (*fit inside*, *exact size*, *fill and crop*). A percentage wins over width/height. |
| **Compress** | Modal V2 with a `RadioGroup`: light / balanced / strong. Returns the original untouched when nothing can be gained, and suggests WebP when it would shrink a PNG a lot. |
| **Convert** | Opens the same format picker as `/convert`, restricted to image formats; the result comes back as a full `/image` card. |
| **Remove background** | u2netp salient-object cut-out, returned as a transparent PNG. |

After an action the card shows **before → after** (format, dimensions, weight
with the percentage saved) and keeps the same four buttons, so actions chain.
GIF and animated WebP/PNG stay animated through resize/compress/convert
(capped at 300 frames and 40 M frame-pixels).

Formats: PNG, JPEG, WebP, GIF, BMP, TIFF, ICO, AVIF, HEIC (iPhone, via
`pillow-heif`). Formats Discord cannot display (HEIC, AVIF, BMP, TIFF, ICO) get
a PNG preview; the original rides along as a file component.

### Stateless buttons

The card **always carries the image as an attachment**; each button re-reads it
from `interaction.message.attachments` (the preview, named `moddy-preview.png`,
is skipped). When that message shows no image, the card is fetched back through
the interaction token (`interaction.original_response()`, works for ephemeral
cards too) and the miss is logged with the attachment names seen. The Resize /
Compress modals keep the message the button was clicked on (a modal submit does
not reliably repeat its attachments). Nothing is stored anywhere, so the buttons
survive restarts and never expire. Custom ids: `moddy:tools:image:{resize,compress,convert,bg}`.
Modals are the documented persistence exclusion (they live only for the
interaction that opened them).

* **Ephemeral card** (default): the click edits the card in place.
* **Public card**: the person who ran the command (`interaction_metadata`) edits
  it in place; anyone else gets a private answer.
* A failed action restores the card and sends the error as a private follow-up,
  so the user never loses their image.
* The attachment must fit the **upload limit of the place** (`interaction.filesize_limit`:
  10 MB in DMs and most servers, more on boosted ones). A source above it is only
  previewed, with the buttons removed and a hint to send a lighter copy
  (limitation, see below). A result above it is refused with `result_too_large`.

### Safety

* The pixel count is checked from the **header, before any decoding**
  (`MAX_PIXELS` = 80 MP) and animations have a frame/pixel budget — the same
  philosophy as `automod/image_hash.py`.
* Everything is re-encoded from decoded pixels: EXIF (GPS included) never
  survives, orientation is baked in.
* Pillow runs in `asyncio.to_thread` behind a semaphore of 3.

---

## Background removal (`tools/bg_removal.py`)

The **u2netp** model (4.6 MB, the small U²-Net of rembg) is run **directly with
`onnxruntime` + `numpy`**; the `rembg` package is not installed (it pulls scipy,
scikit-image, pymatting and numba).

* **Download once**: `https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2netp.onnx`
  into `U2NETP_DIR` (default `<tmp>/moddy-models`; on Railway the disk is
  ephemeral, so it is fetched once per deploy), through `bot.public_api`.
  The file is **sha256-verified** (`309c8469258dda742793dce0ebea8e6dd393174f89934733ecc8b14c76f4ddd8`)
  before use, written atomically and re-checked on every load. The hash was
  computed from the official release asset in the build sandbox; its md5
  (`8e83ca70e441ab06c318d82300c84806`) is the one rembg publishes. Both URL and
  hash are overridable: `U2NETP_URL`, `U2NETP_SHA256`.
* **Lazy**: nothing is imported until the first removal.
* **One inference at a time** (an `asyncio.Lock`), always in a worker thread;
  the ONNX session uses 2 intra-op threads and no memory arena.
* **Unloaded after 10 minutes idle** (`IDLE_SECONDS`): the session is dropped,
  `gc.collect()` and `malloc_trim` hand the memory back.
* Pipeline: resize to 320x320, rembg's normalisation, mask min-max normalised,
  resized back to the original size with Lanczos, used as the alpha channel
  (multiplied with an existing alpha). A mask with no subject (< 0.5 % of the
  picture) is an error, not an empty image.

**Quality** is honest on the card: fine for a clearly separated subject, only
approximate on fine hair, glass and busy backgrounds. An animation loses all
frames but the first.

**Memory measured in the build sandbox** (Linux, CPython 3.13, onnxruntime 1.29,
RSS):

| Moment | RSS |
|---|---|
| Python + Pillow imported (what an idle bot costs for this feature) | 29 MB |
| + numpy and onnxruntime imported (first removal starts) | 116 MB |
| After a 12-megapixel removal (peak) | 176 MB |
| After the 10-minute idle unload | 124 MB |

So a removal costs about +60 MB transient on top of the +87 MB of the imported
libraries; the unload returns the session and tensors (-52 MB) but **the
onnxruntime library itself stays mapped** (~+95 MB over baseline). If that
residue matters on Railway, the next step is to run inference in a short-lived
child process that exits when idle (not done: more moving parts).

---

## `/convert file [format]`

Detects what the file **really is** (magic bytes first, then structure — a
`.docx` is a zip with `word/` —, then the extension, then the MIME type) and
offers a **select of the formats it can become**, one select per kind
(*Images, Data, Spreadsheets, Documents, Ebooks, Archives, Audio, Video*).
`format` (autocomplete) skips the picker; an impossible target answers with the
list of possible ones. `tools/convert/formats.py` is the single source of truth
(matrix, caps); the Select options, the direct check, the dispatcher and the
tests all read it.

| Kind | Conversions | Library |
|---|---|---|
| Images | PNG JPEG WebP GIF BMP TIFF ICO AVIF -> each other and -> PDF; HEIC is read-only (source) | Pillow + pillow-heif |
| Data | JSON YAML TOML XML CSV <-> each other | `json`, PyYAML (`safe_load`), `tomllib` + tomli-w, xmltodict |
| Spreadsheets | CSV XLSX ODS (+ JSON <-> sheets) | openpyxl; ODS with the standard library |
| Documents | Markdown <-> HTML, HTML/Markdown -> text, DOCX -> text / Markdown / HTML, TXT re-encoded to UTF-8 | markdown, markdownify, mammoth |
| PDF | -> PNG / JPEG (<= 10 pages, a zip when several), -> text (<= 300 pages) | pypdfium2, pypdf |
| Archives | ZIP, TAR.GZ, 7Z <-> each other | zipfile, tarfile, py7zr |
| Audio | MP3 OGG Opus WAV FLAC M4A AAC <-> each other | ffmpeg (imageio-ffmpeg) |
| Video | MP4 WebM MOV MKV -> MP4 / WebM / GIF / audio; GIF -> MP4 / WebM | ffmpeg |
| **Cloud** | DOCX ODT PPTX XLSX ODS -> PDF; PDF -> DOCX / EPUB; EPUB MOBI AZW3 <-> each other and -> PDF | CloudConvert |

Details that matter:

* **Spreadsheets**: first sheet only (noted on the card); text that looks like a
  formula (`=1+1`) is written as text, never as a formula; at most 500 000 cells.
* **DOCX**: images are dropped (noted), the text and structure are kept.
* **Archives**: member names are sanitised (`..`, absolute paths, symlinks and
  devices are skipped and noted); at most 1 000 files, 100 MB unpacked, and an
  extreme compression ratio is refused (zip-bomb guard); encrypted archives are
  reported as `protected`.
* **PDF -> text** on a scan has no text: the error points to `/ocr`.
* **ffmpeg** (the binary ships in the `imageio-ffmpeg` wheel; 0 RAM when idle):
  **one conversion at a time**, 180 s timeout, 2 threads, output <= 64 MB,
  duration caps (audio 10 min, video 60 s, video -> GIF 20 s). The input
  demuxer is **forced** from the sniffed format and `-protocol_whitelist file`
  is set, so a crafted playlist cannot read other files or the network.
* Caps: input 25 MB (text 5 MB), checked before and after the download.
* The result is re-uploaded; above the upload limit of the place, a local
  conversion fails with `result_too_large` while a cloud conversion hands a
  **24-hour download link** instead.

### CloudConvert (only layout office documents and ebooks)

Gateway provider `cloudconvert` (adapter + client, see `docs/infra/API_GATEWAY.md`).
Everything with a light local library is converted on Moddy; CloudConvert is the
only path that sends a file elsewhere, and the card says so
(`Converted by CloudConvert`; cards computed on Moddy show no source line).

* Job = `import/url` (CloudConvert downloads the file from the Discord CDN URL
  stored in the card state) -> `convert` -> `export/url` (link valid 24 h),
  created then polled every 1.5 s, hard timeout 120 s (`GATEWAY_TIMEOUT_CONVERT`).
* The CDN URL travels in `CallSpec.metadata`, never in `payload` (which is
  forwarded to the staff webhook log); the export link must be on
  `*.cloudconvert.com` or the result is refused.
* **Hidden when `CLOUDCONVERT_API_KEY` is absent**: the formats disappear from
  the picker and the autocomplete, nothing is "broken". `CLOUDCONVERT_SANDBOX=true`
  targets the sandbox.
* **Quota**: call type `convert_cloud`, **5 per user per day** (guild 30, global
  unlimited), seeded in `db/base.py`. Local conversions are not metered.
* **Provider rate rules** (`gateway/config.py`): 10/min and `CLOUDCONVERT_PER_DAY`
  (default **25**, the free allowance; fail-closed so a Redis blip can never turn
  into a paid conversion). Raise it after buying credits (~8-9 USD per 500 minutes).

---

## State and persistence

| Component | custom_id | State |
|---|---|---|
| `/image` buttons | `moddy:tools:image:{resize,compress,convert,bg}` | none, the image is read back from the card |
| Format picker | `moddy:tools:convert:to:<token>:<group>` (`DynamicItem` Select) | token -> `{url, name, kind, size, origin, user}` via `save_state` (Redis, 24 h, LRU fallback) |

The picker needs the **file**, which cannot ride in a custom id: the state holds
the Discord CDN URL of the attachment (valid ~24 h, which is also the state's
TTL). After that the click answers *"This card has expired. Run /convert again"*.
`origin` tells the result where to go: from `/image` an image result comes back
as a full `/image` card (chainable), from `/convert` it is a conversion card.
Both are registered by `ToolsMediaPersistence` (`utils/tool_views_registry.py`).

## i18n

`tools.image.*` and `tools.convert.*` in `fr`, `en-US`, `es-ES`, `pt-BR`, `de`
(errors under `.errors.<code>`, card notes under `.notes.<key>`); the 32
`locales/commands/*.json` carry `image` and `convert` (name, description,
parameters `file` and `format`). `tests/test_tools_media.py` asserts key and
placeholder parity across the five locales and that **every `MediaError` code
raised in the code has a message**.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `CLOUDCONVERT_API_KEY` | unset | enables office documents + ebooks (v2 key, `task.read` + `task.write`) |
| `CLOUDCONVERT_SANDBOX` | `false` | use the sandbox API |
| `CLOUDCONVERT_PER_MINUTE` / `CLOUDCONVERT_PER_DAY` | 10 / 25 | provider-account rules |
| `GATEWAY_TIMEOUT_CONVERT` | 120 | seconds for a cloud job |
| `U2NETP_DIR` | `<tmp>/moddy-models` | where the model file is kept |
| `U2NETP_URL` / `U2NETP_SHA256` | official rembg release | override if the model is re-hosted |

Dependencies are listed with a comment each in `requirements.txt` (all imported
lazily). Icons used (still empty constants, awaiting artwork): `ASPECT_RATIO`,
`COMPRESS`, `TRANSFORM`, `AUTO_FIX_HIGH`, `CLOUDCONVERT`.

## Known limitations

* A source image **above the upload limit** of the place cannot be re-attached,
  so its `/image` card has no buttons (the preview is shown).
* The picker state lives 24 h (CDN URL lifetime); `/image` cards never expire.
* Background removal: approximate on hair/glass/busy backgrounds; an animation
  keeps only its first frame; onnxruntime stays mapped (~95 MB) after the
  session unloads.
* Spreadsheets: first sheet only, no formulas/styles are carried over; CSV
  values are text (no typing). DOCX: images dropped. No layout-faithful office
  conversion without CloudConvert.
* Video/GIF are capped at 60 s / 20 s and one conversion runs at a time, so a
  queue forms under load.
* The CloudConvert flow was written against the v2 API documentation and
  verified with fakes only (no key in the build sandbox); try it once with the
  sandbox (`CLOUDCONVERT_SANDBOX=true`) before relying on it.
* HEIC is read-only (iPhone photos are converted *from* HEIC, never to it); AVIF
  needs Pillow's AVIF codec or the `pillow-heif` wheel, otherwise it is hidden.
