# 2026-10-25 — Auto Role: server tag roles

## What was done
- New `tag_roles` list in the Auto Role module: roles given while a member
  displays the server tag, removed when they take it off. Also applied on join.
- New *Server tag roles* section in the `/config` panel, shown only when the
  server has a tag.
- `utils/guild_tags.py`: tag detection (gateway `profile.tag`, wearers'
  `primary_guild.tag`, fallback `GUILD_TAGS` feature) + `wears_tag`.
- `cogs/server_tag_events.py`: wraps `GUILD_CREATE` / `GUILD_UPDATE` /
  `GUILD_MEMBER_ADD` / `GUILD_MEMBER_UPDATE` parsers (read-only, then the
  original parser) and triggers `AutoRoleModule.sync_tag_roles`.
- Refactor: one validation loop and one role-section builder for the 3 lists.

## Files
`modules/auto_role.py`, `modules/configs/auto_role_config.py`,
`utils/guild_tags.py`, `cogs/server_tag_events.py`, `locales/*.json`,
`tests/test_auto_role_tags.py`, `docs/features/AUTO_ROLE.md`, `CLAUDE.md`.

## Decisions
- Raw parser hook instead of `on_member_update`: members are not chunked at
  startup, so the high-level events miss most tag changes.
- Discord has no bot route for a server's tag (`GET /guilds/{id}/profile` is
  user-only), hence the in-memory detection; `GUILD_TAGS` only means "can set
  one", used as a fallback when nothing else is known.
- Tag roles mirror the tag (idempotent sync), and wait for AltGuard like the
  join roles.

## Follow-ups
- No backfill of members already wearing the tag when the roles are first
  configured — they get the roles on their next member update.
- Not yet validated on a live server that bots receive `profile` in
  `GUILD_CREATE` (falls back to the feature / wearers otherwise).
- `tests/test_translate_button.py::TestDefaultEnabled::test_runs_where_nothing_is_stored_and_can_be_paused`
  was already failing before this change.
