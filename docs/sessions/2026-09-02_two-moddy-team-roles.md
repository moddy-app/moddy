# 2026-09-02 — Two Moddy Team roles, and one of them by default

## What was asked

The linked-role system doubles: **Moddy Team** stays, **Moddy Team Manager**
joins it. Two constraints came mid-session and shaped the result more than the
split itself:

- **`/team ticket` must carry both roles**, not just the base one.
- **It must still work with one role, and one role is the default.** A server
  takes the second later — "et après on peut repartir sur 2 rôles".

The metadata key is `manager` (not `team_manager`, which the first draft
assumed).

## The shape

`utils/moddy_team_role.py` grew a `TeamRoleKind` — key, name, stored path,
metadata key — and two instances, `TEAM` and `MANAGER`. Every helper takes
`kind=TEAM` by default, so the callers that only ever mean the base role
(`/team see`, `ticket_service`) were not touched at all.

| | Moddy Team | Moddy Team Manager |
|---|---|---|
| metadata (backend) | `team` | `manager` |
| stored id | `moddy_team.role_id` | `moddy_team.manager_role_id` |

A manager holds **both**: `team` stays true for them, so nothing granted to the
base role has to be granted twice.

**The bot still publishes nothing to Discord.** `manager` is computed by the
backend from `staff_permissions` — the same table `team` comes from — so the
existing `moddy:staff` message already triggers its recomputation. No new
channel, no new payload, no bot-side change. The key appears in this repo only
as documentation and as a line on the `/team role` card.

## Scope, everywhere

`t.role`, `t.unrole` and `t.access` take the same word: `team` (default),
`manager`, `both`. The message form reads it in either order — a scope is a word
from a three-item list and a guild id is digits, so neither can be mistaken for
the other.

## The window, once instead of twice

`services/team_link_session.py` now tracks a *list* of roles. One window covers
every role that still needs a requirement, because a window per role would mean
stripping the same staffer of their roles twice in a row. It resolves on the
**last** requirement to appear, and gained a `partial` outcome for the case
where one landed and the other did not — running the command again picks up only
what is missing.

`WINDOW_SECONDS`: 30 → 75. Seven clicks was already tight for thirty seconds;
there can be fourteen now.

## Files

| File | Change |
|---|---|
| `utils/moddy_team_role.py` | `TeamRoleKind`, `TEAM`/`MANAGER`/`KINDS`, `kind_from_key`; every helper takes `kind=` |
| `services/team_link_session.py` | list of roles, `pending`/`linked`, `PARTIAL`, `WindowResult`, `id_set`, positions/teardown/recovery generalised, 75 s |
| `staff/commands/team/team_role.py` | `roles` scope, one state block per role, requirement list per role |
| `staff/commands/team/role_delete.py` | same scope; each id forgotten right after its own deletion |
| `staff/commands/team/access.py` | `role` option (`team`/`manager`) |
| `utils/team_access_views.py` | the role travels in the custom_ids, as an **optional** segment |
| `services/ticket_service.py` | a staff ticket grants `admin` to **both** roles when both exist |
| `staff/base.py` | response **list** per command, tracking from dispatch, deletion after the fact, bounded map |
| `staff/framework/cog.py`, `staff/{support,communication}_commands.py` | `begin_command()` at dispatch |
| `tests/test_linked_roles.py` | 82 tests (was 52) |
| `tests/test_staff_response_tracking.py` | **new** — 9 tests on the deletion contract |
| `locales/*.json` (×5) | `metadata`, `scope_hint`, `window_partial` as an outcome, `window_kept_roles`; `{role}`→`{roles}`; `state` dropped |
| `docs/LINKED_ROLES.md`, `docs/STAFF_SYSTEM.md`, `CLAUDE.md` | rewritten around the two roles |

## Decisions and why

- **One role is the default, and that is a decision, not an omission.** Creating
  a manager role in every server that ever needed support hands out a
  distinction nobody asked for. `manager`/`both` are opt-ins, and the two roles
  are independent, so coming back for the second is the normal path.
- **The name lookup stays an exact match, and the tests say why.**
  `Moddy Team Manager` contains `Moddy Team`. A `startswith` would resolve the
  base role to the manager role in exactly the servers that have both, and
  `/team access` would then grant the team's permissions to the wrong one. A
  role already stored as the other kind is skipped for the same reason.
- **The custom_id role segment is optional.** A `/team access` card posted
  before this deploy carries no third field; it matches, and means the base
  role. Buttons that quietly stop responding after a deploy are not an
  acceptable way to ship an option.
- **Recovery reads both stored shapes.** A window interrupted by the deploy
  itself stored a single `team_role_id`; one interrupted after stores
  `team_role_ids`. Reading only the new key would leave that staffer stripped of
  every role for good — the one outcome this feature must never produce.
- **`partial` is not `expired`.** Telling a staffer who linked one role out of
  two that nothing happened would send them through the whole window again for
  work already done.
- **A cancelled window stays cancelled**, whatever got linked: the staffer did
  something they should not have, and that is what the card has to say.
- **Both roles on a staff ticket** (Jules, mid-session). It changes nothing for
  a manager, who holds the base role too — what it actually covers is a server
  that only ever created the manager role, where granting the base role alone
  would open the channel to nobody.
- **`/team see` was left on the base role.** It opens one channel to the team;
  there is no version of that which wants to be manager-only, and the base role
  is what every staffer holds.

## Then: deleting a staff command left messages behind

Reported mid-session. `staff/base.py` mapped **one** response id per command,
so a command that answers twice — `t.role` sends the window card, then the
report — lost the first one on the second reply, and the card stayed in the
channel for good. Two more holes came out of looking at it:

- a reply landing **after** the deletion (the report arrives up to
  `WINDOW_SECONDS` later) was never covered: the deletion had been handled and
  forgotten by then;
- nothing was ever removed from the map, which grew for the life of the process.

Now: responses are a list, a command is tracked from dispatch (`begin_command`,
wired into the router and both legacy cogs) rather than from its first reply,
a deleted command is remembered so anything still to come is sent and removed
again, and the map is bounded at 500, oldest first. `ctx.send` returns `None`
in that case — the three callers that edit their message already guarded on it.

`tests/test_staff_response_tracking.py` covers the three cases plus the bounds.

## Then: the cards said too much

"pas besoin de mettre tous les tuto, on est pas con." The `/team role` cards
were written for somebody who has never seen a linked role; they are read by
the people who built them.

Gone: `howto_title` (a heading over two lines), `hint` ("run it again to
check"), `scope_hint` (a usage reminder next to the command that was just run),
`window_rules` (a paragraph explaining the borrowed permission) and
`role_delete.hint`. Kept and cut to one line each: the click path — genuinely
several menus deep — the role → requirement pairs, and the reason a window did
not get there. `window_note` replaces the paragraph with the only two facts
that matter while the clock runs: the roles come back, anything else is undone.

## Known issues / follow-ups

- **The backend does not publish `manager` yet.** Until it does, a linked
  manager role exists and stays empty — no error anywhere, just nobody in it.
  The contract is written out in `docs/LINKED_ROLES.md` → *The `manager`
  metadata*; who exactly is a manager (Dev + Manager, or Supervisors too) is the
  backend's to compute and was not fixed here.
- **Still nothing run against a live guild**, same as the previous session: the
  tests cover the bookkeeping, not Discord's behaviour on `edit_role_positions`
  with three roles, nor whether two `GUILD_ROLE_UPDATE`s arrive as expected.
- 75 s for fourteen clicks is still tight; `WINDOW_SECONDS` is a constant.
- `STAFF_PREFIX` in `utils/staff_permissions.py` is still a hardcoded bot id, so
  text staff commands do not work on a dev instance. Untouched again.
