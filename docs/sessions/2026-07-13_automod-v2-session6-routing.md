# Session — Automod v2 · Session 6 : routing par difficulté (nano → mini)

**Date :** 2026-07-13
**Branche :** `claude/automod-v2-session-6-j4s4a2` (basée sur `AUTOMOD_V2`)
**Plan :** `docs/AUTOMOD_V2_PLAN.md` — SESSION 6

## Objectif

Mettre l'intelligence chère uniquement là où elle sert : router **avant** de juger
(cas ambigus → `gpt-4.1-mini`, cas évidents → `gpt-4.1-nano`), et rendre
**impossible** l'application d'une sanction lourde décidée par nano sans une
confirmation binaire par mini.

## Ce qui a été fait

### Router de difficulté (pur) — `automod/routing.py`
- `difficulte(contenu, signal, relation, severity) -> "evident" | "ambigu"`,
  **gratuit** (aucun appel IA). `ambigu` si : regex non flagrant + ≤ 3 mots,
  `familiarite in (haute, moyenne)`, marqueur de rire, ou score embedding en zone
  grise (`|score − seuil| ≤ 0.05`). Un regex flagrant (`score ≥ seuil + 0.15`)
  court-circuite en `evident`.
- Helpers `is_ambigu`, `contexte_initial_for` (contexte ×2 pour `ambigu`).

### Routage & confirmation — `automod/engine.py`, `automod/nano.py`
- `_decide` calcule `niveau` (après la relation) ; `_judge` sélectionne
  modèle / contexte / `decideur` et `_make_chat_fn(model, call_type, max_tokens)`
  est paramétré (nano / mini / confirmation).
- Budget guard étendu : `_budget_increment(weight)` via `incrby`, un appel mini
  pèse ×4 (`MINI_BUDGET_WEIGHT`).
- `engine.confirm_heavy(...)` → `nano.confirmer(...)` : revue senior binaire
  (`{"confirme", "motif"}`), **fail-safe** (échec/malformé ⇒ refus), comptée ×4,
  **non budget-gated** (les crans ≥ 6 sont rares → correction > économie).
- `Decision.decideur` (`"nano"`/`"mini"`) + `confiance_calibree` (interface
  annexe A.2, `None` tant que le gateway n'expose pas les logprobs).

### Barème & module — `automod/bareme.py`, `modules/automod.py`
- `bareme.appliquer_non_confirme(res)` : plafonne un cran non confirmé à
  `CONFIRM_UNCONFIRMED_CRAN` (= 4, mute 48 h, **jamais de ban**) + composante
  `confirmation_refusee`, `needs_review` conservé.
- `_maybe_confirm_heavy` (module) appelé après le barème et **avant** le
  short-circuit `dry_run` (simulation fidèle) : si `cran ≥ 6` et
  `decideur == "nano"`, confirme via l'engine ; refus ⇒ downgrade. Carte : hint
  `review_hint_unconfirmed`.

### Constantes, i18n, gateway, harness
- `constants` : `MINI_MODEL`, `CALL_TYPE_DECISION_MINI`, `CALL_TYPE_CONFIRM`,
  `MINI_BUDGET_WEIGHT`, `ROUTING_*`, `AMBIGU_CONTEXT_MULTIPLIER`, `CONFIRM_*`,
  et **marqueurs de rire centralisés** `RIRE_MOTS`/`RIRE_EMOJIS`/`RIRE_MARQUEURS`
  (source unique, annexe A.3). `normalize.has_laughter` (détecteur unique, réutilisé
  par `relations.py`).
- `db/base.py` : seed `automod_decision_mini` + `automod_confirm` (guild + global).
- i18n `modules.automod.bareme.{unconfirmed, review_hint_unconfirmed}` (fr + en-US).
- `automod/eval/run.py` : `CaseResult.difficulte` reporté par cas (hors baseline)
  + synthèse routing dans le rapport → le runner exerce le router hors-ligne.

## Fichiers modifiés / créés
- **Créés** : `automod/routing.py`, `tests/automod/test_routing.py`,
  `tests/automod/test_confirmation.py`, ce log.
- **Modifiés** : `automod/constants.py`, `automod/normalize.py`,
  `automod/relations.py`, `automod/schemas.py`, `automod/nano.py`,
  `automod/engine.py`, `automod/bareme.py`, `automod/eval/run.py`,
  `modules/automod.py`, `db/base.py`, `locales/fr.json`, `locales/en-US.json`,
  `tests/automod/_redis_stub.py`, `tests/automod/test_eval_harness.py`,
  `docs/AUTOMOD.md`, `docs/AUTOMOD_V2_PLAN.md`, `CLAUDE.md`.

## Décisions
- **Séparation détection/application respectée** : le router est pur ; le choix
  de modèle est exécuté par l'engine (qui dépense les appels) ; la confirmation
  est *déclenchée* par le module mais l'appel IA vit dans l'engine.
- **Aucun appel IA pour router** (heuristiques + golden set) ; le pré-call nano
  3-tokens reste un TODO.
- **« Cran ≥ 6 sans confirmation impossible »** est mécanique : `appliquer_non_confirme`
  ne produit qu'un mute 48 h (test `"ban" not in actions`).
- **Gain S6 offline = observabilité** : en `--replay` les verdicts viennent des
  fixtures, donc le routage ne change aucun résultat (baseline inchangée) ; il se
  mesure vraiment en `--live`.

## Tests
- `tests/automod/test_routing.py` (14), `tests/automod/test_confirmation.py` (20),
  routing dans `test_eval_harness.py` (3), `_redis_stub.py` += `incrby`.
- **266 passés** (`pytest tests/automod/`), runner `--replay` toujours **1.0/1.0**,
  aucune régression `faux_positif_reel`.

## Suites / follow-ups
- **S7** : précédents serveur (RAG) réutilisant l'embedding déjà calculé.
- **S8** : feature `situation` (harcèlement diffus) branchée sur mini (S6) en shadow.
- **Annexe A.2** : brancher `confiance_calibree` si le gateway expose un jour les
  logprobs (interface déjà posée).
