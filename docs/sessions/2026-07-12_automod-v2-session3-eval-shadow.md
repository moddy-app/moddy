# Automod v2 — Session 3: regression harness & shadow mode

**Date:** 2026-07-12
**Branch:** `claude/automod-v2-session-3-bojpf2` (based on `AUTOMOD_V2`)
**Plan:** `docs/AUTOMOD_V2_PLAN.md` § SESSION 3

## Goal

Make automod changes provable ("does this move the problem or fix it?") and let a
server run the system with zero risk while it calibrates. Two deliverables: an
**offline regression harness** over a committed golden set, and a **shadow mode**
that simulates sanctions and collects human annotations.

## What was done

### 1. Golden set + fixtures (`automod/eval/`)
- `golden.jsonl` — **62** labelled cases (≥60 required): the 8 calibrated
  few-shots, real false positives ("je suis con", "arrête stp", quotes, lyrics,
  casual swearing), true positives across every category, prompt-injection
  attempts, English messages, self-harm high-bar cases. Each line: `attendu`
  (`sanctionnable` + optional `categorie`/`gravite_min`), `tags` (incl.
  `faux_positif_reel`), `origine`.
- `fixtures.json` — recorded model outputs (embedding score + nano raw verdict)
  per case id, so `--replay` reproduces a run **offline, free, deterministic**.
  Generated consistently with the real routing (prefilter/trivial/blocklist).
- `golden_baseline.json` — committed baseline (precision/recall 1.0 on the seed).

### 2. Offline runner (`automod/eval/run.py`)
- Replays the whole funnel (prefilter → trivial → blocklist → embedding → nano →
  **grounding** → barème). Reports precision/recall/F1, per-category recall, a
  confusion matrix and the cases that changed vs the baseline.
- `--replay` (default, CI) vs `--live` (real `bot.gateway`, `--update-fixtures`).
  Decides only — never applies a sanction, never writes to the DB.
- **CI gate:** exits non-zero iff a `faux_positif_reel` case becomes sanctionnable
  again. Weakening `validate_grounding` resurrects 7 known false positives and
  turns CI red (asserted in tests).
- `Makefile` targets: `test`, `eval`, `eval-baseline`, `eval-live`, `eval-import`.

### 3. Shadow mode (`dry_run`)
- Config `dry_run` (module + `/config` Options toggle "Mode simulation").
- `modules/automod.py::_notify_shadow` short-circuits application (no
  delete/sanction/case/DM) and posts a **SIMULATION** card with the would-be
  sanction + barème breakdown.
- `utils/automod_shadow_views.py` — the card + three **persistent** annotation
  buttons (✅ Correct / ❌ Faux positif / ⚠️ Disproportionné) as `DynamicItem`s
  (registered in `utils/persistent_views.py`). A moderator's click records the
  ruling and re-renders the card; buttons survive a restart (candidate id in the
  `custom_id`, card rebuilt from the DB row).

### 4. Annotation corpus (`automod_eval_candidates`)
- New table (`db/base.py`) + repository (`db/repositories/eval_candidates.py`):
  message, context, verdict, cran + barème, and the moderator's ruling.
- `automod/eval/import_candidates.py` (`make eval-import`) turns annotated
  candidates into golden-shaped JSONL for manual review.

### 5. i18n + tests + docs
- i18n `modules.automod.shadow.*` + `config.dry_run.*` (fr + en-US).
- `tests/automod/test_eval_harness.py` — 13 tests (corpus size/schema, fixture
  consistency, clean replay == baseline, the CI gate, grounding-caught cases,
  true-positive round-trip). **Full suite: 175 passed.**
- `docs/AUTOMOD.md` §8 "Évaluation" (+ config/apply notes); plan tracking table
  + session-3 journal; `CLAUDE.md` structure.

## Files modified / added

- **Added:** `automod/eval/{__init__,run,import_candidates}.py`,
  `automod/eval/{golden.jsonl,fixtures.json,golden_baseline.json}`,
  `db/repositories/eval_candidates.py`, `utils/automod_shadow_views.py`,
  `tests/automod/test_eval_harness.py`, `Makefile`,
  `docs/sessions/2026-07-12_automod-v2-session3-eval-shadow.md`.
- **Modified:** `modules/automod.py`, `modules/configs/automod_config.py`,
  `utils/automod_render.py` (shared barème-breakdown renderer),
  `utils/persistent_views.py`, `db/base.py` (table + repo mixin),
  `locales/fr.json`, `locales/en-US.json`, `docs/AUTOMOD.md`,
  `docs/AUTOMOD_V2_PLAN.md`, `CLAUDE.md`.

## Decisions & rationale

- **Replay from recorded fixtures** (not live) keeps CI free/deterministic while
  still exercising the deterministic layers the harness protects (grounding,
  category normalisation, barème). Prompt changes are re-measured with `--live`.
- **Narrow, loud CI gate**: only a `faux_positif_reel` regression breaks the
  build (plan §3.2); other verdict changes are reported (baseline diff), and
  `make eval-baseline` acknowledges an intentional improvement.
- **Hallucination fixtures** on synthetic `faux_positif_reel` cases: the recorded
  nano verdict *would* sanction (absent citation / incoherent target / speculative
  reason) and the deterministic grounding guard must void it — the test proves
  disabling the guard trips the gate.
- **Annotation buttons are persistent `DynamicItem`s** — the card is rebuilt from
  the DB row after a restart (repo persistent-view contract).
- No new gateway call types and **zero runtime cost** (the harness is offline;
  `--live` reuses the existing `automod_embed`/`automod_decision`).

## Known follow-ups (Session 4+)

- The `automod_eval_candidates` corpus is the raw material for per-server
  precedents (Session 7): the detection-time embedding can be stored/reused.
- Session 4 (cost & anti-fragmentation) can now measure each optimisation against
  this harness without regressing known false positives.
