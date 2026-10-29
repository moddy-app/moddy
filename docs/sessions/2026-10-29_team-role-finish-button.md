# 2026-10-29 — `t.role`: window on every run, Finish button, 20 min cap

## What was done
- `t.role` now **always** opens the linking window and lends the throwaway
  role "Moddy Team — linking", even when the roles already carry their
  requirement (to check or redo it). Already-linked roles are simply not
  "pending".
- The owner is no longer refused (`owner` blocker removed): they get the
  throwaway role; their own roles are not set aside (`removable_roles` → []).
- No more 75 s countdown: the card carries a **Finish** button
  (`FinishWindowButton`, persistent DynamicItem
  `moddy:teamrole:finish:<guild>:<staff>`, only that staffer). The window
  closes on Finish, on an action outside the linking, or after 20 minutes
  (`WINDOW_SECONDS = 1200`). Linking a role no longer closes it.
- New outcome `finished` (Finish pressed with a role still unlinked). After
  the window the card is edited to "Window closed" (button gone), then the
  report is posted as before.
- i18n updated in 5 locales; `blocked_owner` removed.
- Docs: `LINKED_ROLES.md` (window, outcomes, a trap about Moddy missing from
  the requirement picker). Tests in `tests/test_linked_roles.py`.

## Decision
- Auto-close on linking was removed so the staffer controls the end. Trade-off:
  `Manage Roles` stays lent longer (up to 20 min) — the user asked for it.

## Open question (not a bot bug)
- The staffer saw only Discord's default connections in *Links → Add
  requirement*, not Moddy. Most likely the app's role-connection metadata
  schema or its Linked Roles Verification URL is missing — backend / developer
  portal side. Not verified from here.
