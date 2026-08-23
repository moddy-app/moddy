# Server logs: merging the several events that describe one act

## What was done

Reported from a live server: muting someone through Moddy posted **three
near-identical embeds** in the log channel — `moderation.mute_add` (the case),
`users.user_timed_out` (Discord's timeout) and a second `moderation.mute_add`
(the timeout mirrored back into the moderation category by
`_log_timeout`). Technically three distinct events, all three true; visually
one act told three times.

Added an opt-out option, `merge_duplicates` (**on by default**), that delivers
one log per *act* instead of one per registry event.

- **`serverlogs/registry.py`** — `_MERGE_FAMILIES` + `merge_family(event)`: which
  events describe one act, and how much context each carries (priority).
  Families: mute, unmute, ban, unban, kick, warn, unwarn, role changes.
- **`serverlogs/renderer.py`** — `LogEntry` now keeps its lines as
  `(label, rendered)` pairs, carries a `subject_id`, and gained `absorb(other)`:
  folds a sibling in, taking only the labels it does not already have, and
  names the absorbed event under the log (`Merged with: …`).
- **`serverlogs/service.py`** — `submit()` holds a family member for
  `MERGE_WINDOW = 3 s` instead of dispatching it; `flush()` delivers the
  highest-priority entry enriched by the others. Rendering and fan-out moved
  into `_dispatch()`, shared by both paths.
- **`modules/logs.py`** — the `merge_duplicates` config field.
- **`modules/configs/logs_config.py`** — a fourth toggle on the Options screen,
  with the one-line explanation under it (the toggle stores a value no
  component displays, so CLAUDE.md rule 9 wants the text).
- **i18n** — `config.options.toggles.merge` / `.merge_description` and
  `values.merged_with` in the five locales.
- **`docs/LOGS.md`** — new *Merging duplicates* section, `merge_duplicates` in
  the stored-schema table and in the dashboard contract.

## Decisions made and why

- **Merging is per channel.** The winner absorbs a sibling only where both
  would have landed in the same channel; a sibling routed somewhere the winner
  does not go is still delivered there, on its own. A server that already
  splits `moderation` and `users` across two channels loses nothing by leaving
  the option on — there was no duplicate to merge in either channel. Merging on
  a union of destinations would have moved logs into channels that never asked
  for them.
- **Only declared families are held back.** A generic "same event, same
  subject inside N seconds" rule would collapse two messages deleted in a row
  from the same author into one log. Losing a log is worse than showing two, so
  every event outside a family is dispatched immediately and untouched — and
  the 3 s latency is paid only by moderation-shaped events.
- **A window, not a "first one wins".** The siblings come from different
  sources (a Moddy case, a gateway event, an audit entry) and arrive within
  milliseconds of each other but in **no guaranteed order**, so the first
  arrival waits rather than deciding alone. Ties in priority keep arrival
  order, and `absorb` unions the content either way, so the merged result does
  not depend on who got there first.
- **Absorb by label, not by text.** The case says `Reason: bonjour`, the
  timeout says `Reason: [N6A8Q2] @juthing_ (Permanent) : bonjour` — same field,
  two phrasings. Deduplicating on the label keeps one **Reason** line instead
  of two that say the same thing differently.
- **Default on.** The option exists because the default was wrong for the
  common setup (everything to one channel). Servers that want one log per
  registry event turn it off in Options.
- **The merge is announced.** `Merged with: A user was timed out` under the
  log, so a moderator reading it knows why they are not seeing the event they
  enabled.

## Files modified

| File | Change |
|---|---|
| `serverlogs/registry.py` | `_MERGE_FAMILIES`, `merge_family()` |
| `serverlogs/renderer.py` | labelled lines, `subject_id`, `absorb()`, merge note |
| `serverlogs/service.py` | hold / flush / `_dispatch`, `MERGE_WINDOW` |
| `serverlogs/listeners/moderation.py` | keep the merge key when only an id is known |
| `modules/logs.py` | `merge_duplicates` config field |
| `modules/configs/logs_config.py` | Options toggle |
| `locales/{fr,en-US,es-ES,pt-BR,de}.json` | toggle label + description, `values.merged_with` |
| `docs/LOGS.md` | *Merging duplicates* section + schema |
| `tests/test_logs.py` | 7 new tests |

## Known issues / follow-ups

- **Latency.** A family event is delivered ~3 s late; for a kick, up to ~5 s,
  since `on_member_remove` already waits up to 2 s for the audit entry that
  tells a kick from a plain leave. Fine for a log channel, but it is real.
- **The families are hand-declared.** A new pair of events describing one act
  has to be added to `_MERGE_FAMILIES` or it will duplicate again. Adding an
  event to the registry should include the question "does something else
  already say this?".
- **Still not validated live** — like the rest of the logs system. The merged
  rendering is unit-tested, not seen in a real channel.

## Tests

`python3 -m pytest -q` → **1026 passed** (1019 before, +7).
