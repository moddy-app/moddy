# 2026-07-12 — Automod v2 (session 2): deterministic barème & recidivism engine

Session 2 of the automod v2 track (see `docs/AUTOMOD_V2_PLAN.md`). nano now only
**qualifies** a message; a new deterministic **barème** computes the sanction.
Sanctions are 100 % reproducible, auditable and explainable line by line.

## What was done

### 1. `automod/bareme.py` (new — pure, no I/O)

The core deliverable. Given a qualified verdict (`categorie` / `gravite` /
`confiance`), the member's recidivism history and the guild config, it returns a
`ResultatBareme`: a **cran** (0–7 rung on a single ladder), the Discord
`actions` + `duree_heures`, and a `composantes` breakdown whose signed deltas sum
to the cran.

- **Ladder** (`LADDER`): 0 deletion-only → 1 warn → 2–6 mute (2h…672h) → 7 ban.
  `supprimer` is always included.
- **Floor** (`PLANCHER`): cran per (category × gravity), the cold first-offence
  policy from the plan §2.2.
- **Recidivism engine**: `points_actifs` = Σ `POINTS_GRAVITE` × exponential decay
  (half-life 45 d) × `POIDS_SOURCE` (source reliability) × `MULT_MEME_CATEGORIE`
  (1.5 for same-category repeats). `crans_recidive`: ≥5→+1, ≥15→+2, ≥40→+3.
- **Modulators** (plan §2.4 order): guild severity shift, confidence cap
  (`low`→≤1, `medium`→≤4), veteran clemency (−1, never on sensitive categories
  or gravity haute+), fresh-account malus (+1), guild `max_action` ceiling.
- **Kill-switch** (`categories_desactivees`): a category → deletion only.
- `needs_review` when cran ≥ 6 (prep for session-6 mini confirmation).

### 2. Recidivism data — `db.list_member_sanctions` (no migration)

`db/repositories/moderation.py::list_member_sanctions(guild, user, since)`
returns `{action, categorie, gravite, date, source_fiabilite}` per past guild
sanction. `categorie`/`gravite` come from the automod evidence event (else
derived from the action). **`source_fiabilite` is derived at read time** from
`case_sanctions.issued_by_type` + the latest `case_appeals` status — an accepted
appeal → `automod_appel_accepte` (weight 0), a refused one → `automod_confirme`.
`list_automod_evidence_message_ids` now excludes cases with an accepted appeal
(purges `messages_deja_moderes`). Both requirements from plan §2.5 are met
without a schema change.

### 3. nano contract cleanup

`actions` / `duree_heures` are **removed** from the nano contract (system prompt,
`parse_verdict`, `_DEFAULT_VERDICT`, `_reject`, `juger`). nano no longer decides
any punishment. `Decision.actions` stays on the dataclass but is now filled by
the barème in the module.

### 4. Module application + explainability

`modules/automod.py::_compute_bareme` loads the 180-day sanction history, the
member's server tenure and the guild config, computes the cran and **overwrites
`decision.actions` / `decision.duree_heures`**. The alert card shows a localized
breakdown (`_bareme_breakdown`) and the case evidence payload stores `cran`,
`points_recidive` and the `bareme` components for the timeline.

### 5. Config + i18n

`max_action` (warn/mute/ban), `langue_serveur` (auto/fr/en-US) and
`categories_desactivees` added to the config, validation and defaults;
`guild_locale` honours `langue_serveur`. New **Limites & langue** section in
`modules/configs/automod_config.py` (two selects). i18n keys
`modules.automod.bareme.*` and `modules.automod.config.{section_limits,max_action,language}`
added to `fr.json` + `en-US.json`.

### 6. Tests

`tests/automod/test_bareme.py` — 36 table-driven cases: ladder bounds, floor per
(cat, gravity), decay at half-life, accepted-appeal weight 0, same-category
multiplier, source ordering, recidivism escalation, severity shift, confidence
caps, veteran clemency (granted / refused on high gravity / refused with
history / refused on sensitive category), fresh-account malus, `max_action`
ceiling, kill-switch, 0–7 bounds, breakdown-sum invariant, `needs_review`.
Session-1 nano tests adapted (nano no longer carries actions). **Full suite: 162
passed.**

## Files modified

- `automod/bareme.py` (new), `automod/nano.py`, `automod/schemas.py`
- `db/repositories/moderation.py`
- `modules/automod.py`, `modules/configs/automod_config.py`
- `locales/fr.json`, `locales/en-US.json`
- `tests/automod/test_bareme.py` (new), `tests/automod/test_nano_grounding.py`
- `docs/AUTOMOD.md`, `docs/AUTOMOD_V2_PLAN.md`

## Decisions & rationale

- **Derived, not stored, `source_fiabilite`.** Deriving from issuer + appeal
  state at read time (and excluding accepted-appeal messages in SQL) matches the
  plan's effect with **zero migration** and no new AppealService code — the
  service already records the appeal status.
- **Fallback gravity** for manual sanctions without automod evidence
  (`warn→basse`, `mute→moyenne`, `ban→haute`) so the barème always has a gravity
  to weigh.
- **Breakdown honesty**: a `borne` component is recorded whenever the final 0–7
  clamp bites, so the displayed deltas always sum to the cran.

## Known follow-ups

- Session 3 (regression harness + shadow mode) can feed on the barème's
  breakdown / components already emitted here.
- `needs_review` is only a card highlight today; session 6 wires the mini
  confirmation for cran ≥ 6.
- Transform-appealed sanctions currently keep weight `automod` (only
  accept/refuse are special-cased) — revisit if it proves noisy.
