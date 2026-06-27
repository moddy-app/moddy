# Session — Cases browser redesign + Discord-side mod perms

Date: 2026-06-27

## What was done

### 1. Redesigned `/cases` and added `/sanctions`
Replaced the old per-case paginated `/cases` view with a single reusable,
filterable + paginated **cases browser** (`utils/cases_views.py`) powering two
commands:

- **`/cases`** (`cogs/cases_user.py`) — a member browses their own sanctions
  across every server. Read-only.
- **`/sanctions`** (`cogs/cases_server.py`, new) — guild-only; a server
  moderator browses sanctions issued in the guild and can fully manage each
  case.

**UX (per `docs/DESIGN.md` + `docs/MODALS_V2.md`):**
- List screen: compact paginated overview (`PAGE_SIZE = 5`), status dot + type
  emoji + reference + sanction-action emojis + context + reason snippet.
- Filters live in a **Modal (Modals V2)** opened from a single *Filters*
  button: status, sanction type, period (24h/7d/30d/90d), and a context filter
  (server `Select` in user mode, `UserSelect` in server mode). Keeps the list
  screen to two action rows. Active filters are summarised; a *Reset* button
  appears when any filter is set.
- Detail screen ("zoom"): full folder (fields, reason, sanctions, public
  comments). Internal staff notes are never shown.
- Server mode detail carries **all moderator case actions**: add sanction,
  revoke sanction, comment, edit reason, close/reopen — recorded as
  `author/issuer = discord_user` (not `moddy_staff`). User mode is read-only.

**DB (`db/repositories/moderation.py`):** added `search_cases` (filtered +
paginated; each row carries `actions[]` and `has_active` via subqueries, no
N+1), `count_cases`, and `list_subject_scopes` (servers a subject has cases in,
for the `/cases` server filter). Shared filter builder `_case_filters`.

### 2. Discord-side permissions for `/ban` `/mute` `/warn`
Added `@app_commands.default_permissions(...)` so Discord enforces access
(not just bot-side): `ban` → Ban Members, `mute`/`warn` → Moderate Members.

## Files modified
- `cogs/cases_user.py` (rewritten — uses the browser)
- `cogs/cases_server.py` (new — `/sanctions`)
- `utils/cases_views.py` (new — `CasesBrowserView` + filter/comment/edit/
  add-sanction/revoke modals)
- `db/repositories/moderation.py` (`search_cases`, `count_cases`,
  `list_subject_scopes`, `_case_filters`)
- `cogs/moderation_commands.py` (`default_permissions` on ban/mute/warn)
- `locales/en-US.json`, `locales/fr.json` (`commands.cases.browser.*`)
- `CLAUDE.md`, `docs/MODERATION_CASES.md` (docs)

## Decisions
- **Guild scope isolation:** `/sanctions` queries only `discord_guild`-scoped
  cases, so guild moderators can never read or edit `global`/`platform`
  (Moddy-team) cases.
- **Permission model for `/sanctions`:** bot-side `any(ban/kick/moderate/
  manage/admin)` check (Discord's `default_permissions` ANDs flags and can't
  express "any of", and viewing is low-risk).
- **Sanctions recorded, not enforced:** adding a sanction from `/sanctions`
  records it on the case folder; it does not perform the Discord action. Actual
  enforcement stays with `/ban` `/mute` `/warn` (auto-synced via audit log).

## Known issues / follow-ups
- Could not run the bot here (no `discord` package in the sandbox); changes are
  `py_compile`-clean and JSON validated. Worth a live smoke test of the Modals
  V2 `Label`/`UserSelect` flows on discord.py ≥ 2.6.
