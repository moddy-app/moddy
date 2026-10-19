# Translate button (module `translate_button`)

When a member writes in a language the server does not speak, Moddy answers
with one dim line and a button:

```
-# 🇫🇷 French detected                          [ Translate ]
```

Whoever clicks gets the translation **privately** (ephemeral), in **their own**
language, with `Translated by <DeepL logo> **DeepL**` as the source phrase.

The module is **enabled by default** on every server (`DEFAULT_ENABLED`, like the
AI Detector) and can be paused from `/config` -> *Translate Button* -> **Manage**.
Design contract: [TOOLS.md](TOOLS.md). Module rules: [../guides/MODULE_SYSTEM.md](../guides/MODULE_SYSTEM.md).

## Files

| File | Role |
|---|---|
| `tools/langdetect.py` | Pure logic: cleaning, eligibility, the `py3langid` wrapper, the "known languages" rules |
| `services/translate_button_service.py` | `bot.translate_button`: lazy model load, learning counters (Redis), prompts + bursts, deletion timers, click translation (DeepL via the gateway) |
| `modules/translate_button.py` | `TranslateButtonModule`: config schema, `on_message` |
| `modules/configs/translate_button_config.py` | `/config` panel |
| `utils/translate_button_views.py` | Prompt view, `TranslateButton` DynamicItem, translation + error cards |
| `cogs/module_events.py` | Forwards `on_message` (after a cheap `len >= 20` test) |
| `cogs/config.py` | Opens the panel through `TranslateButtonConfigView.create` (async read of the learnt languages) |
| `stats/registry.py` | `translate_button.prompted` (dim `language`), `.clicked`, `.cache_hit` |
| `tests/test_translate_button.py` | Everything below |

## When does a message get a button?

All of these must hold, in this order (the cheapest checks first, so most
messages never reach the model):

1. Author is a member (not a bot, not a webhook), message type is `default` or
   `reply`, the channel (or the parent of a thread) is not ignored, the bot can
   send in the channel, and **DeepL is configured** (`gateway.deepl_available()`).
   Without a DeepL key the module is silent: a button that cannot work is never shown.
2. **Eligible text** (`ld.eligible_text`): after removing code blocks, inline
   code, links, mentions, timestamps, custom emojis and `||` spoiler marks, the
   text has **at least 4 words** and 20 letters (a character of an unspaced
   script — Japanese, Chinese, Thai — counts double), is at least 60 % letters,
   and has at least 3 distinct words/characters. The first 1000 characters are used.
3. **Confident detection** (`Detector.detect`): confidence **>= 0.90**
   (normalised probability); **>= 0.97** under 40 letters, because short text is
   ambiguous (`tu es trop forte` is French, Spanish or Italian). The detector is
   restricted to the 25 `SUPPORTED_LANGUAGES`; anything else, or a failure, is
   "no verdict" — never a wrong label.
4. The language is **not known** on this server (see below).

Every confident detection is **counted** (known languages included) so the
shares are representative; only step 4 decides about the button.

## Known languages (no button)

1. the **server language** (`guild_locale`, the single server setting);
2. the **usual languages**, learnt automatically: a language that makes up
   **>= 10 %** of the detected messages over a rolling **14 days**, once at least
   **200** messages were sampled (before that the rule is silent, so a quiet server
   does not conclude that its first foreign messages are "usual");
3. **extra languages** added by hand in `/config`.

Learning storage: one Redis hash per server per day
`moddy:trbtn:langs:<guild>:<YYYYMMDD>` (`HINCRBY` per language, TTL 15 days).
Counters are aggregated in memory and flushed in **one pipeline every 30 s**;
reads are cached 5 min per server. Without Redis the counters live in the
process (lost on restart). No message text is ever stored.

## Consecutive messages: one prompt

Same author, same channel, same language, previous message < **2 min** ago and
fewer than 10 messages covered: the existing prompt is **edited** (its button now
covers `first .. last`) instead of posting a new reply. The tracker is in memory
(lost on restart: the next message just opens a new prompt). If the prompt was
deleted meanwhile, a fresh one is posted. The prompt is a **silent reply**
(`mention_author=False`, no mentions allowed) read by the whole channel, so it uses the
**server language**.

The prompt is deleted by the bot after the configured delay (2, 5, **10**, 30
minutes, or never). Timers are in-process: a prompt posted before a restart stays.

## The click

`DynamicItem` `moddy:autotr:tr:<channel>:<first_id>:<last_id>` — no stored state,
it survives restarts. The callback (wrapped with `guarded`) re-derives
everything from the interaction:

1. refuses (ephemeral error card) if the click is in another channel than the
   one encoded, DeepL is missing, or the user exceeded **20 clicks/minute**;
2. answers a loading card, fetches the first message and, for a burst, scans up
   to 100 following messages for the same author's eligible messages up to `last_id`
   (max 10);
3. translates each message (max 1500 chars each, 3200 in total) into the
   **clicker's** language: the `/preferences` forced language, else the Discord
   client locale (any DeepL target, not only Moddy's five), through
   `bot.gateway.translation` (per-user `translation` quota);
4. caches per message id + edit time + target language in Redis for 24 h
   (`moddy:trbtn:tr:...`) — a second click, or another person with the same
   language, costs nothing; an edited message is translated again;
5. edits the loading card into the translation card (mentions neutralised before
   DeepL, `NO_MENTIONS` on the way out).

Error codes (`modules.translate_button.errors.*`): `failed`, `unavailable`,
`rate_limited`, `quota`, `gone` (original deleted), `empty`.

## `/config` panel

Stored in `guilds.data.modules.translate_button` (absent or `{}` = defaults, active):

```json
{
  "ignored_channel_ids": ["123"],      // max 25; a thread follows its parent
  "extra_languages": ["en", "ja"],     // subset of SUPPORTED_LANGUAGES
  "delete_after": 600,                 // 120 | 300 | 600 | 1800 | 0 (never)
  "state": "active"                    // generic module state (Manage)
}
```

Screen: learnt languages (**read-only text**, since no control shows them — rule 9),
extra-languages select (with flags, max 25), lifetime select, ignored channels
select, then Back / Save / Cancel / Manage. Persistent, `Manage Server` re-checked
on every click. There is **no language dropdown for the prompt itself**: it uses
the server language. `REQUIRED_BOT_PERMISSIONS = []` (baseline only).

## Memory (measured)

`py3langid` 0.4.0 (model 4.6 MB compressed, expanded in memory) plus numpy. It is
imported and built **lazily**, in a worker thread, the first time any server
produces an eligible message, and never unloaded (the model is shared by all
servers). `Detector._trim` drops the 97-language matrix that `set_languages`
keeps for a later reset (~27 MB), leaving only the 25 supported languages.

Resident set size, Python 3.13, Linux, fresh process each (`python -I`):

| Scenario | Before | After `load()` | Delta | After first classify |
|---|---|---|---|---|
| numpy **not** loaded yet, trimmed (shipped) | 13.1 MB | 79.1 MB | **+66.0 MB** | 79.6 MB |
| numpy **already loaded** (onnxruntime / other module), trimmed (shipped) | 26.3 MB | 79.1 MB | **+52.7 MB** | 79.5 MB |
| numpy not loaded, **not trimmed** | 13.0 MB | 106.1 MB | +93.1 MB | 106.6 MB |
| numpy already loaded, **not trimmed** | 26.5 MB | 106.3 MB | +79.8 MB | 106.7 MB |

- The first classification adds ~0.5 MB; 1000 more add nothing (79.6 MB).
- `VmHWM` (peak) during `load()` reaches ~134 MB (xz decompression + restriction)
  and falls back to ~80 MB. The Railway bill follows the peak, but only once, at
  the first eligible message after a restart.
- Classification takes ~0.05-0.1 ms, so it runs on the event loop; only `load()`
  (~1 s) goes through `asyncio.to_thread`.
- Rough guide for `RAILWAY.md`: **about +55 MB** (numpy already present because
  of onnxruntime for background removal) to **+65 MB** (numpy not otherwise loaded)
  resident for the whole life of the process after the first eligible message.

## Honest limitations

- Language identification on short or code-switched messages is imperfect; the
  thresholds trade recall for precision: some foreign messages get no button.
  Closely related pairs (Norwegian/Danish/Swedish, Spanish/Portuguese, Czech/Slovak)
  can be confused; Norwegian Bokmål/Nynorsk are folded into `no`.
- Only the 25 catalogue languages are detected; a message in another language is
  ignored (not mislabelled). Flags stand for a language, not a nation.
- Learning needs 200 detected messages in 14 days; a small server relies on the
  server language and the manual list.
- Burst tracking and deletion timers are in memory (restart = a new prompt, an
  old one is left in place). The translation itself always works after a restart.
- A burst translation only covers the same author's *eligible* messages found in
  the 100 messages after the first one.
- DeepL quota is per clicking user (`translation`); nothing is spent until a click.
- Messages in channels the bot cannot read, or older than the prompt's lifetime on
  a deleted original, answer `gone`.

## i18n

`modules.translate_button.*` in the five runtime locales: `name`, `description`,
`config.*` (title, description, `learnt.*`, `languages.*`, `delay.*` with
`options.<seconds>`, `channels.*`), `prompt.{detected,button}`, `loading`,
`card.{title,jump}`, `errors.*`, and `languages.<code>` for the 25 language
names. Parity and placeholders are checked by `tests/test_translate_button.py`.
