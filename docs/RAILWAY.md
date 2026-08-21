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
**Description :** Secret optionnel pour protéger l'endpoint `/status` du bot
**Note :** Si configuré, le backend doit envoyer `Authorization: Bearer <secret>` pour appeler `/status`
**Génération :**
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

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

## Checklist Railway

- [ ] `DISCORD_TOKEN`
- [ ] `DATABASE_URL` (partagée avec le backend)
- [ ] `REDIS_URL` (partagée avec le backend)
- [ ] `REDIS_PASSWORD` (si Redis avec auth)
- [ ] `TASK_STREAM_SECRET` (identique côté backend — sinon `moddy:tasks` est inopérant)
- [ ] `INTERNAL_API_SECRET` (optionnel, protège `/status`)
- [ ] `PORT` → `3000`
- [ ] `ENV_MODE` → `production`
- [ ] `DEBUG` → `False`
- [ ] `BOT_STATUS` (optionnel)
- [ ] `ALTGUARD_BOT_TOKEN` (optionnel, requis pour le module AltGuard)
- [ ] `ALTGUARD_API_URL` (optionnel, défaut `https://verify.moddy.app`)

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
