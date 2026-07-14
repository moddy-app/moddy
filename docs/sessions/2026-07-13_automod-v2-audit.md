# 2026-07-13 — Automod v2 audit (sessions 1–8 consistency pass)

## Goal

Audit the eight Automod v2 sessions (`docs/AUTOMOD_V2_PLAN.md`) end to end:
verify each session is actually implemented, documented, and coherent across the
detection pipeline (`automod/`), the `/config` UI, and the moderation-cases
system (`docs/MODERATION_CASES.md`). Fix any inconsistency or bug found.

## What was verified (all green)

- **Detection pipeline (`automod/`)** — every S1–S8 deliverable file is present
  (`bareme`, `eval/`, `relations`, `routing`, `precedents`, `situation`, …).
  `pytest tests/automod` → **314 passed**; `python -m automod.eval.run --replay`
  → **precision/recall/F1 = 1.000** over 72 golden cases, exit 0, no baseline
  drift.
- **Recidivism plumbing (S2)** — `db.list_member_sanctions` derives
  `source_fiabilite` from issuer + appeal status; keys match `bareme.POIDS_SOURCE`
  (`.get()`-guarded, no KeyError). Accepted appeals drop the message from
  `messages_deja_moderes`.
- **Precedents (S7)** — `bot.precedents` wired in `bot.py`; fed from
  `AppealService._feed_precedent` (accept→non_sanctionnable, refuse→sanctionnable,
  transform→skipped) and from shadow `✅/❌` buttons; situation annotations are
  correctly **not** fed as precedents.
- **Persistent views** — shadow + situation cards share
  `ShadowAnnotateButton` (`DynamicItem`), registered via
  `ShadowAnnotationPersistence` in `utils/persistent_views.py`. The `/config`
  panel is intentionally non-persistent (standard module pattern). Compliant with
  CLAUDE.md §8.
- **`/config` UI** — all S1–S8 knobs surfaced (dry_run, situation toggle,
  max_action, langue_serveur, severity, precedents section + browser). Follows
  DESIGN.md (Components V2, custom emojis, i18n, working-copy Save/Cancel).

## Problems found & fixed

1. **Data-loss bug — `categories_desactivees` wiped on Save**
   (`modules/configs/automod_config.py`). `_deep_default` rebuilt the config
   without carrying `categories_desactivees`, and `save_module_config` →
   `update_guild_data` **replaces** the whole `modules.automod` subtree. So any
   ops/backend-set kill-switch list was silently dropped the first time an admin
   saved the panel. Fixed by preserving the key through the working copy (no UI
   selector added — it stays an ops/backend field, as documented).

2. **Outdated doc — `MODERATION_CASES.md` §9** said "nano may return
   `duree_heures`". Since S2 the **barème** computes the sanction and its
   duration; nano only qualifies. Corrected.

3. **Missing coupling doc — `MODERATION_CASES.md` §9**. Added an "appeal outcomes
   feed the automod back" note documenting the S2 recidivism weighting
   (`source_fiabilite` derivation) and the S7 precedent feeding from appeal
   rulings.

4. **Plan checkboxes — `AUTOMOD_V2_PLAN.md`** Session 8 "Critères de fin" were
   left unchecked though the session is complete (and tests prove it). Ticked.

5. **`CLAUDE.md` structure drift** — `utils/automod_shadow_views.py` was listed
   twice; `utils/cases_views.py` and `utils/automod_render.py` were missing.
   De-duplicated and added.

## Files modified

- `modules/configs/automod_config.py` — preserve `categories_desactivees`.
- `docs/MODERATION_CASES.md` — barème duration wording + appeal→automod coupling.
- `docs/AUTOMOD_V2_PLAN.md` — S8 completion checkboxes.
- `CLAUDE.md` — utils structure fixes.
- `docs/sessions/2026-07-13_automod-v2-audit.md` — this log.

## Verification

`python -m py_compile modules/configs/automod_config.py` OK · 314 tests green ·
eval runner 1.000/1.000, exit 0, no baseline drift — after the changes.

## Follow-ups (not blocking)

- `categories_desactivees` has no `/config` selector yet (ops/backend-only). A
  category multi-select could be added later if servers need self-service.
