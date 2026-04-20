# Session 2026-04-20 — Verification Badge System (v2)

## What was done

Extended and refined the verification badge system implemented the previous session.

## Files modified

| File | Change |
|---|---|
| `utils/emojis.py` | `VERIFIED` and `VERIFIED_ORG_MEMBER` now use the same emoji `<:verified:1495533349266264230>`; added `DOCS_VERIFIED_URL`, `format_verification_badge()`, `_parse_org_list()`; `get_user_verification_badge()` now returns a 3-tuple `(badge, org_names, tier)` |
| `cogs/user.py` | Title uses `name=`/`badge=` i18n params separately; badge is a hyperlink via `format_verification_badge()`; notices show verification date + "Learn more" link (except Discord/Moddy staff); multi-org support with locale-aware joining (`_format_org_names()`); inline avatar/banner buttons use `global_name` |
| `cogs/avatar.py` | Uses `global_name`; badge is a hyperlink; passes `name=`/`badge=` to i18n |
| `cogs/banner.py` | Same as avatar.py |
| `staff/staff_manager.py` | `m.badge` rewritten with short aliases (`v`, `org`, `member`, `rm`); stores verification date as Unix timestamp; supports multiple orgs (JSON array); plain user IDs now work (fetch fallback to `SimpleNamespace`); `import re` added |
| `utils/staff_role_permissions.py` | Added `badge_manage` permission to `MANAGER_PERMISSIONS` |
| `utils/staff_help_view.py` | Updated `m.badge` entries with short aliases and multi-org syntax |
| `locales/fr.json` | Updated `avatar`, `banner`, `user` titles to use `{name}`/`{badge}`; added `verified_date`, `verified_learn_more`, `verified_org_*` notice keys; fixed double-bold bug |
| `locales/en-US.json` | Same |
| `CLAUDE.md` | Updated §7 with hyperlink format, `format_verification_badge()`, `global_name` rule, 3-tuple return |
| `docs/EMOJIS.md` | Updated `verified` emoji ID; added `verified_org` and `verified_org_member` |

## Key decisions

- `VERIFIED` and `VERIFIED_ORG_MEMBER` share the same emoji (`<:verified:1495533349266264230>`); `VERIFIED_ORG` keeps its own emoji. The `tier` return value distinguishes them in code.
- Badge emoji is always wrapped as a hyperlink `[emoji](https://docs.moddy.app/articles/verified-badges)` via `format_verification_badge()`.
- `name=` and `badge=` are passed as **separate** i18n parameters to avoid the badge ending up inside `'s` (English) or between other words.
- Multiple orgs stored as a JSON array string in `VERIFIED_ORG_MEMBER_ORG`; legacy single-string format still supported via `_parse_org_list()`.
- "Learn more" link shown only for non-staff badges (not Discord employee / Moddy team).
- Verification date stored as Unix timestamp string in `{ATTR_KEY}_DATE` when set via `m.badge`.

## `m.badge` command syntax

```
m.badge @user v                        → VERIFIED
m.badge @user org                      → VERIFIED_ORG
m.badge @user member Orga1, Orga2      → VERIFIED_ORG_MEMBER (multiple orgs)
m.badge @user rm <v|org|member>        → remove badge
```

Accepts plain user IDs — if `fetch_user` fails, falls back to a `SimpleNamespace` placeholder so DB operations still proceed.

## Bug fixes

- Double bold markers (`****Orga****`) caused by `**{org_name}**` in i18n key + `**{o}**` in `_format_org_names()` — fixed by removing `**` from the i18n keys.
- `m.badge 159985415099514880 org` returned "Invalid Usage" because `tokens = tokens[1:]` was inside the `try` block and never executed when `fetch_user` failed — moved token consumption before the fetch attempt.
