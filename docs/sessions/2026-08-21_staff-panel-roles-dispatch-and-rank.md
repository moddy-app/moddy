# 2026-08-21 — `/manage staff` roles dropdown dispatch fix + `/manage rank`

## What was done

### 1. `m.staff` roles dropdown returned "This interaction failed"

Root cause: `StaffPanelRolesSelect`, `StaffPanelScopeSelect` and
`StaffPanelActionButton` were all declared with the **same** `DynamicItem`
template (`_CID_TEMPLATE`, an `(?P<action>roles|scope|save|remove)`
alternation). discord.py stores dynamic items in a dict keyed by the
*compiled* pattern, and `re.compile` caches — so the three classes collapsed
into one registry entry. The last one registered (`StaffPanelActionButton`)
shadowed the other two, and every roles/scope select click was dispatched to
a `ui.Button` item, blowing up before the callback ran.

Fix: one non-overlapping template per class —
`_CID_ROLES_TEMPLATE`, `_CID_SCOPE_TEMPLATE`, `_CID_ACTION_TEMPLATE`
(`save|remove` only), `_CID_PERMS_TEMPLATE` unchanged. The emitted
`custom_id`s are unchanged, so panels already posted in Discord keep working.

### 2. `m.rank` is a real command again

`rank` was only an alias of the `/manage staff` panel, so the documented
`m.rank @user Manager` form silently ignored the role argument. Added
`staff/commands/manage/rank.py`: `/manage rank <user> <role>` (slash choices)
and `m.rank <@user|user_id> <role>` (role name accepted as the stored value,
the display name, or a loose case/spacing variant). It reuses the panel's
gates — hierarchy check on existing staff, `can_assign_role`, bot targets
rejected — then `db.add_staff_role()` (which also sets the `TEAM` attribute).
`rank` was removed from `StaffPanelCommand.aliases` so the registry routes it
to the new command (`setstaff` still opens the panel).

## Files modified

- `staff/commands/manage/staff.py` — split templates, drop the `rank` alias
- `staff/commands/manage/rank.py` — **new**
- `locales/{fr,en-US,es-ES,pt-BR,de}.json` — `staff.manage.rank.*`
- `tests/test_persistent_views.py` — `test_dynamic_item_templates_do_not_overlap`
- `docs/PERSISTENT_VIEWS.md` — non-overlapping template rule
- `docs/STAFF_SYSTEM.md` — `/manage rank` row + implementation files

## Decisions

- `rank` **adds** a role to whatever the member already has rather than
  replacing the set; replacing is what the panel is for, and `m.unrank`
  already covers full removal.
- No `locales/commands/*.json` entries: staff commands are deliberately not
  localized (see docs/COMMAND_LOCALIZATION.md).

## Follow-ups

- The panel still applies role/permission changes immediately (Save is only a
  confirmation) — unchanged by this session, still flagged in
  docs/PERSISTENT_VIEWS.md Step 15.
