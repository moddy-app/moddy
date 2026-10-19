# AI Detector — C2PA Content Credentials

Moddy tells a channel where a posted image comes from, **only when it can prove
it**: the image carries a C2PA manifest signed by a certificate on the official
C2PA trust list (validation state `Trusted`). Unsigned Stable Diffusion /
ComfyUI parameters in a PNG get a softer "probably generated" card.

| Piece | File |
|---|---|
| Pure core (no Discord) | `aidetector/` — `formats.py`, `png_text.py`, `manifest.py`, `verify.py`, `analysis.py` |
| Service (`bot.ai_detector`) | `services/ai_detector_service.py` — trust list, downloads, concurrency, cache |
| Module (default-enabled) | `modules/ai_detector.py` |
| `/config` screen | `modules/configs/ai_detector_config.py` |
| Cards + persistent button | `utils/ai_detector_views.py` |
| `/ai-check` command | `cogs/ai_detector.py` |
| Tests | `tests/test_ai_detector.py`, fixtures in `tests/data/c2pa/` |

Library: **c2pa-python 0.38.0** (`requirements.txt`, pinned — it bundles the
native c2pa-rs 0.91 library in its wheel).

---

## 1. Flow

1. `cogs/module_events.on_message` → `AiDetectorModule.on_message` for every
   human message with attachments.
2. Candidates: PNG / JPEG / WebP attachments (declared type), at most 4 per
   message. Ignored channels (and their threads) and switched-off verdict kinds
   are filtered out.
3. `AiDetectorService.analyze_attachment` downloads **`attachment.url`** (the
   original file — `Attachment.read(use_cached=False)`). Never `proxy_url`:
   the media proxy re-encodes and drops the C2PA store. Files over 25 MB are
   skipped.
4. `aidetector.analysis.analyze` runs in `asyncio.to_thread`, under a semaphore
   of 3. Automatic analyses give up (`busy`) when 25 are already waiting.
5. Decision (cheapest first):
   - magic bytes must be PNG / JPEG / WebP;
   - a `c2pa` / `caBX` marker present → c2pa-python reads and verifies the store;
     **only `get_validation_state() == "Trusted"`** is classified;
   - otherwise a PNG may carry unsigned generation parameters → template 5.
6. A verdict → the module replies to the message (no ping) in the **server
   language**, with the "More information" button. No verdict → silence.

### Trust configuration (`aidetector/verify.py`)

```json
{
  "version": 1,
  "core":   {"allowed_network_hosts": []},
  "verify": {"verify_trust": true, "verify_after_reading": true,
             "remote_manifest_fetch": false, "ocsp_fetch": false},
  "trust":  {"trust_anchors": "<official PEM bundle>",
             "trust_config": "1.3.6.1.4.1.62558.2.1\n1.3.6.1.5.5.7.3.36\n1.3.6.1.5.5.7.3.4\n1.3.6.1.5.5.7.3.8\n"}
}
```

- `trust_anchors`: the official list,
  `https://raw.githubusercontent.com/c2pa-org/conformance-public/refs/heads/main/trust-list/C2PA-TRUST-LIST.pem`.
- `trust_config`: allowed EKUs — c2pa-kp-claimSigning, id-kp-documentSigning,
  id-kp-emailProtection, id-kp-timeStamping (one OID per line, the c2pa-rs format).
- **Network off while reading**: the file comes from a stranger; no remote
  manifest, no OCSP, empty host allow-list. A file can never make the bot fetch
  a URL it names.
- A fresh `Context` is built per read (`Context.from_json`) — cheap, and no
  native handle is shared across threads.

### Trust list lifecycle (`services/ai_detector_service.py`)

- Redis key **`moddy:ai_detector:trust_list`** (plain string, PEM), TTL **24 h**,
  shared by every bot process.
- At startup (`setup_hook`, background task — never delays startup) and then
  every hour: read Redis; adopt it if it differs from the loaded list; if the
  key expired, download it, write it back with `EX 86400`, load it.
- A failed download never unloads the list in use. While nothing is loaded at
  all (retry every 10 min), C2PA files report `trust_unavailable` and only PNG
  metadata can produce a verdict.
- Without Redis: one download a day per process.

## 2. Classification (`aidetector/manifest.py`)

The store is walked from the active manifest through its ingredients
(`parentOf` / `componentOf` only — an `inputTo` reference photo does not make a
generated image a photo). Each action keeps: action name, short
`digitalSourceType`, `when`, `softwareAgent` name.

| Kind | Rule | Template |
|---|---|---|
| `ai_full` | `trainedAlgorithmicMedia` on a `c2pa.created` action, no camera capture in the history | 1 |
| `ai_partial` | `compositeWithTrainedAlgorithmicMedia` anywhere, or `trainedAlgorithmicMedia` on a later action, or AI creation mixed with a capture | 2 |
| `photo` | `digitalCapture` (or `computationalCapture`, what phones like the Pixel declare) on the creation | 3 |
| `photo_edited` | same, plus non-AI retouch actions (`c2pa.edited`, `cropped`, `color_adjustments`, `resized`, `filtered`, `drawing`, `orientation`, `redacted`, `converted`, `transcoded`, `edited.metadata`) | 4 |
| `unsigned` | PNG text chunk `parameters` (Stable Diffusion) or `prompt` / `workflow` (ComfyUI) — no C2PA verdict | 5 |

Priority when a message holds several images: `ai_full` > `ai_partial` >
`unsigned` > `photo_edited` > `photo`.

Variables:

| Template var | Source |
|---|---|
| `{editeur}` | `signature_info.issuer` of the manifest holding the deciding action (fallback: active manifest) |
| `{outil}` | `softwareAgent.name` of the deciding action, else that manifest's `claim_generator_info[0].name` (or the legacy `claim_generator` string) |
| `{appareil}` | `stds.exif` / `c2pa.metadata` assertion: `Make` + `Model` (the model alone when it already starts with the make) |
| `{date}` | `when` of the deciding `c2pa.created` action, else the signature time — rendered as a Discord timestamp `<t:…:f>` (each reader sees it in their own language and time zone, as in the product mock-up) |
| `{actions}` | retouch action names translated (`ai_detector.actions.*`) |

French footers use `{of_issuer}` for the elision (`d'OpenAI`, `de Google`).

### Privacy

Nothing is copied out of the store wholesale. `manifest.py` reads an explicit
whitelist: issuer organisation, signature algorithm and time, generator name /
version, camera make / model, action names / dates / source types / software
names. **Never** GPS, author (`stds.schema-org.CreativeWork`, `tiff:Artist`),
CAWG identity assertions, serial numbers, the certificate common name, ingredient
titles. For PNG metadata the prompt is never returned — only the generator and
`Model`, `Sampler`, `Steps`, `CFG scale`, `Size`. Every displayed value is
stripped of markdown characters. Tests assert that GPS, author and serial
strings planted in signed fixtures never reach a card.

## 3. The cards (`utils/ai_detector_views.py`)

**Verdict card** — exactly the product mock-up: one container (accent
`0x0069FE` = 27134), a `TextDisplay` `### <:ai_shield:…> **Title**\nsentence`,
a visible small `Separator` (the template's `---`), a `TextDisplay`
`-# footer [**En savoir plus**](https://moddy.app/ai-detector)`. Below,
**outside** the container, the secondary button "More information".

**More information** — `AiDetectorDetailsButton`, a `DynamicItem`
`moddy:aidet:info:<channel_id>:<message_id>:<attachment_id>`, registered by
`AiDetectorPersistence` (`utils/persistent_views.py`). Public (anyone reading
the card may click), answered **ephemerally in the clicker's language**. The
click must come from the channel the id names. Details come from the service
cache (1 h, 512 entries) or a fresh analysis of the original attachment; a
deleted image answers "no longer available". The panel shows:

- result + source (verified C2PA signature / unsigned metadata);
- signature: issuer, validation state, algorithm, signing time, tool, camera;
- signed history (oldest first, 10 lines max) and chain size;
- for PNG metadata: generator, fields found, technical settings;
- how it works, how far to trust it (C2PA vs unsigned, photo ≠ truth of the
  scene), and why no result proves nothing.

## 4. `/ai-check`

Global command (servers, DMs, user installs), ephemeral by default
(`incognito`). Options: `image` (attachment) or `message` (link to a message
**of this server**, read only if the requester can view the channel and read
its history — never a window into a hidden channel). Always answers: verdict
card + details, or "No verifiable information" with the reason (`no_data`,
`untrusted`, `invalid`, `unclassified`, `unsupported`, `trust_unavailable`,
`error`, `busy`) and the "how it works / reliability" container.

Localised names: `locales/commands/*.json` → `commands.ai-check` (32 locales).

## 5. Module & configuration

`AiDetectorModule` — `MODULE_ID = "ai_detector"`, **`DEFAULT_ENABLED = True`**
(see [MODULE_SYSTEM.md → 3 quinquies](../guides/MODULE_SYSTEM.md)). No
module-specific bot permission: the baseline (view, send, history, external
emojis) covers replying.

Stored config (`guilds.data.modules.ai_detector`), absent = defaults:

```json
{
  "categories": ["ai_full", "ai_partial", "photo", "unsigned"],
  "ignored_channel_ids": [],
  "state": "active"
}
```

- `categories`: verdict families announced (`photo` covers `photo` and
  `photo_edited`). Unknown values are dropped on load; `validate_config`
  rejects them.
- `ignored_channel_ids`: ≤ 25 channels; their threads are ignored too.
- `state`: generic module state — `paused` turns the module off in that server.

`/config` → *AI Detector*: a multi-select of announced results (ticked = on),
a channel select of ignored channels, Save / Cancel, and **Manage** (pause /
resume). Dashboard: an absent or `{}` config means "active with the defaults";
write `{"state": "paused"}` (plus the defaults) to switch it off.

## 6. Statistics

`ai_detector.verdict` (global, dims `kind`, `source` = `auto` | `command`) and
the generic `module.action` (`module=ai_detector`, `action=<kind>`) for automatic
replies.

## 7. Limits — honest version

- **No result proves nothing.** Screenshots, re-saves, most social networks
  and many messaging apps strip C2PA. Most AI images in the wild carry none.
- A `Trusted` result reflects what the signer **declared**; it cannot be forged
  without the signer's key, but the signer may be wrong or terse (some tools
  sign without a `digitalSourceType` → `unclassified`, silent).
- Unsigned PNG parameters can be added or removed by anyone (template 5 says so).
- Whether Discord keeps C2PA in every upload path (desktop, mobile, re-uploads)
  has not been verified for every client: the module reads the original
  `attachment.url`, which is the best Discord offers.
- Cost: every image posted in every server is downloaded (≤ 25 MB) — mostly a
  substring scan; the native reader only runs when a C2PA marker is present.

## 8. Tests

`tests/test_ai_detector.py` signs real images at test time (PNG, JPEG, WebP;
capture → retouch chains; generative fill) with the test-only certificate chain
in `tests/data/c2pa/` and validates them as `Trusted` against the matching test
root — the production code path with different anchors. It also checks that the
same files are **not** trusted with the official list snapshot, that tampering
breaks trust, the five templates word for word, the card layout against the
mock-up, privacy, i18n in 5 languages, the module, the default-enabled manager
paths and the service (Redis cache, downloads, caps).

**Real-world images**: drop them in `tests/data/c2pa/real/` and add the expected
kind to `real/expected.json`; they are verified offline against
`tests/data/c2pa/C2PA-TRUST-LIST.pem` (refresh that snapshot if a newer signer
is not on it).
