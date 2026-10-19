# Music links — the `music_links` module and `/music`

Someone posts a Spotify link; half the channel uses Deezer, Apple Music or
YouTube Music. Moddy answers under the message, silently, with one dim line and
link buttons to the same song elsewhere. `/music` does the same on demand
with a richer card (cover, album, year, duration).

| Piece | File |
|---|---|
| Pure core (no Discord, no network, no DB) | `tools/music.py` |
| Service (`bot.music_links`) | `services/music_links_service.py` |
| Module (default-enabled) | `modules/music_links.py` |
| `/config` screen | `modules/configs/music_links_config.py` |
| Cards | `utils/music_links_views.py` |
| `/music` command | `cogs/music_links.py` |
| Gateway wiring | `cogs/module_events.py` (`on_message`), `cogs/config.py` (`music_links` screen), `bot.py` (`self.music_links`) |
| Tests | `tests/test_music_links.py`, fixtures in `tests/data/music/` |

Design contract shared with the other utility tools: [TOOLS.md](TOOLS.md).

---

## 1. Why the resolution is home-made

- The **Odesli / song.link** public API answers `401 PUBLIC_API_ACCESS_DEPRECATED`.
- The **Spotify Web API** is closed to new apps.

So Moddy reads what is public and free, through `bot.public_api` (no key, no
dependency, no gateway provider):

1. **Spotify public page** — `open.spotify.com/<kind>/<id>`. Its Open Graph /
   `music:*` meta tags carry the title, artist, album, year, duration and cover.
   Parsed with the standard library (`html.parser`), head only, 2 MB cap. A
   non-browser User-Agent is required: a browser one receives the empty
   single-page-app shell.
2. **Deezer** — `api.deezer.com/search[/album|/artist]`, a plain
   `Artist Title` query; if nothing matches, a second `track:"…"` / `album:"…"`
   query whose artist is checked on the results.
3. **Apple Music** — iTunes Search API (`itunes.apple.com/search`,
   `entity=song|album|musicArtist`). About 20 requests/minute per IP: a local
   budget of 16/minute leaves Apple Music out of a result rather than getting
   the IP blocked.
4. **YouTube Music** — there is no free API, so the button is a **search link**
   (`music.youtube.com/search?q=Artist Title`). It opens a search, not the exact
   song, and the config screen says so.

## 2. Detection

`tools.music.find_links` (pure): `open.spotify.com/(embed/)?(intl-xx/)?(track|album|artist)/<22 chars>`,
the `user/<name>/` prefix, and the short links `spotify.link`, `spoti.fi`,
`spotify.app.link`. Playlists, episodes and shows have no equivalent elsewhere:
the module ignores them, `/music` explains it. At most 3 links per message,
no duplicates.

Short links are followed by `MusicLinksService.resolve_short`: HTTPS only,
**3 hops at most, and only through Spotify hosts** (`SHORT_LINK_HOSTS`), never
anywhere else; the result is cached 30 days.

## 3. Matching — "no link rather than a wrong link"

`tools.music.best_match` scores each candidate (track: title 55 %, artist 30 %,
duration 15 %) and requires title similarity ≥ 0.82, artist ≥ 0.6 and a total of
at least 0.72 (albums 0.78, artists an exact name, ties go to the artist with
the most fans).

- Marketing noise is ignored: `Remastered 2009`, `Radio Edit`, `feat. X`,
  `Deluxe Edition`, years.
- A **different version** is refused unless Spotify shows it too: `(Live)`,
  `(Pianoforte)`, `(Acoustic)`, `- Club Remix` carry a -0.35 penalty.
- **Imitations** (karaoke, tribute, "made famous by", cover, lullaby,
  instrumental) are refused unless Spotify's own item is one.
- Several artists on Spotify (`A, B`) match a candidate that credits only one
  of them; the reverse (`Rick Astley Tribute Band`) does not match `Rick Astley`.

Deezer's search for *Never Gonna Give You Up* returns Pianoforte, remixes,
karaoke and covers but not the original: the test suite keeps that real payload
as a fixture and asserts the answer is "no match".

## 4. The module

`DEFAULT_ENABLED = True` (like the AI Detector): active in every server that
never touched it; pausing is the generic state in `/config` → **Manage**.

- Stored config (`guilds.data.modules.music_links`, JSONB):
  `{"platforms": ["deezer", "apple_music", "youtube_music"], "ignored_channel_ids": [..], "state": "active|paused"}`.
  Absent or `{}` = all platforms, no ignored channel, active. An empty
  `platforms` list is treated as "all" (a module with no button is pointless).
  At most 25 ignored channels; a thread follows its parent channel.
- `on_message` does only cheap checks (module active, guild message, human
  author, not a webhook, a "spotify" hint in the text, channel not ignored, the
  bot may send) and hands the rest to `bot.music_links.spawn(...)`: **it never
  waits for the network**. `spawn` bounds the background work in time (40 s) and
  in number (40 pending; beyond that the reply is silently dropped).
- Silence is the default: a failed lookup posts nothing, and a reply whose only
  button would be the YouTube Music *search* is not worth posting
  (`worth_posting`) — unless that is the only platform the server enabled.
- The reply: a **Components V2** container with `-# ♪ **Title** — Artist`
  (album and artist links get a small tag) and the link buttons. Sent as a
  reply with `mention_author=False`, `silent=True` and
  `allowed_mentions=none`; titles are markdown-escaped and `@` mentions are
  broken. Language: the **server language** (`guild_locale`).
- `REQUIRED_BOT_PERMISSIONS = []`: the baseline (view, send, history, external
  emojis) is all it needs; it also checks `send_messages` in the channel.

`/config` → Music links: a multi-select of the platforms (the ticked options
are the current setting, so there is no "current value" line) and a channel
select for ignored channels, with the usual Save / Cancel draft and **Manage**.
The view is persistent (`__persistent__`, custom_ids `moddy:music_links:config:*`,
auth = Manage Server re-checked on every click) and registered through
`utils/music_links_views.PERSISTENT_VIEWS`.

## 5. `/music <url>` (French: `/musique`)

Global, user-installable, works in DMs. Ephemeral by default (`incognito`
option). The card has the cover, title, artist, album, year and duration, a
button for Spotify and every platform found, and the footer
`Found by <logos> **Spotify**, **Deezer**, **Apple Music**` (+ `requested by`
when public). Refusals are cheap and immediate (no loading card): not a Spotify
link, or a playlist / episode / show. Otherwise a loading card is shown, then
edited. When Deezer and Apple Music have no match the card says so and still
offers the YouTube Music search.

## 6. Cache, limits, resilience

| What | Where | TTL |
|---|---|---|
| Resolved item | Redis `moddy:music:v1:<kind>:<id>` (in-process LRU fallback, 512 entries) | 30 days |
| Partial result (a platform timed out or its budget was spent) | same key | 10 min |
| Spotify page not found / unreadable | `moddy:music:v1:fail:<kind>:<id>` | 1 h (network blips 2 min) |
| Short link target | `moddy:music:v1:short:<hash>` | 30 days |

- Concurrent requests for the same item share one resolution; at most 4
  resolutions run at once; a whole resolution has a 25 s deadline.
- Every external call is bounded (`SPOTIFY_TIMEOUT` 10 s, `SEARCH_TIMEOUT` 8 s).
- A platform that errors is left out of the result and the result is cached
  only briefly, so it is asked again soon.
- Cache entries are validated on read (`MusicInfo.from_dict`): unknown fields
  and non-HTTPS or foreign link values are dropped.
- Returned URLs are rebuilt on the expected host (`www.deezer.com`,
  `music.apple.com`) with tracking parameters removed; the cover must come from
  a Spotify CDN.

## 7. Persistence

The reply and the command card contain **link buttons only**, which need no
persistence. The only persistent view is the `/config` screen (see §4).
`DYNAMIC_ITEM_CASES` is empty.

## 8. Statistics

Declared in `stats/registry.py` (global, no migration needed):

| Metric | Dimensions |
|---|---|
| `music_links.resolved` | `kind` (track / album / artist), `source` (auto / command) |
| `music_links.posted` | `kind` |
| `music_links.failed` | `reason` (not_found / unavailable / unsupported), `source` |

The module also calls `self.count("posted")` (per-server usage).

## 9. i18n

`modules.music_links.*` (name, description, `config.*`, `platforms.*`,
`reply.*`) and `tools.music.*` (`title.*`, `fields.*`, `no_match`,
`errors.{not_spotify,unsupported,not_found,unavailable}`) in the 5 runtime
locales; the command (`music`, `music link`, parameter `url`) in all 32
`locales/commands/*.json`. Platform names are brand names and stay untranslated.

## 10. Icons

Constants in `utils/emojis.py` (empty until the artwork is uploaded, the
helpers drop them): `MUSIC_NOTE` (module and card heading), `SPOTIFY`,
`DEEZER`, `APPLE_MUSIC`, `YOUTUBE_MUSIC` (buttons and the source line).

## 11. Known limitations

- **Spotify may block Railway's IP** (403 / 429 on the public page). The module
  then stays silent and `/music` shows "I cannot read the Spotify page
  right now"; failures are cached for an hour so the IP is not hammered. This
  cannot be fully verified outside production.
- The Spotify markup is not a contract: if the `og:` / `music:` tags change, the
  parser returns "unreadable" instead of guessing.
- YouTube Music is a search, never the exact song.
- Playlists, episodes and podcasts are not supported.
- Deezer and iTunes catalogues differ by country; a song absent from their
  public search simply gets no button.
- iTunes' 20 requests/minute is per IP: under heavy traffic Apple Music is
  skipped for some links (they are cached for 10 minutes only, then retried).
- The matching is deliberately strict; a rare true match can be refused, never
  the reverse by design.
