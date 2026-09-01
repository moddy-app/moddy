# 2026-09-01 — Brocoli en salon Discord

Deux dépôts, une fonctionnalité : un salon où un administrateur configure son
serveur en parlant à Brocoli. Branche `brocoli-discord-channel` dans `moddy` et
dans `website-backend`.

## Le problème à résoudre

Toute la surface `/ai` du backend est derrière `current_user` — une session
dashboard issue de l'OAuth Discord. Le bot n'en a pas. Deux autres constats en
ouvrant le chantier :

- `services/backend_client.py` est documenté dans `CLAUDE.md` mais **n'existe
  pas**, même sur `origin/main`. Il n'y avait donc aucun chemin HTTP bot →
  backend : la communication passait uniquement par Redis.
- Le clone local de `website-backend` était **18 commits en retard**. Brocoli
  (PR #71, #72) y était arrivé le 29 août ; une première recherche sur le clone
  périmé avait conclu à tort qu'il n'existait pas.

## La décision structurante

**Le bot atteste une identité, jamais une portée.**

Le bot signe « ce message vient du compte `X`, dans la guilde `G` ». Il
n'affirme **pas** que `X` est administrateur : le backend le demande à Discord
lui-même, avec le `DISCORD_BOT_TOKEN` qu'il possède déjà, et relit le staff en
base.

L'alternative — faire porter les droits par l'attestation — était plus simple et
inacceptable : un secret fuité aurait donné l'administration de n'importe quel
serveur, en se contentant de l'affirmer. Ici, un secret fuité usurpe une
identité mais ne fabrique aucun droit, et n'ouvre que ce que ce compte pouvait
déjà faire.

Trois bornes s'y ajoutent, toutes indépendantes du prompt et de qui parle :
genre limité à `guild_config` (donc facturation et autorité inatteignables
depuis Discord, même pour le staff), guilde limitée à celle attestée, et
allowlist explicite vide par défaut.

## Backend (`website-backend`)

| Fichier | |
|---|---|
| `app/middleware/bot_auth.py` | **nouveau** — vérification d'attestation, dérivation des droits |
| `app/dependencies.py` | `conversation_actor` : session **ou** attestation, un seul aval |
| `app/routers/ai.py` | bascule des 8 endpoints + verrou de genre et de guilde dans `_authorize` |
| `app/config.py` | `bot_assert_secret`, `bot_assert_allowed_guilds` |
| `tests/test_bot_assert_security.py` | **nouveau**, 20 cas |
| `docs/BOT_ASSERTED_AUTH.md` | **nouveau** — contrat complet |

Décisions :

- **Réutiliser `app/redis/signing.py`** plutôt que réécrire du HMAC. Une seconde
  implémentation est une seconde occasion de se tromper. La primitive est
  transport-agnostique, elle marche telle quelle sur des en-têtes HTTP.
- **Secret dédié** (`BOT_ASSERT_SECRET`), pas une réutilisation de
  `TASK_STREAM_SECRET`. Rayons d'explosion différents : l'un protège Redis
  contre un attaquant qui y a déjà accès, l'autre permet de parler au nom d'un
  compte par-dessus HTTPS. Et rotations indépendantes.
- **Anti-rejeu fail-closed.** Redis muet ⇒ refus. C'est l'inverse du choix fait
  pour les quotas (`app/ai/quota.py` laisse passer), et c'est assumé : laisser
  passer un tour non compté est bénin, laisser passer un rejeu ne l'est pas.
- **Le choix du chemin se fait sur les en-têtes**, jamais sur un champ du corps —
  un client ne doit pas choisir comment il est authentifié — et une attestation
  invalide échoue au lieu de retomber sur le chemin session.
- **Allowlist vérifiée après la signature** : répondre « guilde non autorisée » à
  un appelant non signé lui dirait quelles guildes existent.

## Bot (`moddy`)

| Fichier | |
|---|---|
| `utils/brocoli_signature.py` | **nouveau** — signature des attestations |
| `services/brocoli_client.py` | **nouveau** — HTTP + parseur SSE |
| `cogs/brocoli_chat.py` | **nouveau** — commande, salon, `on_message`, rendu |
| `utils/brocoli_views.py` | **nouveau** — cartes + boutons de confirmation persistants |
| `utils/persistent_views.py` | enregistrement de `BrocoliDecisionPersistence` |
| `config.py` | `BROCOLI_API_URL`, `BOT_ASSERT_SECRET`, `BROCOLI_GUILD_IDS` + erreur au boot |
| `locales/{fr,en-US}.json` | bloc `brocoli` |
| `locales/commands/*.json` | **les 32** |
| `tests/test_brocoli.py` | **nouveau**, 15 cas |
| `docs/BROCOLI_CHANNEL.md` | **nouveau** |

Décisions :

- **Le bot est un client, pas un second Brocoli.** Aucun prompt dans ce dépôt.
  Deux implémentations de la logique d'agent divergeraient, et celle de Discord
  serait celle que personne n'audite.
- **Carte éditée sur un intervalle de 1,5 s** plutôt qu'à chaque `text_delta` :
  Discord limite les éditions par salon, et une carte qui clignote est moins
  lisible qu'une carte qui avance par paliers.
- **`app_commands.guilds()` sans argument enregistre globalement.** Le cog
  refuse donc de se charger si `BROCOLI_GUILD_IDS` est vide, au lieu de risquer
  `/brocoli` sur tous les serveurs.
- **Une conversation par salon**, pas par membre : le salon est un fil de travail
  sur un serveur ; le découper par personne ferait oublier à Brocoli ce qui vient
  d'être configuré.
- **Pas de minuteur d'expiration côté bot.** Le TTL de 900 s appartient au
  backend ; le doubler ici dériverait de la vraie échéance. Les boutons restent
  cliquables et c'est le backend qui répond « expiré ».
- **Le test de signature réimplémente l'algorithme du backend à la main** plutôt
  que d'appeler notre helper deux fois : un test qui vérifie l'implémentation
  avec l'implémentation passerait même si les deux dépôts dérivaient ensemble.

## Deux erreurs corrigées en route

- **Locale.** J'avais utilisé `guild.preferred_locale`, que la règle 4 du
  `CLAUDE.md` interdit explicitement. Les cartes du salon sont lues par tout le
  serveur : elles passent par `await guild_locale(bot, guild)`. Seules les
  réponses éphémères de `/brocoli` suivent la langue de l'utilisateur.
- **Locales de commande.** J'avais supposé que les traductions manquantes
  retombaient sur l'anglais. C'est vrai à l'exécution, mais
  `tests/test_command_localizations.py` **impose** que les 32 fichiers aient les
  mêmes clés — la suite est passée de verte à 30 échecs. Les 32 sont remplies.

## Divergence trouvée dans la documentation backend

Le journal de session `2026-08-29_brocoli-comportement-et-questions.md` décrit un
outil `ask_user`, un événement `user_question`, un statut `awaiting_answer`, une
table `ai_questions`, `Tool.suspends` et `resume_after_answer`. **Rien de tout
cela n'existe dans `app/`** : la PR #73 (« questions en texte ») a remplacé cette
mécanique, et le journal de la #72 n'a pas été corrigé. Le backend n'émet que
sept événements. Vérifié avant d'écrire la doc du salon, qui dit donc que les
questions arrivent en texte.

## État

- Backend : **496 passés**, 9 ignorés (baseline 476 → +20).
- Bot : **1419 passés** (baseline 1414), dont `test_persistent_views.py` 288.
- Les deux branches sont commitées, non poussées, dans des worktrees séparés —
  les branches `kymra` des deux dépôts et leur travail non commité (`ratelimit`,
  HSTS côté backend) n'ont pas été touchés.

## Suites

1. **Déploiement.** Générer `BOT_ASSERT_SECRET`, le poser sur les deux services
   Railway, renseigner `BOT_ASSERT_ALLOWED_GUILDS` (backend) et
   `BROCOLI_GUILD_IDS` (bot) avec `1421493239579676682`. Backend d'abord.
2. **Rien n'a été testé contre un vrai backend** — la suite couvre les contrats,
   pas l'intégration. Le premier essai réel se fait sur le serveur de dev.
3. **Reprise de flux absente** : si le bot redémarre pendant un tour, la carte
   reste figée. Les boutons de confirmation, eux, survivent.
4. Modes `read_only` / `auto` supportés par l'API mais non exposés dans le salon.
