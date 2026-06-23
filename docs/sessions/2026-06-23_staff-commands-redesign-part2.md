# 2026-06-23 — Staff commands redesign (part 2: mod, manage, recategorization)

Continuation of the staff command framework rollout. Migrated the remaining
groups and recategorized the platform/billing commands out of `/dev`.

## What was done
- **Framework**: added slash **sub-group** support (`StaffCommand.group`) so
  commands can live under `/mod case create` (message `mod.case create`); a
  reusable `ConfirmView` for destructive actions; `is_migrated()` so legacy
  cogs defer migrated commands; `_has_node` now bypasses for Managers.
- **`/mod`**: `interserver_info`, `interserver_delete` (confirmed), and the
  `case` sub-group (`create/view/list/edit/close/note`). **Fixed** the
  previously broken `edit/close/note` (a message command couldn't open a modal)
  via the framework's `open_modal`.
- **`/manage`** (replaces `staff_manager.py`): a unified **staff** panel merging
  `rank` + `setstaff` (roles + granular permissions + add/remove), plus
  `staffinfo`, `list`, `unrank`, `badge`, `subrefresh`, and the `redirect` and
  `banner` sub-groups.
- **Recategorization** (per request — Stripe/ops aren't dev): `redirect` →
  `/manage redirect` (`redirect_manage`), `banner` → `/manage banner`
  (`banner_manage`), `sub-refresh` → `/manage subrefresh` (`stripe_manage`).
  Removed their routing from the legacy dev cog.
- **Modals V2**: banner create/edit rebuilt with a `Select` (type) and a
  `CheckboxGroup` (visibility) instead of the old text-field hacks; redirect
  modal rebuilt with `Label`-wrapped inputs.
- **i18n**: message commands now always render in English (no per-user language
  signal); slash uses `interaction.locale`. Added `staff.mod.*`,
  `staff.manage.*` keys (FR + EN, audited).

## Validation
- All four groups (`/dev` 15, `/team` 6, `/mod` 8, `/manage` 17) build and
  **serialize to valid Discord payloads** against discord.py 2.7.1, including
  mixed flat + sub-group commands. Banner modals build (Select + CheckboxGroup).
- Every referenced `staff.*` i18n key exists in both locales.

## Follow-ups
- Rebuild `t.help` as a framework-aware help once all groups are migrated.
- Remove the now-empty `staff/dev_commands.py` (dead handlers/modals) in a
  cleanup pass.
- Optional: localize slash command *descriptions* via an `app_commands.Translator`.
