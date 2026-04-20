# Session 2026-04-20 — Verification Badge System (v3)

## What was done

Three improvements to the verification badge system:

1. **Moved dates and org lists from `attributes` to `data.verification`** — attributes are boolean flags, structured data belongs in `data`.
2. **`m.badge member` is now additive** — assigning orgs to an existing `VERIFIED_ORG_MEMBER` user appends instead of replacing.
3. **New `m.badge import` command** — bulk-set/remove badges from a JSON array (inline or attached `.json` file).

## Files modified

| File | Change |
|---|---|
| `utils/emojis.py` | `get_user_verification_badge()` gains optional `user_verification_data` param; reads orgs from `data.verification.VERIFIED_ORG_MEMBER.orgs` when provided, falls back to legacy `VERIFIED_ORG_MEMBER_ORG` attribute |
| `cogs/user.py` | `UserInfoView` gains `user_verification_data` param; dates read via new `_get_badge_date()` helper (checks `data.verification` first, falls back to `*_DATE` attributes); `user_command` extracts `data.verification` from DB and passes it through |
| `cogs/avatar.py` | `AvatarView` gains `user_verification_data` param; passed to `get_user_verification_badge()` |
| `cogs/banner.py` | Same as avatar.py |
| `staff/staff_manager.py` | `handle_badge_command`: dates/orgs written to `data.verification.*` (not attributes); org assignment is additive (merge with existing); added `m.badge import` / `_handle_badge_import()` |
| `utils/staff_help_view.py` | Added `m.badge import` entry |

## Data structure

Badge data in `users.data`:
```json
{
  "verification": {
    "VERIFIED":          { "date": 1234567890 },
    "VERIFIED_ORG":      { "date": 1234567890 },
    "VERIFIED_ORG_MEMBER": { "date": 1234567890, "orgs": ["Org1", "Org2"] }
  }
}
```

Boolean presence flags (`VERIFIED`, `VERIFIED_ORG`, `VERIFIED_ORG_MEMBER`) remain in `users.attributes`.

## `m.badge import` JSON format

```json
[
  {
    "user_id": "123456789",
    "action": "set",
    "badge": "member",
    "orgs": ["Org1", "Org2"],
    "replace_orgs": false,
    "date": 1234567890
  },
  {
    "user_id": "987654321",
    "action": "remove",
    "badge": "verified"
  }
]
```

- `action`: `"set"` (default) or `"remove"`
- `badge`: `v`/`verified`, `org`/`verified_org`, `member`/`verified_org_member`
- `orgs`: list of org names (member badge only)
- `replace_orgs`: if `true`, replaces existing orgs; default `false` (additive)
- `date`: Unix timestamp (default: now)
- JSON can be inline or in an attached `.json` file

## Backward compatibility

All reads fall back to legacy attribute keys (`VERIFIED_ORG_MEMBER_ORG`, `*_DATE`) so existing user data continues working. Removes clean up both old and new paths.

## Key decisions

- Attributes store *what a user is* (boolean), data stores *context* (dates, associated orgs). Keeps the attributes column clean.
- Additive org assignment by default matches UX expectation ("add Org2 to the user" ≠ "replace all orgs with Org2").
- `replace_orgs: true` in JSON import allows explicit replacement for migration scenarios.
