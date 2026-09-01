# 2026-09-01 — The linking window, and the end of the automatic binding

## What happened

The previous session shipped `/team role` binding the Moddy Team role itself
through `PUT /guilds/{id}/roles/{id}/connections/configuration`, the undocumented
route the Discord client uses. It was merged as #376. Then it ran in production:

```
HTTP 403 (Discord code 20001): Bots cannot use this endpoint
```

on the `GET` as well as the `PUT`. `20001` is not a permission problem — it is
the code Discord returns for an endpoint closed to bot tokens. Searched for a way
round it and there is none: no OAuth2 scope covers guild role configuration
(`role_connections.write` writes a *user's* metadata, `rpc.api` is not public),
and the only credential that works is a user session, which is self-botting.

The logging added in the follow-up PR is what produced that line. Without it the
failure would have read `forbidden` and we would have gone looking at
permissions.

## What replaced it

`/team role` now lends `Manage Roles` to the staffer who ran it, for thirty
seconds, so they can do the clicks themselves.

- **Moddy Team** → position 1 (bottom); a throwaway role with *only*
  `manage_roles` → position 2, directly above it. Discord forbids editing any
  role at or above your own highest, so the staffer can reach exactly one role.
- Their other roles are taken off them — written to the database **before** the
  removal — and given back at the end.
- `on_guild_role_update` detects success (`guild_connections`);
  `on_audit_log_entry_create` reverts anything else they do and closes the
  window.
- `recover_sessions()` on every `on_ready` finishes a window a restart cut in
  half.

## Files

| File | Change |
|---|---|
| `services/team_link_session.py` | **new** — the window, the watchdog, the teardown, the recovery |
| `cogs/team_link_events.py` | **new** — gateway wiring + boot sweep |
| `staff/commands/team/team_role.py` | `_blocker()` pre-flight, the window card, outcome reporting |
| `utils/moddy_team_role.py` | the dead binding code removed; docstring tells the truth |
| `staff/framework/command.py`, `cog.py` | `defer` opt-in on staff slash commands |
| `staff/commands/team/{team_role,ticket}.py` | `defer = True` |
| `utils/altguard_views.py`, `utils/team_access_views.py` | `format_member_name(link=…)` |
| `utils/emojis.py` | `STAFF` repointed — the `:staff:` artwork was deleted |
| `tests/test_linked_roles.py` | binding tests dropped, window tests added (44 pass) |
| `locales/*.json` (×5) | `window_*`, `blocked_*`; the `auto_*` keys removed |
| `docs/LINKED_ROLES.md`, `CLAUDE.md` | rewritten around the window |

## Decisions and why

- **Say what it is.** No administrator approves this window — I raised that and
  Jules chose the unilateral flow deliberately. The doc says so in those words
  rather than describing it as a safe helper, because the next person to read it
  needs to weigh it, not be reassured by it.
- **The hierarchy does not contain everything.** `manage_roles` is *Manage
  Permissions* on channels too, and overwrites are not bounded by role position.
  The audit watch is the only thing covering it, and audit entries can lag — so
  the doc calls it detection, not a guarantee.
- **Success means "a requirement exists", not "ours".** `guild_connections` is a
  boolean, and reading which requirement is on the role needs the endpoint
  Discord just closed. What protects us is that the person doing it is our own
  staff.
- **`role_update` on Moddy Team is exempt from the watchdog.** That edit *is* the
  task; without the exemption the feature would cancel its own success.
- **Roles persisted before removal, never only in memory.** A crash mid-window
  would otherwise strip somebody permanently. Managed roles are left alone
  (Discord refuses to remove them) and **Moddy Team is filtered out of the
  restore**, since nothing here may ever grant it.
- **`defer` is opt-in, not global.** Deferring every staff slash command would
  break `ctx.open_modal` — Discord refuses `send_modal` on an answered
  interaction — so commands that open modals must keep the 3 s path.

## After the first real run

`t.role` in a live server was refused with `above_moddy`: the staffer's highest
role sat above Moddy's, and Discord refuses to touch the roles of anybody in
that position.

Jules asked for the limit to go. It now does:

- `removable_roles()` sets aside what Discord allows and leaves the rest, rather
  than refusing the whole window;
- lending the throwaway role and setting the others aside are two separate
  requests, so a server where the second is impossible still gets the first;
- the card names the roles that stayed (`window_partial`). **The containment is
  void in that case** — the staffer keeps whatever those roles carry, and the
  position trick protects nothing. Saying so on the card was the condition for
  removing the check: a half-open box described as closed is worse than a
  refusal.

Also added `/team role_delete` (alias `t.unrole`): deletes the Moddy Team role,
forgets its stored id, refuses while a linking window is running.

## Known issues / follow-ups

- **Nothing here has been run against a live guild yet.** The tests cover the
  restore filter, the blockers and the i18n; they do not cover Discord's
  behaviour on `edit_role_positions`, nor whether the linking edit actually emits
  the `GUILD_ROLE_UPDATE` we detect success on. A fallback re-fetch at expiry
  covers a missed event, but the first real run should be watched.
- 30 s for seven clicks is tight; the delay is a constant (`WINDOW_SECONDS`).
- `STAFF_PREFIX` in `utils/staff_permissions.py` is still a hardcoded bot id, so
  text staff commands do not work on a dev instance. Untouched here.
