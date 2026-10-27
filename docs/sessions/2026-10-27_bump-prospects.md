# Session: Bump prospects (internal)

**Date:** 2026-10-27
**Agent:** Claude Code

## Summary

New internal (team-side) feature: Moddy spots servers that have at least one
bump directory bot (DISBOARD, DiscordL, French.gg…) but no reminder — neither a
third-party reminder bot (`478321260481478677`, `1441619898756501574`,
`735147814878969968`) nor Moddy's own Bump Reminder module running — and posts
one card per server in the team channel `1564692382107041894` with Invite,
Claim/Unclaim, Close and Reopen. `/dev bumpscan` rescans every server.

## Files

- New: `bumpreminder/prospects.py`, `services/bump_prospect_service.py`,
  `cogs/bump_prospects.py`, `utils/bump_prospect_views.py`,
  `db/repositories/bump_prospects.py`, `staff/commands/dev/bumpscan.py`,
  `tests/test_bump_prospects.py`, `docs/features/BUMP_PROSPECTS.md`
- Modified: `config.py` (`MODDY_BUMP_PROSPECT_CHANNEL_ID`), `db/base.py`
  (`bump_prospects` table + mixin), `bot.py` (`bot.bump_prospects`),
  `utils/persistent_views.py`, `utils/staff_role_permissions.py`
  (`bump_prospect` node, Communication role), the 5 locales (`bump_prospects.*`),
  `CLAUDE.md`, `docs/infra/DATABASE.md`, `docs/infra/RAILWAY.md`,
  `docs/guides/STAFF_SYSTEM.md`, `docs/guides/PERSISTENT_VIEWS.md`,
  `docs/features/BUMP_REMINDER.md`

## Decisions

- **Gateway member query per server** (`query_members(user_ids=…)`): the
  startup chunk is off, so the cache cannot prove a bot is absent. Paced at one
  server per second through a single queue to stay far below the gateway limit.
- **One row per server, forever** (PK `guild_id`): a rescan never re-posts,
  and a closed card stays closed.
- **Invite created on click, ephemeral, single use, 24 h** — not on the card,
  so a server nobody looks at never sees an invite in its audit log, and
  nobody burns another staffer's link.
- **Paused module still a prospect**; `active` / `simulation` are not.
- Open cards are settled automatically: module enabled → `converted`, Moddy
  removed → `left` (30-minute follow-up + `on_guild_remove`).

## Follow-ups

- The reminder-bot list is the team's three most common ones; extend
  `REMINDER_BOTS` as others show up on cards.
- `tests/test_translate_button.py::TestDefaultEnabled::test_runs_where_nothing_is_stored_and_can_be_paused`
  fails on `main` too, unrelated to this change.
