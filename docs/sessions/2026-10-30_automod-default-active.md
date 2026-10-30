# 2026-10-30 — Automod: active by default (not simulation)

## What was done
- The automod first-run wizard now defaults to **active** instead of simulation:
  - **Skip** (every wizard step) applies *Balanced* with `state: "active"` (`SKIP_STATE`).
  - Step 3 lists "Enable now" first, marked *recommended* (green button);
    "Start in simulation" is the secondary choice.
  - `automod.presets.preset_config` defaults to `state="active"`.
- i18n (5 locales): `wizard.mode_question` and `wizard.skip_hint` reworded.

## Files modified
- `modules/configs/automod_ai_config.py`, `automod/presets.py`
- `locales/{fr,en-US,es-ES,pt-BR,de}.json`
- `tests/test_automod_ai_panel.py`
- `docs/features/AUTOMOD.md`, `docs/integration/AUTOMOD_CONFIG.md`

## Decisions
- Safe without an alerts channel: the module does not run until
  `notify_channel_id` is set (`AutomodModule.load_config`), so Skip without a
  channel still cannot sanction anyone.
- Existing servers are untouched (only fresh configurations are affected).
- A dashboard reproducing the wizard should follow the same default.
