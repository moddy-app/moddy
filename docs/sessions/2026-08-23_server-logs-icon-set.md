# Server logs: one icon set for the panel and the log messages

## What was done

158 new custom emojis were provided for the logs feature, with the rule: the
logs **configuration panel** may use nothing else (the module icon in the
`/config` picker excepted), and the **log messages** should use them too.

- **`utils/emojis.py`** — new `LOG_EMOJIS` dict (the 158 icons) + `log_emoji(name)`.
  A dict rather than 158 constants: the names are Discord's own vocabulary
  (`createchannel`, `timedout`, `Notchecked`), several would collide with the
  general constants (`Edit`, `Filter`, `Play`, `check`, `ban`…), and the logs
  resolve their icon *by name* from the registry. `log_emoji` returns `""` for
  an unknown name — a typo must never stop a log from being delivered.
- **`serverlogs/registry.py`** — the 18 category icons now come from that set,
  plus `_EVENT_ICONS` (an icon for each of the 163 events, keyed by bare event
  name) and `event_emoji(key)`: the event's own icon, else its category's.
- **`serverlogs/renderer.py`** — the event title moved from `embed.title` into
  a `### <icon> Title` heading at the top of the description. A jump link
  (`entry.url()`) becomes a markdown link on that heading instead of
  `embed.url`.
- **`modules/configs/logs_config.py`** — every icon of the three screens now
  comes from the set (`_ICON_BACK`, `_ICON_NEXT`, `_ICON_OPTIONS`,
  `_ICON_CLEAR`, `_ICON_ON`, `_ICON_OFF`), and each event in the checklist
  carries its own icon.
- **Docs** — `docs/EMOJIS.md` lists the set; `docs/LOGS.md` gained an *Icons*
  section (resolution order, the "only source" rule and its one exception).

## Decisions made and why

- **The heading had to leave `embed.title`.** Discord renders a custom emoji in
  an embed *description* and prints it as raw `<:name:id>` text in a *title*.
  Asking for icons in the log messages therefore meant moving the title into
  the description as a `### ` heading — which is also the Components V2 title
  convention (CLAUDE.md rule 5). The jump link some events carry moved with it,
  as a markdown link, so nothing was lost.
- **Two levels of icon resolution, not 163 hand-mapped events.** An event
  without its own entry inherits its category's icon, so a new event is never
  iconless and naming it in `_EVENT_ICONS` stays optional polish rather than a
  fourth step in "adding an event".
- **Keyed by bare event name.** A ban is `server.ban_add` and
  `moderation.ban_add`; the same act must look the same in both channels.
- **The rule is enforced by tests**, not by good intentions:
  `test_categories_and_events_use_the_logs_icon_set` and
  `test_the_config_panel_draws_only_from_the_logs_icon_set` fail if an icon
  from anywhere else appears.

## Files modified

| File | Change |
|---|---|
| `utils/emojis.py` | `LOG_EMOJIS` (158 icons) + `log_emoji()` |
| `serverlogs/registry.py` | category icons, `_EVENT_ICONS`, `event_emoji()` |
| `serverlogs/renderer.py` | heading with icon inside the description |
| `modules/configs/logs_config.py` | panel icons + per-event icons in the checklist |
| `docs/EMOJIS.md`, `docs/LOGS.md` | the set and the rule |
| `tests/test_logs.py` | icon coverage + panel-source tests (updated rendering tests) |

## Known issues / follow-ups

- **The icon-to-event mapping is a guess from the icon *names*.** The emojis
  were provided as names and ids; nothing here renders them, so pairings like
  `messages` → `dm`, `voice` → `Play` or `stickers` → `stickerscreated` may not
  look right in Discord. Each is a one-line change in
  `registry._EVENT_ICONS` / the `_CATALOGUE` — worth a pass by eye in a real
  server.
- A few categories had no neutral icon in the set (voice, stickers, soundboard,
  events, stage), so they borrowed a directional one (`stickerscreated` for the
  *Stickers* category, for instance).
- The `boostlevel*`, `roleicon1…18`, `premiumbadge*`, `Verificationlevel*`,
  `quarantined`, `raid`, `unusualdmactivity`… icons are in `LOG_EMOJIS` and
  available, but nothing references them yet — they are there for the events
  that will want them (and for the value formatters, if boost tiers or
  verification levels ever get an icon of their own).

## Tests

`python3 -m pytest -q` → **1028 passed** (1026 before, +2).
