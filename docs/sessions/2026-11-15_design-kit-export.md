# 2026-11-15 — Design kit for another bot + emoji copy jsk

## What was done
- New self-contained pack `docs/export/design-kit/` (French, for the user's other
  bot): `README.md`, `DESIGN.md` (philosophy, card anatomy, colours, icons,
  config panels, errors, i18n, checklist, copyable `BaseView`/`BaseModal`/card
  helpers), `COMPONENTS_V2.md`, `MODALS_V2.md`, `PERSISTENT_VIEWS.md` — rewritten
  generic (no dependency on Moddy's code, `mybot:` custom_id prefix).
- `copy_emojis_jsk.py`: paste into `/dev jsk` on Moddy with the target bot's
  token. Collects Moddy's application emojis + every emoji referenced in code and
  locales (`minibots.emojis.moddy_emojis`) + `DARK_VARIANTS`, uploads the missing
  names to the target application via REST (429-aware), runs in `bot.spawn`,
  edits a progress message, then posts `nouveaux_emojis.json` (every target emoji with its new id), `emoji_mapping.json` and a remapped
  `utils/emojis.py`.
- `remap_emoji_ids.py`: local helper applying the mapping to any files.
- CLAUDE.md index + docs/README.md: new `docs/export/` folder.

## Decisions
- Script kept under 4000 characters (the jsk modal's `max_length`).
- Token passed on the second-to-last line: the jsk result panel echoes the first
  400 characters of the code; slash `/dev jsk` recommended so the token never
  sits in a channel message. CDN downloads go without the token.
- One copy per emoji name (an application cannot hold two emojis with the same
  name); every Moddy id sharing that name maps to it — same rule as the mini-bot
  sync.

## Verification
- Script run against a local fake Discord API (aiohttp): 503 uploads, existing
  name skipped, a forced 429 retried, no Authorization header on CDN requests,
  mapping + remapped `emojis.py` produced.
- Docs' code snippets checked against discord.py 2.7.1 signatures.

## Follow-ups
- Not run against the real Discord API from here (needs the target token).
