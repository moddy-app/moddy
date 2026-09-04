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

## État — déployé en production le 2026-09-01

- Backend : PR #79, mergée, déployée `SUCCESS` à 17:50 (`c0d8209`). **496 passés.**
- Bot : PR #374, mergée, déployée `SUCCESS` à 17:53 (`05a544a`), puis redéployée
  à 18:28 avec ses variables. **1488 passés** après fusion de `main`.
- Un conflit à la fusion : ce travail avait retiré la ligne
  `services/backend_client.py` du `CLAUDE.md` (le fichier n'existe pas), et la
  PR #375 l'a réajoutée. Les trois lignes ont été gardées plutôt que de
  re-supprimer l'entrée de quelqu'un d'autre — **mais le fichier n'existe
  toujours pas et rien ne l'importe**, donc cette ligne reste fausse sur `main`.

## Ce que le premier essai réel a montré

Chaîne complète vérifiée en production, dans les logs des deux services :

```
GET  discord.com/…/guilds/1421493239579676682     → 200
GET  discord.com/…/members/1177298939880415342    → 200
GET  discord.com/…/roles                          → 200
[brocoli] conversation ouverte | kind=guild_config | mode=ask | staff=True
POST /ai/conversations                            → 200 (1106 ms)
POST /ai/conversations/…/messages                 → 200
POST api.openai.com/v1/responses                  → 429
```

Les trois appels Discord sont le modèle de sécurité en action : le bot atteste
une identité, le backend va **demander à Discord** si ce compte administre la
guilde. L'attestation signée, la dérivation des droits, le flux SSE et le rendu
fonctionnent de bout en bout.

Le seul échec : `You have no credits remaining` — le compte OpenAI est à sec.
Ça n'affecte pas que Brocoli : la même clé sert à l'automod, aux embeddings et
aux outils de texte.

## Suites

> Cette section touche les deux dépôts. Les chemins préfixés
> `website-backend/` sont côté backend ; les autres sont dans ce dépôt.

1. **Recharger OpenAI.** Rien à redéployer ensuite.
2. **Point unique de défaillance confirmé.** Un solde vide met à l'arrêt
   l'automod, les embeddings, les outils de texte et Brocoli en même temps.
   `website-backend/app/ai/client.py` parle à OpenAI en httpx brut, sans SDK :
   ajouter un fournisseur de repli (Groq a déjà un adaptateur ici, dans
   `gateway/adapters/groq.py`) est peu de travail pour supprimer cette
   fragilité.
3. **Chemin déterministe devant le modèle**, sur le motif de l'entonnoir de
   `automod/` : les intentions fréquentes (activer un module, changer un seuil)
   ne devraient coûter aucun appel — et continueraient de marcher pendant une
   panne du fournisseur. À concevoir à partir des vraies phrases du salon, pas
   en devinant.
4. **Reprise de flux absente** : si le bot redémarre pendant un tour, la carte
   reste figée. Les boutons de confirmation, eux, survivent.
5. Modes `read_only` / `auto` supportés par l'API mais non exposés dans le salon.
6. La langue du salon suit le réglage serveur, non posé sur la guilde de dev —
   les cartes sortent donc en anglais tant que `/config` → *Server settings* ne
   fixe pas le français.
