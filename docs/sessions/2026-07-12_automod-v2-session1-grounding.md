# 2026-07-12 — Automod v2, Session 1: Grounding & verdict contract v2

Part of the multi-session automod overhaul tracked in
[`docs/AUTOMOD_V2_PLAN.md`](../AUTOMOD_V2_PLAN.md). This is **Session 1** of 8.

## Goal

Make a hallucinated verdict *mechanically impossible* (e.g. sanctioning
"connard" when the message actually says "je suis con"), and replace the
abstract rule prose in nano's prompt with contrasted few-shot examples.

## What was done

- **New verdict contract (v2).** nano now returns a verbatim `citation` and a
  `cible` (`membre` / `auteur_lui_meme` / `groupe` / `aucune`). It *qualifies*
  the message; the sanction ladder itself moves to the deterministic barème in
  Session 2. `actions` / `duree_heures` are kept for now (marked
  `# TODO(session2)`) so sanctions keep applying in the meantime.
- **Deterministic grounding guards** (`nano.validate_grounding`), run as the last
  filter in `nano.juger` before a verdict can carry a sanction. Three checks, any
  failure ⇒ non-sanctionnable + `Decision.rejet_grounding=<motif>`, never raises:
  - `grounding_citation_absente` — citation empty / carries `[DATA:…]` markers /
    not a verbatim substring of the (fence-stripped) message. Comparison is case-
    and accent-insensitive with collapsed whitespace (`nano._norm`).
  - `grounding_cible_incoherente` — victim-requiring category (`insulte`,
    `menace`, `harcelement`, `harcelement_sexuel`) with `cible` `aucune` /
    `auteur_lui_meme`.
  - `grounding_raison_speculative` — speculative wording in `raison`.
  - Each rejection is logged (`logger.info`, tag `grounding_rejected`) → webhook
    logs; the motif is kept on the `Decision` for the alert card / timeline.
- **New system prompt (v2)** with 8 calibrated few-shots and grounding as an
  absolute rule. English instructions; `raison` / `explication` in the server's
  language.
- **Cold judgement hardened**: `historique_auteur` and `severite` removed from
  nano's user payload (history contaminated the culpability judgement; both
  become deterministic barème inputs in Session 2). The plumbing
  (`AuthorHistory`, `severity`) still flows through for the barème's benefit.
- **Canonical FR categories** (`constants.CATEGORIES`) + `nano.CATEGORIE_ALIASES`
  / `normalize_categorie` fold legacy detector/stored values (`insultes`,
  `menaces`, `contenu_sexuel`…) onto the canonical set — no data migration.
- **`NANO_TEMPERATURE = 0.0`**, **`NANO_MAX_TOKENS = 300`**.

## Files modified

- `automod/schemas.py` — `Decision` gains `citation`, `cible`, `rejet_grounding`.
- `automod/nano.py` — v2 prompt, `validate_grounding`, `_norm`,
  `strip_data_fence`, `normalize_categorie`, `CATEGORIE_ALIASES`, payload trimmed,
  grounding wired into `juger`; removed dead `_SEVERITE_GUIDE`.
- `automod/constants.py` — `CATEGORIES`, `CATEGORIES_AVEC_VICTIME`, temperature 0,
  max tokens 300.
- `modules/automod.py` — evidence payload now stores `citation` / `cible`.
- `docs/AUTOMOD.md` — §2 rewritten (v2 contract, grounding guards, categories).
- `docs/AUTOMOD_V2_PLAN.md` — added, with a progress tracker.
- `tests/automod/test_nano_grounding.py` — new (24 tests; the 7 required
  scenarios + guard/parse/category-folding units).

## Decisions & rationale

- **Severity dropped from nano's prompt**: in v2 severity drives only the
  embedding routing threshold; its strictness role returns deterministically in
  the Session-2 barème. Kept the `severite` param in signatures for compat.
- **Citation kept raw in `parse_verdict`** (only trimmed, not fence-stripped) so
  the guard can still detect and reject echoed `[DATA:…]` markers.
- **Grounding lives in the pipeline, not the module**: it is pure decision logic
  (no I/O), consistent with the "pipeline decides / module applies" split.

## Verification

`pytest tests/automod/` → **126 passed** (24 new). No network/gateway touched
(chat call stubbed).

## Follow-ups (Session 2)

- Deterministic barème (`automod/bareme.py`): cran ladder, weighted-points
  recidivism engine, severity as a cran modulator.
- Remove `actions` / `duree_heures` from the nano contract once the barème
  computes them.
- Consume `AuthorHistory` in the recidivism engine; purge points + evidence on
  accepted appeals.
