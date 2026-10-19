# Inspection tool — `/sauce`

Part of the utility tools family (design contract: [TOOLS.md](TOOLS.md)).

| | |
|---|---|
| Cog | `cogs/tools_inspect.py` |
| Cards | `utils/tools_inspect_views.py` (link buttons only: nothing to persist) |
| Pure logic | `tools/sauce.py` (parsers, engine URLs) |
| Service | `services/sauce_service.py` (gateway orchestration) |
| Gateway providers | `saucenao`, `tracemoe` (`gateway/adapters/`, `gateway/clients/`) |
| Tests | `tests/test_tools_inspect.py` |

> `/exif` (file metadata) and the **Find source** message menu were removed.
> Discord caps an app at five message context menus and they are all taken
> (see [VOICE_TRANSCRIPTION.md](VOICE_TRANSCRIPTION.md)): `/sauce` takes an
> attachment or a link to a message of this server instead.

---

## `/sauce [image] [message]`

Two ways in, one pipeline (`services/sauce_service.py`):

* `image:` attachment (PNG, JPEG, WebP, GIF, 20 MB max);
* `message:` link to a message of **this server** — only read when the requester
  can see the channel and its history (same guard as `/ai-check`);

Visibility: `incognito` option (user preference, ephemeral by default). A public card speaks the server language and shows
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

**Without `SAUCENAO_API_KEY` only anime screenshots can be recognised** (trace.moe):
any artwork or photo then ends on "no reliable source". The empty card says so
(`tools.sauce.nothing_body_anime_only`) when SauceNAO was not asked, so it is not
mistaken for a real "no match". If every search comes back empty, check that the
variable is set on Railway.

---

## i18n

`tools.sauce.*` (title, anime/best/also blocks, `confidence.*`, `errors.*`) in the
5 runtime locales. Command: `sauce` in the 32 files of `locales/commands/`.

## Stats

`sauce.used{source: slash|link, found}`.

## Known limitations

* trace.moe and SauceNAO fetch the image URL themselves: Discord CDN URLs are
  signed and expire, so a search on an old attachment URL can fail (the
  upload fallback covers attachments of the current command only).
* Google Lens has no stable URL scheme; the button may land on a Lens upload
  page rather than results. Links longer than 512 characters are dropped.
