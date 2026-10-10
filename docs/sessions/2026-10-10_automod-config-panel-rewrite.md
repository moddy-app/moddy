# 2026-10-10 — Automod AI /config panel rewrite (Lot D)

## What was done
Complete rewrite of `/config` → Automod AI around instant save and one mandatory
setting (the alerts channel): first-run wizard (channel → level → simulation),
home with status + summary + menu, What is monitored, Sanctions (list + one
type + custom infractions, Modals V2), AI guidance, Exemptions, Advanced,
persistent precedents browser.

## Files
- New: `automod/presets.py`, `modules/configs/automod_ai_common.py`,
  `automod_ai_sanctions_config.py`, `automod_ai_advanced_config.py`,
  `tests/test_automod_ai_panel.py`.
- Rewritten: `modules/configs/automod_ai_config.py`,
  `automod_ai_precedents_view.py`.
- Touched: `cogs/config.py` (entry), `utils/persistent_views.py`,
  `utils/emojis.py` (LOG_EMOJIS exception comment), locales (new
  `modules.automod_ai.panel.*`, old `modules.automod_ai.config.*` removed),
  docs, two existing tests (`_deep_default` -> `normalize_cfg`).

## Decisions
- Instant save through `save_module_config` (it validates); no working copy.
- Alerts channel on Home (mandatory, first thing looked for).
- Legacy `categories_desactivees` folded into `sanctions[].enabled` on load,
  never written back.
- A level is applied switched off + in simulation; step 3 enables it.
- Old panel custom_ids (`moddy:automod:config:*`) are not kept: an old message
  simply stops answering; reopening `/config` fixes it.

## Follow-ups
- Old messages still showing the previous panel get "This interaction failed"
  until `/config` is reopened.
