# Variables d'environnement Railway — Service Moddy Bot

Ce document liste toutes les variables d'environnement à configurer dans Railway pour le service `moddy` (bot Discord).

## Variables critiques

### DISCORD_TOKEN
**Valeur :** `<token-du-bot-discord>`
**Description :** Token d'authentification du bot Discord
**Obtention :** Discord Developer Portal → Applications → Bot → Token

### DATABASE_URL
**Valeur :** `<fournie-par-railway>`
**Description :** URL de connexion PostgreSQL — **partagée avec le backend**, même base de données

### REDIS_URL
**Valeur :** `redis://<host>:<port>` ou fournie par Railway
**Description :** URL de connexion Redis — **partagée avec le backend** (Pub/Sub + Streams)

### REDIS_PASSWORD
**Valeur :** `<mot-de-passe-redis>` (optionnel si Redis sans auth)
**Description :** Mot de passe Redis, si requis

### TASK_STREAM_SECRET
**Valeur :** `<générer-avec-secrets.token_urlsafe(48)>` (32 caractères minimum)
**Description :** Secret HMAC partagé backend ⇄ bot signant chaque entrée du
stream `moddy:tasks`. **Même valeur des deux côtés.** Sans lui, le bot rejette
toutes les tâches (bot customization, annonces staff, `update_panel`, sanctions
dashboard) — voir [TASK_SIGNATURE.md](TASK_SIGNATURE.md)
**Génération :** `python -c "import secrets; print(secrets.token_urlsafe(48))"`
**Note :** ne **jamais** réutiliser `REDIS_PASSWORD` — le modèle de menace est
celui d'un attaquant qui a déjà l'accès Redis

### TASK_STREAM_ALLOW_UNSIGNED
**Valeur :** `false` (défaut)
**Description :** Fenêtre de déploiement uniquement : accepte les entrées
`moddy:tasks` **sans** signature tant que le backend ne signe pas encore. Une
signature erronée reste toujours rejetée. À repasser à `false` dès que le
backend est en production (voir [TASK_SIGNATURE.md](TASK_SIGNATURE.md) §6)

## Sécurité de l'API interne

### INTERNAL_API_SECRET
**Valeur :** `<générer-avec-secrets.token_urlsafe(32)>`
**Description :** Secret obligatoire pour protéger l'endpoint `/status` du bot
**Note :** Le backend doit envoyer `Authorization: Bearer <secret>` pour appeler `/status`. Le bot refuse maintenant l'accès si le secret est absent.
**Génération :**
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

### TASK_STREAM_SECRET
**Valeur :** `<générer-un-secret-différent-avec-secrets.token_urlsafe(32)>`
**Description :** Secret HMAC partagé avec le backend pour signer les tâches sensibles du stream Redis `moddy:tasks`
**Note :** Utiliser exactement la même valeur côté bot et backend. Une valeur absente ou trop courte bloque la production et l'exécution des tâches.

## Brocoli — salon de conversation avec l'assistant IA

### BOT_ASSERT_SECRET
**Valeur :** `<générer-encore-un-autre-secret-avec-secrets.token_urlsafe(48)>`
**Description :** Secret HMAC signant les attestations d'identité envoyées à l'API `/ai` du backend (32 caractères minimum)
**Note :** Utiliser exactement la même valeur côté bot et backend. **Ne jamais réutiliser `TASK_STREAM_SECRET`** : celui-là protège Redis contre un attaquant qui y a déjà accès, celui-ci permet de parler au nom d'un compte Discord. Rayons d'explosion différents, rotations indépendantes. Voir `docs/BROCOLI_CHANNEL.md`.

### BROCOLI_GUILD_IDS
**Valeur :** ids de guildes séparés par des virgules, ex. `1421493239579676682`
**Description :** Serveurs où la commande `/brocoli` est enregistrée
**Note :** Vide = fonctionnalité entièrement désactivée (le cog ne se charge pas). Doit correspondre à `BOT_ASSERT_ALLOWED_GUILDS` côté backend, sinon la commande existe mais chaque message repart en `403`.

### BROCOLI_CHANNEL_IDS
**Valeur :** ids de salons séparés par des virgules, ex. `1544393707707437117`
**Description :** Salons déjà existants où Brocoli écoute, sans avoir à lancer `/brocoli`
**Note :** Le salon doit se trouver dans une guilde listée par `BROCOLI_GUILD_IDS`. Utile pour un salon de test, ou pour reprendre la main si l'entrée `guilds.data.brocoli` est perdue.

### BROCOLI_API_URL
**Valeur :** `https://api.moddy.app` (défaut)
**Description :** Base publique de l'API backend qui héberge Brocoli

## Discord

### DISCORD_CLIENT_ID
**Valeur :** ID de l'application Discord
**Description :** Client ID du bot Discord

### BOT_STATUS
**Valeur :** texte optionnel
**Description :** Statut personnalisé du bot

## Serveur HTTP interne

### PORT
**Valeur :** `3000` (par défaut)
**Description :** Port sur lequel le bot expose `/health` et `/status`
**Note :** Le backend appelle `GET <BOT_URL>/status` pour les métriques du bot

## Empreinte mémoire et coût Railway

Railway facture la RAM à la GB-minute, et c'est de loin le premier poste de
coût du projet (≈93 % de la facture, contre ≈4 % pour le CPU). Ces variables
existent uniquement pour piloter cette empreinte.

### MALLOC_ARENA_MAX
**Valeur recommandée :** `2`
**Description :** glibc alloue une arène mémoire (jusqu'à 64 Mo) **par thread**
et ne la rend jamais à l'OS. Le process fait tourner uvicorn, le bot et
plusieurs threads de bibliothèques, ce qui fait stagner le RSS bien au-dessus
de la mémoire réellement vivante. La brider à 2 arènes réduit typiquement le
RSS de 20 à 40 % sans aucun changement de code. À définir sur **tous** les
services Python du projet, pas seulement sur le bot.

### CHUNK_GUILDS_AT_STARTUP
**Valeur :** `False` (défaut) / `True` pour restaurer l'ancien comportement
**Description :** quand elle vaut `True`, discord.py télécharge la liste
complète des membres de **tous** les serveurs au démarrage et garde un objet
`Member` résident (~1 à 2 Ko) pour chacun — le plus gros poste mémoire du
process.

Avec la valeur par défaut (`False`), le cache ne contient que les membres que
Moddy a réellement vus (`MemberCacheFlags(joined=True)` : arrivées, messages,
interactions). Les recherches passent par `utils/members.py` :

- `get_or_fetch_member(guild, user_id)` — cache, puis un fetch REST sur défaut
  de cache. À utiliser partout où un `None` casserait une fonctionnalité
  (permissions de ticket, application d'une sanction, gate AltGuard…).
- `fetch_all_members(guild, cache=False)` — la liste complète à la demande, sans
  la laisser résidente. Réservée aux rares endroits qui en ont vraiment besoin
  (resync AltGuard, statistiques staff).

**Régression connue et assumée :** tout ce qui balaie *tous* les serveurs pour
savoir où un utilisateur est membre (`/mutualserver`, le compte de serveurs
partagés de `/team user`, le fan-out `on_user_update` des logs serveur) ne voit
plus que les serveurs où la personne est en cache. Y appliquer un fetch coûterait
une requête REST **par serveur**, ce qui est pire que la sous-estimation. Passer
`CHUNK_GUILDS_AT_STARTUP=true` restaure l'exactitude, au prix de la mémoire.

### SENTRY_TRACES_SAMPLE_RATE / SENTRY_PROFILES_SAMPLE_RATE
**Valeurs par défaut :** `0.01` / `0.0`
**Description :** le suivi d'erreurs (le seul usage réel de Sentry ici) n'est pas
concerné. Le tracing garde un tampon de spans par transaction et le profileur
lance un thread d'échantillonnage dédié (donc une arène malloc de plus). Les
remonter n'a d'intérêt que pour une investigation ponctuelle.

### Journalisation
Le logger racine suit `DEBUG` : `INFO` en production, `DEBUG` uniquement si
`DEBUG=True`. Le laisser en `DEBUG` fait formater et allouer par toutes les
bibliothèques des enregistrements que personne ne lit.

Sur Railway (détecté via `RAILWAY_ENVIRONMENT`), **aucun fichier de log n'est
écrit** : stdout est déjà collecté et le disque du conteneur est éphémère.
Ailleurs, un `RotatingFileHandler` plafonne `logs/moddy.log` à 5 Mo × 3.

## Variables optionnelles

### DEBUG
**Valeur :** `False` (production) / `True` (développement)

### ENV_MODE
**Valeur :** `production` | `development` | `maintenance`

### DEEPL_API_KEY
**Valeur :** Clé API DeepL (optionnel — désactive `/translate` si absent)

### GROQ_API_KEY
**Valeur :** Clé API Groq (optionnel — désactive la transcription vocale si absent)
**Note :** Les limites du compte Groq (requêtes/minute, secondes d'audio/heure…)
sont configurées dans `gateway/config.py` et surchargeables via
`GROQ_WHISPER_RPM`, `GROQ_WHISPER_RPD`, `GROQ_WHISPER_AUDIO_SECONDS_PER_HOUR`,
`GROQ_WHISPER_AUDIO_SECONDS_PER_DAY` — voir
[VOICE_TRANSCRIPTION.md](VOICE_TRANSCRIPTION.md)

### ALTGUARD_API_URL
**Valeur :** URL de base du service AltGuard (défaut : `https://verify.moddy.app`)

### ALTGUARD_BOT_TOKEN
**Valeur :** Secret partagé envoyé en `Authorization: Bearer` sur
`/altguard/token` et `/altguard/membership/resync` (obligatoire pour le module
AltGuard — sans lui, le bouton de vérification répond « service indisponible »).
Les canaux Redis `altguard:verdict` / `altguard:membership` passent par le Redis
déjà configuré (`REDIS_URL`), rien à ajouter — voir [ALTGUARD.md](ALTGUARD.md)

### HM_URL
**Valeur :** URL de base du Moddy Health Monitor, sans slash final (optionnel —
sans elle, le heartbeat se désactive proprement avec un warning)

### HM_INGEST_TOKEN
**Valeur :** Secret partagé envoyé en `X-Health-Token` sur
`POST /ingest/heartbeat` — identique sur tous les services surveillés
(optionnel, même comportement que `HM_URL` si absent) — voir
[HEALTH_MONITOR.md](HEALTH_MONITOR.md)

### BETTERSTACK_HEARTBEAT_URL
**Valeur :** URL secrète complète de la page « Heartbeat » Better Stack
(ex. `https://uptime.betterstack.com/api/v1/heartbeat/<TOKEN>`) — un simple
GET dessus toutes les 3 minutes signale que le bot va bien, `.../fail` un
échec explicite. Optionnel — sans elle, ce ping se désactive proprement
avec un warning (indépendant de `HM_URL`/`HM_INGEST_TOKEN` ci-dessus) — voir
[HEALTH_MONITOR.md](HEALTH_MONITOR.md)

## Notifications centralisées

Les deux salons vivent dans le serveur de l'équipe Moddy. Défauts dans
`config.py` — voir [NOTIFICATIONS.md](NOTIFICATIONS.md).

### MODDY_NOTIF_REPORT_CHANNEL_ID
**Valeur :** id de salon (défaut : `1541231528754028594`)
**Description :** Salon où sont postés les signalements d'abus déposés depuis le
bouton drapeau d'une notification, avec le panneau de revue (Claim / Voir le
message / Accepter / Refuser)

### MODDY_NOTIF_REPORT_LOG_CHANNEL_ID
**Valeur :** id de salon (défaut : `1541233478522241034`)
**Description :** Salon où chaque étape du traitement d'un signalement (créé,
pris en charge, accepté, refusé) est journalisée

## Demandes de support

Les deux salons vivent dans le serveur de l'équipe Moddy. Défauts dans
`config.py` — voir [SUPPORT_REQUESTS.md](SUPPORT_REQUESTS.md).

### MODDY_BUG_REPORT_CHANNEL_ID
**Valeur :** id de salon (défaut : `1542307806055759943`)
**Description :** Salon où atterrissent les signalements `/bug-report`, avec la
carte staff (Prendre / Répondre / Fermer)

### MODDY_CONFIG_HELP_CHANNEL_ID
**Valeur :** id de salon (défaut : `1542307892970131516`)
**Description :** Salon où atterrissent les demandes « configurez-le pour moi »
envoyées depuis le bouton sous les annonces de Moddy

### MODDY_SUPPORT_URL / MODDY_DASHBOARD_URL / MODDY_DOCS_URL
**Valeurs :** `https://moddy.app/support`, `https://dashboard.moddy.app`,
`https://docs.moddy.app`
**Description :** Liens publics affichés sous `/config`, les cartes de support
et les notifications

## Checklist Railway

- [ ] `DISCORD_TOKEN`
- [ ] `DATABASE_URL` (partagée avec le backend)
- [ ] `REDIS_URL` (partagée avec le backend)
- [ ] `REDIS_PASSWORD` (si Redis avec auth)
- [ ] `INTERNAL_API_SECRET` (obligatoire, protège `/status`)
- [ ] `TASK_STREAM_SECRET` (obligatoire, identique côté backend — sinon `moddy:tasks` est inopérant)
- [ ] `BOT_ASSERT_SECRET` (si salon Brocoli, identique côté backend, ≠ `TASK_STREAM_SECRET`)
- [ ] `BROCOLI_GUILD_IDS` (si salon Brocoli, identique à `BOT_ASSERT_ALLOWED_GUILDS`)
- [ ] `BROCOLI_CHANNEL_IDS` (facultatif — salon existant, sinon `/brocoli` en crée un)
- [ ] `PORT` → `3000`
- [ ] `ENV_MODE` → `production`
- [ ] `DEBUG` → `False`
- [ ] `BOT_STATUS` (optionnel)
- [ ] `ALTGUARD_BOT_TOKEN` (optionnel, requis pour le module AltGuard)
- [ ] `ALTGUARD_API_URL` (optionnel, défaut `https://verify.moddy.app`)
- [ ] `MODDY_NOTIF_REPORT_CHANNEL_ID` (optionnel, défaut en dur dans `config.py`)
- [ ] `MODDY_NOTIF_REPORT_LOG_CHANNEL_ID` (optionnel, défaut en dur dans `config.py`)
- [ ] `MODDY_BUG_REPORT_CHANNEL_ID` (optionnel, défaut en dur dans `config.py`)
- [ ] `MODDY_CONFIG_HELP_CHANNEL_ID` (optionnel, défaut en dur dans `config.py`)
- [ ] `HM_URL` (optionnel, désactive le heartbeat si absent)
- [ ] `HM_INGEST_TOKEN` (optionnel, désactive le heartbeat si absent, identique sur tous les services)
- [ ] `BETTERSTACK_HEARTBEAT_URL` (optionnel, désactive le ping Better Stack si absent)
- [ ] `MALLOC_ARENA_MAX` → `2` (fortement recommandé — voir « Empreinte mémoire »)
- [ ] `CHUNK_GUILDS_AT_STARTUP` (optionnel, défaut `False` — `True` restaure l'ancien cache complet)
- [ ] `SENTRY_TRACES_SAMPLE_RATE` / `SENTRY_PROFILES_SAMPLE_RATE` (optionnels, défauts `0.01` / `0.0`)

## Dépannage

### Le bot ne démarre pas
- Vérifier que `DISCORD_TOKEN` est valide
- Vérifier que `DATABASE_URL` est accessible

### Redis non connecté
- Vérifier `REDIS_URL` et `REDIS_PASSWORD`
- Le bot démarre sans Redis mais les features Pub/Sub et Stream sont désactivées

### `/status` renvoie 401
- Vérifier que `INTERNAL_API_SECRET` est le même côté backend et bot

## Documentation connexe

- [BACKEND-INTEGRATION.md](BACKEND-INTEGRATION.md) — Architecture complète bot ↔ backend
- [NOTIFICATIONS.md](NOTIFICATIONS.md) — Système de notifications centralisées
