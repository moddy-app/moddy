# Module "Manage" panel (state + delete configuration out of the home container)

## What was done
- The generic state row (state line + Pause / Resume / Simulation buttons) is gone
  from every module home container. A small secondary **Manage** button
  (`module_manage_button()`) sits in the home's bottom button row, outside the
  container, once a config is saved (hidden while Save / Cancel edits are pending).
- It opens the shared `ModuleManageView` (`modules/configs/_common.py`): the state
  line, a select Active / Simulation / Paused with one sentence per option (replaces
  the Pause / Resume buttons, judged unintuitive), and "Delete configuration" with an
  in-place confirmation, then Back to the module home.
- Every control is a `DynamicItem` carrying the module id
  (`moddy:config:manage:<module>:open|back|delete|cancel|confirm`,
  `moddy:config:manage:<module>:state`), registered by `ModuleStatePersistence`;
  Manage Server re-checked on every click. `ModuleStateButton` / `module_state_row`
  removed.
- `ModuleBase.CONFIG_DELETABLE` (default False) + classmethod hook
  `before_config_delete(bot, guild_id)`. Enabled on the modules that already had a
  delete button: adaptive_slowmode, altguard (hook: take the panel down),
  auto_restore_roles, auto_role, automod_ai (hook: clear drafts), interserver,
  logs (its "Clear everything" button moved here), member_applications, starboard,
  voice_transcription. Their own delete buttons / `on_delete` / `AutomodDeleteView`
  are removed.
- i18n: `modules.config.manage.*` in the 5 locales; removed `state.buttons`,
  `state.changed`, `logs.config.clear`, `automod_ai.panel.delete`.

## Decisions
- Deletion stays off for tickets, welcome channel/DM, social notifications, bump
  reminder: their config owns things outside the stored row (posted panels, feed
  subscriptions, scheduled reminders); turning it on needs a `before_config_delete`.
- Bot customization keeps its own "reset" button (it resets the bot's identity on
  Discord, not just the stored row); its Manage panel offers the state only.

## Files
`modules/configs/_common.py`, every `modules/configs/*_config.py` with a home screen,
`modules/module_manager.py`, the 10 deletable module classes, `utils/persistent_views.py`,
`cogs/config.py`, locales, tests (`test_module_state.py`, `test_config_standard.py`,
`test_automod_ai_panel.py`), docs (MODULE_SYSTEM, DESIGN, PERSISTENT_VIEWS, AUTOMOD,
LOGS, VOICE_TRANSCRIPTION, SOCIAL_NOTIFICATIONS).

## Follow-ups
- Decide whether the modules listed above should get deletion (with cleanup hooks).
