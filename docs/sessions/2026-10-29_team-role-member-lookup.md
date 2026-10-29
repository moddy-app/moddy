# 2026-10-29 — `t.role` sometimes never opened the linking window

## Symptom
`/team role` / `t.role` sometimes did not create the throwaway
"Moddy Team — linking" role.

## Cause
The command looked the staffer up with `guild.get_member()` only. Members are
not chunked at startup (`config.CHUNK_GUILDS_AT_STARTUP`, cache limited to
`MemberCacheFlags(joined=True)`), so for a staffer Moddy had not cached the
lookup returned `None`. `_blocker` then answered `not_member` ("You are not a
member of this server") and the window — throwaway role included — was never
opened. Intermittent by nature: it depended on whether that member happened to
be in the cache (recent join, earlier fetch…). Most likely when the command
targets another server with `guild_id`.

## Fix
- `staff/commands/team/team_role.py`: `get_or_fetch_member` (cache, then REST).
- `staff/commands/team/see.py`: same bug (refused with "not yours"), same fix.
- Test: `tests/test_linked_roles.py::TestTeamRoleFindsTheStaffer`.
- `CLAUDE.md`: fixed a broken tree line from the previous session.

## Not verified
No production logs were available. If the window still fails, the report card
says why (`window_failed` = Discord refused `create_role` /
`edit_role_positions`, logged as "Could not open the linking window…").
