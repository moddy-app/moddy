# Brocoli en salon Discord

> Un salon où un administrateur configure son serveur en écrivant des phrases.
> Côté bot : `cogs/brocoli_chat.py`, `services/brocoli_client.py`,
> `utils/brocoli_signature.py`, `utils/brocoli_views.py`.
> Côté backend : `website-backend`, `docs/AI_ASSISTANT.md` et
> `docs/BOT_ASSERTED_AUTH.md`.

## 1. Ce que c'est, et ce que ce n'est pas

**Brocoli** est l'assistant IA du backend. Le dashboard lui parle déjà ; ce salon
est une **deuxième interface**, pas un deuxième Brocoli.

La boucle d'agent, les outils, l'historique et **toutes les écritures** restent
dans `website-backend`. Le bot transporte des messages et affiche des
événements. Tout ce qui ressemble à une décision — quels outils existent, si une
action doit être confirmée, ce que contient un diff — appartient au backend,
volontairement : deux implémentations divergeraient, et celle de Discord serait
celle que personne n'audite.

Conséquence pratique : **il n'y a aucun prompt dans ce dépôt.** Si Brocoli
répond mal, la correction est dans `app/ai/prompts.py` ou
`app/ai/knowledge/`, pas ici.

## 2. Le flux

```
membre écrit dans #moddy-chat
        │
        ▼
cogs/brocoli_chat.on_message
        │  signe une attestation d'identité (utils/brocoli_signature)
        ▼
POST api.moddy.app/ai/conversations/{id}/messages    ← SSE
        │
        ▼
_render() : une carte éditée en place
        │
        ├─ text_delta        → texte accumulé, édité toutes les 1,5 s
        ├─ tool_call         → « Je lis la configuration actuelle… »
        ├─ permission_request→ carte de confirmation + boutons
        └─ run_end           → carte finalisée
```

### Pourquoi la carte est éditée et non renvoyée

Un message Discord ne se diffuse pas jeton par jeton. Éditer à chaque
`text_delta` viderait le bucket d'édition par salon en quelques secondes.
La carte est donc éditée sur un intervalle (`EDIT_INTERVAL`, 1,5 s) et
finalisée une fois — ce qui rend aussi l'état « réflexion » lisible au lieu de
clignotant.

## 3. Les garde-fous

### 3.1 Identité signée, portée dérivée

Le bot n'a pas de session dashboard. Il signe une **attestation d'identité** par
requête : « ce message vient du compte `X`, dans la guilde `G` ».

Il n'affirme **jamais** que `X` est administrateur. Le backend le demande à
Discord lui-même. Si l'attestation portait les droits, un secret fuité donnerait
l'administration de n'importe quel serveur.

**Ne jamais ajouter un champ `is_admin` ou `is_staff` à l'attestation**, aussi
pratique que ce soit — c'est exactement la propriété que le contrat protège.

Contrat complet (en-têtes, canonicalisation, ordre de déploiement) :
`website-backend/docs/BOT_ASSERTED_AUTH.md`.

### 3.2 Genre borné

Une conversation ouverte depuis un salon est de genre `guild_config`, et le
backend refuse tout autre genre sur ce chemin. Les outils de facturation et
d'autorité (remboursements, levée de sanction, annonces) sont **inatteignables
depuis Discord**, même si le membre appartient au staff.

### 3.3 Confirmation par carte, jamais par phrase

Une écriture passe par l'événement `permission_request`, rendu en carte avec
deux boutons. Ce n'est jamais une phrase de Brocoli du type « tu veux que je
l'applique ? ». Une action `critical` est confirmée **même en mode `auto`**, et
ce n'est pas au bot d'en décider.

Les boutons sont des `DynamicItem` persistants (`utils/brocoli_views.py`) : la
décision est scopée à une conversation **et** à une action, que ni
`interaction.guild_id` ni `interaction.user.id` ne fournissent — un `custom_id`
statique ne suffirait pas à reconstruire l'état après un redémarrage. Voir
`docs/PERSISTENT_VIEWS.md`.

L'expiration (`AI_ACTION_TTL`, 900 s côté backend) n'est **pas** doublée côté
bot : les boutons restent cliquables et c'est le backend qui répond « expiré ».
Refuser sur un minuteur qu'on ne possède pas dériverait de la vraie échéance.

### 3.4 Déploiement borné

La commande n'est enregistrée que sur les guildes de `BROCOLI_GUILD_IDS`.
**Liste vide = le cog ne se charge pas du tout.** Sans ce garde-fou, un
`app_commands.guilds()` sans argument enregistrerait `/brocoli` globalement, sur
tous les serveurs où Moddy est présent — l'inverse de l'intention.

`BROCOLI_GUILD_IDS` (bot) doit correspondre à `BOT_ASSERT_ALLOWED_GUILDS`
(backend). Si les deux divergent, la commande existe mais chaque message repart
en `403`.

## 4. Le salon

| | |
|---|---|
| Nom par défaut | `moddy-chat` (voisin de `moddy-updates`, créé à l'arrivée du bot) |
| Visibilité | `@everyone` : `view_channel` refusé |
| Créé par | `/brocoli`, `default_permissions(administrator=True)` |
| État | `guilds.data.brocoli` → `{channel_id, conversation_id}` |

Le salon est réservé aux administrateurs parce que le genre `guild_config` exige
des droits d'admin côté backend : un salon ouvert à tous serait un mur de `403`.

**Une conversation par salon**, pas par membre : le salon est un fil de travail
sur un serveur, et le découper par personne ferait oublier à Brocoli ce qui vient
d'être configuré.

Une ligne commençant par `//` ou `#` est ignorée — de quoi discuter dans le salon
sans consommer un tour.

## 5. Langue

Le salon est **lu par tout le serveur** : ses cartes suivent donc la langue du
serveur (`await guild_locale(bot, guild)`), pas la langue du client de la
personne qui a cliqué. Les réponses éphémères de `/brocoli` suivent la langue de
l'utilisateur. Voir `docs/SERVER_LANGUAGE.md`.

Un nom d'outil est traduit s'il figure dans `_NAMED_TOOLS`. Un outil ajouté
côté backend après ce cog retombe sur une phrase générique — i18n renvoie la clé
quand elle manque, et un `[brocoli.tools.x]` dans un salon se lit comme un bug.

## 6. Variables d'environnement

| Variable | Défaut | Effet |
|---|---|---|
| `BROCOLI_API_URL` | `https://api.moddy.app` | Base de l'API backend |
| `BOT_ASSERT_SECRET` | `""` | Secret HMAC des attestations. **Ne jamais réutiliser `TASK_STREAM_SECRET`** : celui-là protège Redis contre un attaquant qui y a déjà accès, celui-ci permet de parler au nom d'un compte |
| `BROCOLI_GUILD_IDS` | `""` | Guildes où `/brocoli` est enregistrée, séparées par des virgules. Vide = fonctionnalité désactivée |

`config.py` log une erreur au démarrage si `BROCOLI_GUILD_IDS` est renseignée
alors que `BOT_ASSERT_SECRET` manque ou fait moins de 32 caractères.

## 7. Erreurs affichées

`BrocoliError.code` choisit la carte (`utils/brocoli_views.notice_card`) :

| Code | HTTP | Ce que le salon dit |
|---|---|---|
| `quota` | 429 | Limite quotidienne atteinte, remise à zéro à minuit UTC |
| `unavailable` | 503 | Assistant indisponible, rien n'a été modifié |
| `busy` | 409 | Un tour est déjà en cours |
| `expired` | 409 | L'action en attente a expiré |
| `forbidden` | 401/403 | Il faut être administrateur |
| `not_configured` | — | `BOT_ASSERT_SECRET` absent |

Un code inconnu retombe sur `unavailable` avec un warning, plutôt que d'afficher
une clé i18n brute.

## 8. Tests

`tests/test_brocoli.py` (15 cas) : la signature rejouée contre l'algorithme du
backend écrit à la main (un test qui vérifie l'implémentation avec
l'implémentation passerait même si les deux dérivaient ensemble), l'ordre
canonique des clés, la sérialisation des snowflakes en chaînes, la couverture de
chaque champ signé, l'unicité du `request_id`, le refus de signer sans secret
fort, et le parseur SSE (événements nommés, événement final sans ligne vide,
keep-alives, payload illisible, correspondance des statuts HTTP).

`tests/test_persistent_views.py` couvre `BrocoliDecisionPersistence`.

## 9. Limites connues

- **Pas de reprise de flux.** Si le bot redémarre pendant un tour, la carte reste
  en l'état ; le tour se termine côté backend mais personne ne l'affiche. Les
  boutons de confirmation, eux, survivent.
- **Un seul mode.** Le salon utilise le mode `ask` du backend. `read_only` et
  `auto` sont supportés par l'API mais ne sont pas exposés dans le salon.
- **Les questions arrivent en texte, pas en carte.** Quand Brocoli a besoin
  d'une précision, il la demande dans sa réponse : le backend n'émet que sept
  événements et aucun n'est une question. Une version antérieure suspendait le
  tour sur un outil `ask_user` ; la PR #73 du backend l'a remplacée par du texte.
  Le journal de session de la PR #72 décrit encore l'ancienne mécanique — ne pas
  s'y fier, le code fait foi.
