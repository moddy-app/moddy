# AltGuard: `matches` field on the verdict + `enforced` default flip

## What was done

The AltGuard service now sends an additional `matches` field on
`altguard:verdict` (the linked accounts a verification matched with, most-linked
first, up to five, always `[]` on a `passed`) and, after its `005` migration,
unconfigured guilds default to `enforced: true` instead of `false`. Neither
changes the bot's contract — `enforced` still gates every action — but both
needed wiring/documenting on the bot side:

- `services/altguard_client.py::parse_verdict` now reads `matches` defensively
  (`data.get("matches", [])`), sanitizing each entry (`discord_user_id` → int,
  `score` → int or `None`, `reasons` → list of str) and dropping malformed
  entries individually rather than the whole list.
- `modules/altguard.py::apply_verdict` forwards `matches` to the log card.
- `utils/altguard_views.py::build_log_card` renders a "linked accounts" block
  when `matches` is non-empty — log channel only, never in the member DM
  (`notify_member` was already untouched by this field and stays that way).
- Added the `modules.altguard.logs.matches` i18n key to all five locales.
- No production workaround existed for the "member leaves and rejoins,
  matches against their own trace" false positive the service now fixes — grepped
  for it, nothing to remove.

## Files modified

- `services/altguard_client.py` — `parse_verdict` matches parsing
- `modules/altguard.py` — pass `matches` to the log card, docstring
- `utils/altguard_views.py` — `build_log_card` renders matches
- `locales/{fr,en-US,es-ES,pt-BR,de}.json` — new `matches` log label
- `tests/test_altguard.py` — parsing + log card coverage for `matches`
- `docs/ALTGUARD.md`, `docs/ALTGUARD_INTEGRATION.md` — documented the field
  and the `enforced` default flip (with the `shadow_mode` observation path)

## Known issues / follow-ups

- `matches` is not persisted to `altguard_verifications`, so `/mod altguard
  refusal` cannot show it after the fact — only the log card at verdict time
  carries it. Acceptable for now since the spec scopes this to the
  moderation log, but worth a follow-up if staff ask for it historically.
