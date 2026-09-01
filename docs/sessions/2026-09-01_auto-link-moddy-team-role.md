# 2026-09-01 — `/team role` binds the linked-role requirement itself

## What was done

The previous session shipped `/team role` ending on instructions: *Server
Settings → Roles → Moddy Team → Links → Add requirement*, on the belief that
Discord exposed no API for it. That belief was half right.

Discord's **official** API has nothing — no field on the role payload, and
`role.tags.guild_connections` is read-only. But the client itself calls
`PUT /guilds/{guild.id}/roles/{role.id}/connections/configuration`, needing only
`MANAGE_ROLES`, documented by [Discord Userdoccers][ud] and nowhere by Discord.
`/team role` now uses it, and falls back on the old instructions when it fails.

[ud]: https://docs.discord.food/resources/guild#role-connection-configuration-object

## Files

| File | Change |
|---|---|
| `utils/moddy_team_role.py` | `link_team_role()`, `resolve_metadata_key()`, `build_requirement()`, `merge_configuration()`, `configuration_contains()`, `LinkResult` |
| `staff/commands/team/team_role.py` | binds after creating; three state wordings; the failure reason on the card |
| `tests/test_linked_roles.py` | +15 tests (payload, merge, every failure mode, the `premium` trap) |
| `locales/*.json` (×5) | `linked_auto`, `auto_{no_metadata,unsupported,forbidden,failed}`; `howto` reworded |
| `docs/LINKED_ROLES.md` | "Why the linking step is manual" → "How the binding is done" |

## Decisions and why

- **The route is treated as the unsupported thing it is.** Every failure is
  caught and returned as a `LinkResult`, never raised; 404/405 is reported as
  "Discord closed this route to us", distinctly from a permission problem an
  admin could act on. If it disappears tomorrow, `/team role` degrades to
  exactly what it did yesterday and nothing else in the bot notices.
- **The metadata key is `team`, and nothing else is accepted.** The first draft
  fell back on "the first boolean key in the schema". The schema also carries
  `premium` — that fallback would have bound the Moddy Team role to every
  subscriber. A missing `team` is now reported, never worked around. (Jules
  supplied the schema mid-session, which is what surfaced it.)
- **The key is read at runtime, not hardcoded blindly.**
  `GET /applications/{id}/role-connections/metadata` is official, bot-token, and
  read-only — the standing "never call the metadata endpoint" rule targets the
  `PUT`, which replaces the whole schema. Only a successful lookup is cached, so
  a bot that booted before the backend registered the schema recovers on the
  next command instead of staying wrong until a restart.
- **The existing configuration is read and merged into.** The `PUT` replaces it
  whole; ours goes in as an extra OR branch so a server that already had a
  requirement on that role keeps it.
- **The card trusts the route's answer, not `role.tags`.** Tags refresh on the
  `GUILD_ROLE_UPDATE` gateway event, which has not arrived when the card is
  built. A "just linked" wording distinguishes Moddy's own binding from one an
  admin did last week.

## Known issues / follow-ups

- **Nothing here has been exercised against the real Discord API yet.** The
  tests cover the payload and every failure path, not the route's existence.
  First real run should be watched: the log line
  `"The role connection route is not available to Moddy (HTTP …)"` is the one
  that says a bot token is refused.
- `tests/test_persistent_views.py` fails wholesale (293) in this environment,
  on a clean tree as well — a dependency/version problem, unrelated.
- The optional public `/link` command is still not written (see the previous
  session log).
