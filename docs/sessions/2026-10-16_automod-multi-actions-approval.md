# Automod runtime: multi-actions, approval, truthful announcements

## What was done
- Removed the "AI guidance" (`indications`) at runtime: config, prompt section,
  `rules` plumbing in engine/nano/profile service, `violation_indications`
  category, `rules_check` route mode (only the custom-infraction check remains),
  preset key, tests, docs. Stored values are ignored.
- `automod/actions.py`: stored action vocabulary, conflict rules, exact timeout
  parse/format, cran bridge. `infractions.py`: new per-infraction schema
  (`actions`, `timeout_seconds`, `require_approval`, `by_gravity`), legacy `crans`
  migration, `effective_policy`, `set_entry_actions`. `bareme.py` integrates the
  set as the floor of the ladder (escalation/caps keep working).
- Hierarchy protection (`_is_protected`), apply-then-verify-then-announce
  (`_execute_incident`), cases only for what happened, timeout verified via the
  member returned by `edit(timed_out_until=…)`.
- Moderator approval: table `automod_pending_approvals`, repository, pure
  `automod/approvals.py`, `utils/automod_approval_views.py` (shared alert card +
  persistent buttons), `apply_approval` / `close_approval`.

## Decisions
- Delete happens immediately even when approval is required; the rest waits.
- Kick is not a case sanction (no `SanctionAction.KICK`): applied and shown on the
  card, no case, no appealable DM.
- Case recorded after the Discord call, so the audit reason has no case ref.
- Perso plans ignore gravity except through `by_gravity` (migration keeps it).

## Follow-ups
- `modules/configs/*` must adopt the new schema/helpers and drop its
  `validate_rules` import (a no-op stub remains in `automod/rules_check.py`).
- A `SanctionAction.KICK` would let kicks open cases.
