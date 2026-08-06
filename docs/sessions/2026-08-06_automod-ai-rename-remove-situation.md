# 2026-08-06 — Automod AI: remove the `situation` feature, rename the module

## What was done

### 1. Removed the `situation` feature (diffuse harassment)

The session-8 feature (friction state machine + `mini` sequence analyst, forced
shadow mode) was removed entirely. It never applied anything and did not earn
its complexity.

Deleted:
- `automod/situation.py`
- `utils/automod_situation_views.py`
- `tests/automod/test_situation.py`

Stripped:
- `automod/engine.py` — `friction_probe()`, `analyze_situation()`, the
  `situation` import.
- `automod/constants.py` — the whole situation/friction block and
  `CALL_TYPE_SITUATION`.
- `modules/automod_ai.py` — `SituationFeature`, its registry entry, the friction
  store / feed / trigger / sequence-collection / card helpers, the
  `features.situation` default and the verdict feed in `on_message`.
- `modules/configs/automod_ai_config.py` — the situation option in the activation
  select (now `max_values=3`), its config block and defaults.
- `utils/automod_shadow_views.py` — the `_render_for` dispatcher (shadow cards
  are the only kind now) and the situation carve-out in the precedent feeder.
- `db/base.py` — the `automod_situation` quota seeds.
- `locales/fr.json`, `locales/en-US.json` — `modules.automod.situation.*` and the
  three `config.situation_*` keys.

### 2. Renamed the module to **Automod AI** (`automod_ai`)

A classic (rule-based) automod module is coming and will take the `automod` id.

- `modules/automod.py` → `modules/automod_ai.py`; `MODULE_ID = "automod_ai"`,
  `MODULE_NAME = "Automod AI"`.
- `modules/configs/automod_config.py` → `automod_ai_config.py`
  (`AutomodConfigView` → `AutomodAIConfigView`);
  `automod_precedents_view.py` → `automod_ai_precedents_view.py`
  (`AutomodPrecedentsView` → `AutomodAIPrecedentsView`).
- i18n namespace `modules.automod.*` → `modules.automod_ai.*` (locales + call
  sites).
- `cogs/config.py` and `cogs/module_events.py` now dispatch on `automod_ai`.
- `docs/AUTOMOD.md` → `docs/AUTOMOD_AI.md` (references updated repo-wide).

The detection package stays `automod/` — it is the AI pipeline, not the module.

### 3. Config migration + cache fixes (`modules/module_manager.py`)

- `LEGACY_MODULE_IDS = {"automod": "automod_ai"}`: on the first load of a guild,
  a config stored under the old key is copied to the new id and the old key is
  emptied. `get_module_config` also falls back to a not-yet-migrated legacy key.
- `get_module_instance` now reloads a guild's modules when the cache is empty.
  Previously, a `module_updated` Pub/Sub event from the dashboard invalidated the
  cache and the modules stayed dead until the next bot restart.

### 4. Documentation

- New `docs/AUTOMOD_AI_CONFIG.md` — the config schema in DB, validation rules,
  cache invalidation, related tables and cost levers, for the backend/dashboard.
- `docs/AUTOMOD_AI.md` — §3 header (new id + migration), §4 rewritten without
  `situation`, situation tunables and the `automod_situation` quota row removed.
- `CLAUDE.md` — project structure + documentation index updated.

## Decisions

- **Migrate rather than dual-read**: the module id is stored in one JSONB key, so
  a one-shot copy at load time keeps every existing guild working while freeing
  the `automod` id immediately.
- **Kept `automod/` as the package name** to avoid churn on a package that is not
  the module and stays the AI pipeline.

## Follow-ups

- The classic automod module can now claim `MODULE_ID = "automod"`.
- The dashboard must write `data.modules.automod_ai` and drop any remaining
  `features.situation` block on its next write.

## Verification

`python -m pytest tests/automod` → 281 passed.
