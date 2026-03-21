# Variables d'environnement Railway - Service Moddy Bot

Ce document liste toutes les variables d'environnement à configurer dans Railway pour le service `moddy` (bot Discord).

## 🔐 Variables critiques de sécurité

### DISCORD_TOKEN
**Valeur :** `<token-du-bot-discord>`
**Description :** Token d'authentification du bot Discord
**Obtention :** Discord Developer Portal → Applications → Bot → Token
**⚠️ CRITIQUE :** Ne jamais partager ou commiter ce token

### INTERNAL_API_SECRET
**Valeur :** `<générer-avec-secrets.token_urlsafe(32)>`
**Description :** Secret partagé pour l'authentification de l'API interne
**⚠️ IMPORTANT :** Doit être IDENTIQUE dans le backend et le bot
**Génération :**
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

## 🌐 Configuration de l'API interne

### INTERNAL_PORT
**Valeur :** `3000`
**Description :** Port du serveur HTTP interne (privé, non exposé publiquement)
**Note :** Utilisé uniquement pour la communication avec le backend via Railway Private Network

### BACKEND_INTERNAL_URL
**Valeur :** `http://website-backend.railway.internal:8080`
**Description :** URL interne du backend pour que le bot puisse communiquer avec lui
**Note :** Utilise Railway Private Networking (`.railway.internal`)

## 🎮 Configuration Discord

### MODDY_GUILD_ID
**Valeur :** `1394001780148535387`
**Description :** ID du serveur Discord principal de Moddy
**Utilisation :** Gestion automatique des rôles premium/abonnement
**Comment obtenir :** Mode développeur Discord → Clic droit sur le serveur → Copier l'identifiant

### MODDY_PREMIUM_ROLE_ID
**Valeur :** `1424149819185827954`
**Description :** ID du rôle premium "Moddy Max" à attribuer aux abonnés
**Utilisation :** Ajouté automatiquement lors de l'achat d'un abonnement
**Comment obtenir :** Mode développeur Discord → Clic droit sur le rôle → Copier l'identifiant

### BOT_STATUS
**Valeur :** `<statut-personnalisé>` (optionnel)
**Description :** Statut personnalisé affiché par le bot
**Exemple :** `"🤖 Moddy v2.0 | moddy.gg"`

## 🗄️ Base de données

### DATABASE_URL
**Valeur :** `<fournie-par-railway>`
**Description :** URL de connexion PostgreSQL
**Note :** Automatiquement fournie par Railway si vous utilisez un service PostgreSQL

## 🔧 Variables optionnelles

### DEBUG
**Valeur :** `False` (production) ou `True` (développement)
**Description :** Active le mode debug avec logs supplémentaires
**⚠️ Production :** Doit être `False` en production

### PORT
**Valeur :** `8080` (par défaut)
**Description :** Port public du bot (pour le bot Discord standard)
**Note :** Différent de INTERNAL_PORT qui est privé

## 📋 Checklist de configuration Railway

Avant de déployer, vérifier que ces variables sont configurées :

- [ ] `DISCORD_TOKEN` - Token du bot Discord
- [ ] `INTERNAL_API_SECRET` - Secret généré et identique au backend
- [ ] `INTERNAL_PORT` - `3000`
- [ ] `BACKEND_INTERNAL_URL` - `http://website-backend.railway.internal:8080`
- [ ] `MODDY_GUILD_ID` - `1394001780148535387`
- [ ] `MODDY_PREMIUM_ROLE_ID` - `1424149819185827954`
- [ ] `DATABASE_URL` - URL PostgreSQL (fournie par Railway)
- [ ] `BOT_STATUS` - Statut personnalisé (optionnel)
- [ ] `DEBUG` - `False` pour production

## 🔍 Vérification

Pour vérifier que les variables sont correctement configurées, consulter les logs de démarrage du bot :

```
✅ Internal API server started on port 3000
✅ INTERNAL_API_SECRET configured
✅ Bot instance configured for internal API
```

## 🚨 Dépannage

### Le bot ne démarre pas
- Vérifier que `DISCORD_TOKEN` est valide
- Vérifier que `DATABASE_URL` est accessible

### L'API interne ne fonctionne pas
- Vérifier que `INTERNAL_API_SECRET` est identique dans le backend et le bot
- Vérifier que `INTERNAL_PORT` est `3000`
- Consulter les logs avec `railway logs moddy`

### Les rôles ne sont pas attribués
- Vérifier que `MODDY_GUILD_ID` est correct
- Vérifier que `MODDY_PREMIUM_ROLE_ID` est correct
- Vérifier que le bot est présent dans le serveur
- Vérifier que le bot a les permissions "Gérer les rôles"
- Vérifier que le rôle du bot est **au-dessus** du rôle premium dans la hiérarchie

## Related Documentation

- [INTERNAL_API.md](INTERNAL_API.md) — Complete internal API documentation
- [BACKEND_INTEGRATION_STATUS.md](BACKEND_INTEGRATION_STATUS.md) — Integration diagnostic
