# Automod v2 — Session 4 : coûts & anti-fragmentation

**Date :** 2026-07-12
**Branche :** `claude/automod-v2-session-4-1xgcpl`
**Plan :** `docs/AUTOMOD_V2_PLAN.md` § SESSION 4

## Objectif

Diviser la facture, encaisser les raids et attraper les messages fractionnés —
sans ajouter un seul appel IA. Trois mécanismes, tous hors du chemin chaud d'un
message normal.

## Ce qui a été fait

### 4.1 Cache de verdicts nano
- `AutomodEngine` mémorise la **qualification** nano par
  `sha256(guild_id + collapse_repeats(texte))` (per-guild : indications/sévérité
  diffèrent), TTL 600 s, LRU 2048, single-flight (comme le cache d'embeddings).
- Seule la qualification est cachée ; le **barème** (cran + récidive) est
  recalculé à chaque fois (l'historique de l'auteur diffère). `a_reverifier`
  n'est jamais restauré (spécifique au contexte).
- Un **probe gratuit** précède le budget guard : un verdict en cache est servi
  même au-dessus du budget.

### 4.2 Agrégation par auteur (harcèlement fractionné)
- Buffer Redis glissant par `(guild, channel, auteur)` (45 s, cap 6 messages).
- Quand un message s'arrête **avant** nano et que le buffer contient ≥ 2
  fragments récents, la **concaténation** est routée sur les seules étapes
  gratuites (blocklist + embedding). Si elle route, nano juge le texte combiné
  (`message_cible.agregat_de` + règle de prompt *AGGREGATED MESSAGE*).
- La `Decision` porte `agregat_de` (tous les ids) et `agregat_contenu` ; le
  module supprime les N fragments.
- Anti-double-jugement : un fragment déjà passé par nano fait sauter l'agrégat
  (set Redis `automod:agg:judged:*`).
- Gated sur `bot.redis` : sans Redis, comportement message-par-message inchangé.

### 4.3 Budget guard par guild
- Compteur Redis `automod:budget:{guild}:{jour}` incrémenté à chaque **vrai**
  appel nano ; cap 300/jour, override par `automod:budget:cap:{guild}`.
- Au-delà du cap : **mode dégradé** — nano réservé aux cas flagrants (regex, ou
  embedding ≥ seuil + 0.10), le reste est dropé. Jamais de coupure sèche.
- Carte « budget IA du jour atteint » postée **une seule fois** par jour
  (`pop_budget_notice`). Un cache-hit ne consomme pas de budget.
- Diagnostics : `budget_stats()`, `verdict_cache_stats()`.

### 4.4 Ordre de mérite du funnel
- La clé du cache d'embeddings est désormais la forme **collapsed/normalisée**
  (`embeddings.cache_key`) : "aaaa"/"aaaaa", "Con"/"con" partagent l'entrée.
- Messages > `PREFILTRE_MAX_CHARS` (1500) : embeddés sur leur forme collapsed,
  tronquée au cap.

### 4.5 Doc coûts
- Tableau d'ordre de grandeur (1 M msgs/mois) ajouté dans `AUTOMOD.md` §5.4.

## Fichiers modifiés

- `automod/constants.py` — constantes S4.
- `automod/engine.py` — refactor routing + `_decide`/`_nano_call`, cache verdicts,
  agrégation, budget guard, diagnostics.
- `automod/embeddings.py` — `cache_key` normalisée + troncature longue.
- `automod/nano.py`, `automod/schemas.py` — contrat d'agrégat.
- `modules/automod.py` — `channel_id`, suppression multi-fragments, carte budget.
- `locales/{fr,en-US}.json` — `modules.automod.budget.*`.
- `docs/AUTOMOD.md`, `docs/AUTOMOD_V2_PLAN.md` — documentation.
- Tests : `tests/automod/test_verdict_cache.py`, `test_aggregation.py`,
  `test_budget_guard.py`, `_redis_stub.py`, ajouts dans `test_embeddings.py`.

## Décisions

- Probe cache avant budget guard (cohérence + économie).
- Barème jamais caché ; seule la qualification l'est.
- Cap budget configurable en Redis (zéro migration, indépendant des quotas
  gateway).
- Agrégation inerte sans Redis → aucune régression sur un déploiement sans Redis.

## Vérification

- `pytest tests/automod/` → **198 passés**.
- `python -m automod.eval.run` (replay) → precision/recall 1.0, exit 0 (aucune
  régression `faux_positif_reel`).

## Suites (S5)

Graphe relationnel + réaction de la cible. Le budget guard compte déjà les
appels nano et absorbera le coût mini de S6 (poids ×4).
