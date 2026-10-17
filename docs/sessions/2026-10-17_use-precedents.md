# 2026-10-17 — Automod `use_precedents`

## Contexte

Le dashboard voulait un réglage « accès aux précédents appris » dans l'onglet Avancé de l'Automod. Il n'existait
qu'un interrupteur global (`PRECEDENTS_ENABLED`, `automod/constants.py`). Jules a validé la création d'une clé par
serveur (côté backend : `website-backend/docs/sessions/2026-10-17_etat-route-auto-role-slowmode-precedents.md`).

## Ce qui a été fait

- Clé `use_precedents` (bool, défaut `true`, racine de la config Automod) :
  - `AutomodModule.load_config` la lit (seul un vrai `false` coupe), `get_default_config` et
    `automod/presets.py::blank_config` la portent, `validate_config` refuse un non-booléen
    (`Option des précédents invalide`) ;
  - `make_precedents_provider` renvoie `None` quand elle vaut `false` ; l'enregistrement d'un précédent n'est pas
    touché.
- Panneau `/config` : la clé fait l'aller-retour (`normalize_cfg`, projection / application de la section
  « Avancé ») — sans cela, chaque sauvegarde du panneau l'aurait remise à `true` — et un bouton bascule
  « Utiliser les précédents appris » (`adv:useprec`) est ajouté sur l'écran Avancé, clé i18n
  `advanced.precedents_use` dans les 5 langues.
- Docs : `docs/integration/AUTOMOD_CONFIG.md` (schéma, table de validation, règle 10), `docs/features/AUTOMOD.md`.
- Tests : `tests/test_automod_use_precedents.py` (11 cas) ; suites `test_automod_ai_panel`,
  `test_persistent_views` et `tests/automod` toujours vertes (1070 passés).

## Décisions

- **Périmètre = précédents du serveur seulement.** Les références et termes appris par l'équipe sont chargés dans
  le moteur partagé (`embeddings`, `blocklist`) pour tous les serveurs, avec un cache de scores par texte :
  les filtrer par serveur fausserait ce cache et demanderait de rouvrir le moteur. Non fait, documenté.
- Seul un vrai `false` coupe (une coquille ne doit pas éteindre une modération, comme pour `state`).
- Le bouton est dans l'écran Avancé existant (vue persistante, `custom_id` stable) : pas de nouvelle vue.

## À savoir

- Déployer le bot **avant** le backend : un bot antérieur ignore la clé.
- Environnement de test : `asyncpg` ne se compile pas ici, installé en roue binaire dans un venv hors dépôt.
