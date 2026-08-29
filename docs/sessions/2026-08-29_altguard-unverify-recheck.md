# 2026-08-29 — AltGuard: unverified role must outrank a stale `verified` DB row

## What was done

Fixed a bug reported by the user: a member who still carries the
**unverified** Discord role (e.g. after a manual `/altguard unverify`, or any
other reconciliation gap) but whose `altguard_members` row still said
`verified` was told "you already passed" when clicking the verification
panel button, and could not re-verify.

- `modules/altguard.py`: added `has_unverified_role()` (mirrors
  `has_verified_role()`) and `resync_stale_verified_status()`, which resets a
  stale `verified` row back to `pending`.
- `utils/altguard_views.py::_already_verified()`: now checks the unverified
  role *before* falling back to the DB. If the member wears that role, the
  answer is always "not verified" — and if the DB disagreed, it is corrected
  on the spot (`resync_stale_verified_status`).
- `docs/ALTGUARD.md`: documented the role-outranks-DB rule in the "Member
  journey" §3 (Consent).
- `tests/test_altguard.py`: added
  `test_the_unverified_role_outranks_a_stale_verified_db_row`.

## Decisions made and why

The unverified/verified **role** is the authority the server (and the gate)
actually enforces — `has_verified_role()` already treated it that way for the
"yes" case (a hand-granted role with no DB row still skips the gate). The fix
makes the "no" case symmetric: the unverified role also outranks a `verified`
DB row, since only the role decides what channels the member can actually
see. Silently correcting the stale row (rather than just returning `False`)
keeps `altguard_members` from repeating the same lie on the next check.

## Known issues / follow-ups

None. All 46 tests in `tests/test_altguard.py` and 283 in
`tests/test_persistent_views.py` pass.
