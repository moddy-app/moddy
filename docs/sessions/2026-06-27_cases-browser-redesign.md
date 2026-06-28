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

## Follow-up (same session)
- Renamed commands: personal `/cases` → **`/mycases`**; server `/sanctions` →
  **`/cases`**.
- Both commands gained an optional `case` argument (public reference) that opens
  straight to the case detail, scope-checked so it can't reach out-of-scope
  cases (`CasesBrowserView.open_reference`).
- `/cases` (server) permission model reworked: `default_permissions(
  manage_messages=True)` + bot-side **Manage Messages** minimum to view/comment/
  edit/close; per-action Discord permission to add/revoke a sanction
  (`SANCTION_PERMISSION`: ban→Ban Members, kick→Kick Members,
  warn/mute/restrict→Timeout Members), enforced both when building the action
  list and on modal submit.
- `/ban` `/mute` `/warn`: `user` argument is now **mandatory**.

## Follow-up #2 (same session — UX & permissions polish)

### Cases browser UI overhaul
- Header emoji is now `<:folders:…>` (list) / `<:folder:…>` (detail). Open-select
  options use the folder emoji too.
- Filters button uses `<:filter:…>`; evidence button uses `<:image:…>`.
- Pagination redesigned: `|←| n/N |→|` — the page indicator is a disabled
  button. All buttons sit **outside** the container.
- Detail screen: container accent reflects the case state (green = open,
  red = closed). The status emoji prefix on the status field was dropped; the
  per-sanction action emoji was dropped on each sanction line.
- Detail-screen action layout: row 1 = Add sanction · Revoke (green) · Close
  (green) ; row 2 = Comment · Edit reason · Evidence ; row 3 = Back.
- The "Back to list" label is now just "Back" / "Retour".
- Same look applied to `/mycases` (read-only — only Evidence + Back).
- Title is shared "Cases" / "Dossiers" for both commands.

### Persistence (now MANDATORY per CLAUDE.md)
- `CasesBrowserView` is fully persistent: `__persistent__ = True`, every button
  and select carries a stable namespaced `custom_id`
  (`moddy:cases:browser:<action>:<mode>`).
- A shell (`bot=None`) is registered for each mode in
  `utils/persistent_views.py`. After a bot restart, any click rebuilds a fresh
  live view from the interaction's user/guild context. Detail-screen mutations
  rehydrate to the list view (per `docs/PERSISTENT_VIEWS.md` "working-copy"
  rule).
- CLAUDE.md §8 hardened: "MANDATORY — no exceptions". Will be enforced in review.

### Sanctions commands
- New `/kick` (Kick Members permission, modal flow identical to /ban /mute
  /warn). DM accent + emoji wired (`LOGOUT`).
- The `incognito` argument stays optional on every sanction command (defaults
  to the user's incognito preference, falling back to True).
- Hierarchy & owner safeguards on every command: the moderator's top role must
  be strictly above the target's, the bot's top role must be above the target's
  (skipped for `warn` — no Discord action), and the guild owner / self can
  never be targeted. Enforced once on slash-command entry **and** again at
  modal submit (so users added inside the modal can't bypass it).
- `SanctionAction.RESTRICT` dropped from the GUILD picker (`CASE_TYPE_ACTIONS`)
  — guild scope only carries warn / mute / kick / ban. Global / platform still
  support restrict via the case service.

### Permission model on `/cases` (server)
- `default_permissions(manage_messages=True)` + bot-side **Manage Messages**
  minimum to view / comment / edit / close.
- Per-action Discord permission to add/revoke a sanction
  (`SANCTION_PERMISSION`): ban → Ban Members, kick → Kick Members,
  warn/mute → Timeout Members. Enforced both when offering actions and at
  modal submit.

### Misc
- Ban sanction emoji updated to `:legal:` (was `:blacklist:`).
- New helper `_perm_error()` + i18n keys for hierarchy / permission errors
  (`moderation.errors.*`, `cases.browser.*`).

## Known issues / follow-ups
- Could not run the bot here (no `discord` package in the sandbox); changes are
  `py_compile`-clean and JSON validated. Worth a live smoke test of the Modals
  V2 `Label`/`UserSelect` flows on discord.py ≥ 2.6.
