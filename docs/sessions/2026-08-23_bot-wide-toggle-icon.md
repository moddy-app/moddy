# One toggle icon pair for the whole bot

## What was done

`TOGGLE_ON` / `TOGGLE_OFF` (`utils/emojis.py`) were repointed to two new
custom emojis and declared the **only** toggle icon pair the bot may use,
replacing the old ones everywhere. Being constants, the two existing call
sites (`modules/configs/voice_transcription_config.py`,
`modules/configs/automod_ai_config.py`) picked up the new ids automatically —
nothing to change there.

The logs config panel was not using these constants for its three real
on/off settings (`ignore_bots`, `attach_transcripts`, `merge_duplicates`):
an earlier session had put them on the logs icon set's `Checked`/`Notchecked`
instead, back when the panel was restricted to that set entirely. That
restriction was already relaxed for Back/Options/Clear in the previous
session; this one extends the same reasoning to the toggles, since a switch
icon is exactly as generic as a Back arrow — it says nothing about logs.

- **`modules/configs/logs_config.py`** — `_ICON_ON = TOGGLE_ON`,
  `_ICON_OFF = TOGGLE_OFF`. The category checklist's "enable all" / "disable
  all" buttons were sharing those same two constants by coincidence and are
  now split into their own `_ICON_ALL` / `_ICON_NONE` (still `Checked` /
  `Notchecked`, the logs set): they are a bulk *action* with a fixed icon,
  not a state that flips — a checkbox concept, not a switch.
- **`docs/EMOJIS.md`** — the two ids updated.
- **`docs/LOGS.md`** — the *Icons* section documents the toggle pair as the
  fourth generic-chrome exception, and explains why "enable/disable all"
  stays on the logs set instead.

## Decisions made and why

- **A switch is not logs-specific**, exactly like a Back arrow: it says
  "this setting is on", never "this is a log thing". Every other `/config`
  panel with a real on/off setting already used `TOGGLE_ON`/`TOGGLE_OFF`;
  the logs panel was the one inconsistency, from before the "generic chrome
  stays generic" rule existed.
- **"Enable all" / "disable all" are not toggles.** A toggle reflects a
  *state* (the icon flips depending on what is currently true). Those two
  buttons always show the same icon regardless of anything — they are an
  action ("do this to every event on the page"), closer to a checkbox concept
  than a switch. Reusing the switch icon for them would have been the wrong
  fix: it would visually claim they reflect a state, which they don't.
- **Enforced by a bot-wide test**, not just a logs one:
  `test_every_toggle_in_the_bot_uses_the_one_toggle_icon_pair` scans every
  `.py` file in the repo (outside itself) for the two retired emoji ids and
  fails if either survives anywhere — a future PR that pastes the old ids
  back in (a copy-pasted snippet, an old doc example) gets caught immediately
  instead of silently reintroducing a second toggle style.

## Files modified

| File | Change |
|---|---|
| `utils/emojis.py` | `TOGGLE_ON` / `TOGGLE_OFF` repointed to the new ids |
| `docs/EMOJIS.md` | the two ids updated |
| `modules/configs/logs_config.py` | toggles use `TOGGLE_ON`/`TOGGLE_OFF`; "all"/"none" split into their own constants |
| `docs/LOGS.md` | *Icons* section: toggle pair as a fourth generic-chrome exception |
| `docs/sessions/2026-08-23_bot-wide-toggle-icon.md` | this file |
| `tests/test_logs.py` | icon-bucket test extended; new bot-wide guard test |

## Tests

`python3 -m pytest -q` → **1176 passed** (1175 before — one test added; the
existing icon-bucket test was extended rather than duplicated).
