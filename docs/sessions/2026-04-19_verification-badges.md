# Session 2026-04-19 — Verification Badge System

## What was done

Implemented a 3-tier verification badge system for user display names, a staff command to manage badges, and fixed the `/avatar` title i18n.

## Files modified

| File | Change |
|---|---|
| `utils/emojis.py` | Updated `VERIFIED` emoji ID; added `VERIFIED_ORG`, `VERIFIED_ORG_MEMBER`; added `get_user_verification_badge()` utility |
| `cogs/user.py` | Title now uses i18n + badge; notices section handles 3 tiers; `on_avatar_click` and `on_banner_click` show badge in title via i18n |
| `cogs/avatar.py` | Fetches moddy_attributes; `AvatarView` accepts `moddy_attributes`; title uses `commands.avatar.view.title` i18n key + badge |
| `cogs/banner.py` | Fetches moddy_attributes; `BannerView` accepts `moddy_attributes`; badge appended to username in title |
| `staff/staff_manager.py` | Added `m.badge` command with set/remove sub-actions |
| `utils/staff_role_permissions.py` | Added `badge_manage` to `MANAGER_PERMISSIONS` |
| `locales/fr.json` | Added `verified_org_member_notice` and `verified_org_notice` keys in `commands.user.view` |
| `locales/en-US.json` | Same |
| `CLAUDE.md` | Added rule §7 — Verification Badge on Usernames |
| `docs/EMOJIS.md` | Updated `verified` emoji ID; added `verified_org` and `verified_org_member` |

## Badge logic (`get_user_verification_badge`)

Priority:
1. `VERIFIED_ORG` attribute → `<:verified_org:...>`
2. Discord staff flag (public_flags bit 0) **or** `TEAM` attribute **or** `VERIFIED_ORG_MEMBER` attribute → `<:verified_org_member:...>` + org list
3. `VERIFIED` attribute → `<:verified:...>`
4. No badge → empty string

## `m.badge` command syntax

```
m.badge @user verified
m.badge @user verified_org
m.badge @user verified_org_member [org_name]
m.badge @user remove <verified|verified_org|verified_org_member>
```

Permission required: `badge_manage` (manager-level).

## Known follow-ups

- Other commands that display usernames (e.g. staff commands, moderation cases) should also call `get_user_verification_badge()` — follow the rule added to CLAUDE.md §7.
