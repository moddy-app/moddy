# 2026-10-29 — `t.role`: 50013 when opening the window (server with only Moddy's role)

## Symptom (production logs)
`Could not open the linking window in guild 1543442026769678459 — HTTP 400
(Discord code 50013): Missing Permissions`, although Moddy is Administrator
(Administrator does not bypass the role hierarchy).

## What changed
- The reorder is computed by `bottom_positions()` from a **fresh
  `guild.fetch_roles()`**, not the cache (two roles were just created), and
  renumbers **every** role below Moddy's top role (roles to link, throwaway
  role, then the rest in their order), so the payload never asks for a slot
  level with or above Moddy's own.
- The error log now names the failing **step** (create / read / reorder /
  lend), Moddy's top-role position and the positions sent.
- Tests: `tests/test_linked_roles.py::TestBottomPositions`.

## Not confirmed
The root cause is not proven. If Discord leaves duplicate positions after a
role creation (Moddy's role not shifted up), the payload can still be
refused; the new log line will show it (`Moddy top role at N` ≤ highest
position sent). Next step then: ask an admin to drag Moddy's role up once,
or handle it in code.

## Follow-up: Moddy Team role already there
Re-running `t.role` with the Moddy Team role already created recreated the
throwaway role but never lent it. Same failure: the reorder was refused.
`bottom_positions` now raises `HierarchyCrowded` when Moddy's role sits too
low to number the roles under it (`len(order) >= top.position`), before
anything is sent. The window ends with the new `crowded` outcome, the
throwaway role is deleted, and the card tells the staffer to drag Moddy's role
up once. The bot cannot do that itself: it may never move its own top role.
