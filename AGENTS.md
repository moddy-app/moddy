### 📄 Instructions pour les IAs – Projet Moddy

Tu aides à développer **Moddy**, un bot Discord écrit en **Python**, hébergé sur un **VPS Ubuntu 24.04 LTS** (chez Hostinger). Il s'agit d'une **application publique**, orientée **assistance pour modérateurs et administrateurs**, mais **sans commandes de sanction** classiques.

#### 📦 Stack et structure

* **Langage** : Python 3.11+
* **Lib** : `nextcord` avec support des **components v2** de Discord
* **Base de données** : Neon (PostgreSQL)
* **Variables d’environnement** via `.env`
* **Arborescence actuelle** :

  ```
  MODDY/
  ├── main.py          # Le cerveau du bot
  ├── bot.py           # Initialisation et classe principale du bot
  ├── config.py        # Configuration (token, etc.)
  ├── cogs/            # Dossier des modules utilisateurs
  │   └── __init__.py
  ├── staff/           # Dossier des commandes staff/dev
  │   └── __init__.py
  ├── requirements.txt # Dépendances Python
  └── .env             # Variables d'environnement (token Discord)
  ```

#### 🛠️ Commandes développeur

Ces commandes sont accessibles **partout (même en DM)**, en mentionnant le bot avec la commande, par ex. : `<@BOTID> reboot`. Elles sont situées dans le dossier `staff/`. Exemples :

* `reboot`
* `user`
* `server`
* `ping`
* `version`
* `sync`
* `deploy` (déploiement d’un commit sur le VPS)

#### 🎯 Commandes slash disponibles

Commandes principales organisées en plusieurs catégories (à coder avec des cogs bien structurés) :

* **Lookup / Informations** :

  * `/user lookup`
  * `/guild lookup`
  * `/event lookup` `[Server Invite] [Event ID]`
  * `/invite lookup`
  * `/webhook lookup`
  * `/avatar` (serveur ou utilisateur)
  * `/banner`

* **Outils et utilitaires** :

  * `/translate [content] | [from] [to]`
  * `/emoji`
  * `/OCR`
  * `/dictionary`
  * `/timestamp syntax`
  * `/roll [min] [max]`

* **Rappels** :

  * `/reminder add`
  * `/reminder remove`
  * `/reminder list`

* **Tags personnalisés** :

  * `/tag send`
  * `/tag manage`

* **Moddy (infos bot)** :

  * `/moddy invite`
  * `/moddy info`
  * `/moddy code`
  * `/preferences`
  * `/help`

#### 📋 Règles et style

* Moddy doit être **modulaire, propre et scalable**.
* Réponses et messages en **français uniquement**.
* Utilise **les components V2** pour les embeds (pas d’anciens systèmes).
* Priorité à la clarté, la fiabilité et la maintenabilité du code.
* Aucun système de modération classique (pas de ban/kick/warn).
* Prévois la prise en charge d’**interactions contextuelles** (menus, boutons, réponses dynamiques).
* Les commandes peuvent être regroupées dans des cogs selon leur thème (lookup, outils, rappels, etc.).

# 🌐 Documentation - Système de langue Moddy

## Vue d'ensemble

Le système de langue de Moddy permet au bot de communiquer avec chaque utilisateur dans sa langue préférée (Français ou Anglais).

## 🎯 Fonctionnement (Nouveau système)

Le système a été simplifié pour être plus robuste et éviter les erreurs.

1.  **Première interaction** :
    *   Quand un nouvel utilisateur interagit avec le bot, sa langue est automatiquement définie sur **Anglais (`EN`)** par défaut dans la base de données.
    *   Le bot envoie ensuite un **message privé (DM)** à l'utilisateur pour l'informer de ce réglage par défaut et lui expliquer comment changer de langue avec la commande `/preferences`.

2.  **Interactions suivantes** :
    *   Pour toutes les interactions futures, la langue de l'utilisateur est récupérée depuis la base de données (via un cache pour la performance).

Ce système **élimine complètement le bug "Interaction already acknowledged"**, car le bot ne répond jamais à l'interaction initiale pour demander la langue.

## 💻 Implémentation dans vos commandes

Pour obtenir la langue de l'utilisateur dans une commande, utilisez la fonction helper `get_user_lang`.

### ✅ Bonne pratique

```python
# cogs/mon_cog.py
import nextcord
from nextcord.ext import commands
from cogs.language_manager import get_user_lang # Important

class MonCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.texts = {
            "FR": {"title": "Titre en français"},
            "EN": {"title": "English Title"}
        }

    @nextcord.slash_command(name="macommande")
    async def ma_commande(self, interaction: nextcord.Interaction):
        # Récupère la langue de l'utilisateur. C'est tout !
        lang = get_user_lang(interaction, self.bot)

        # Utilise la langue pour obtenir le bon texte
        title = self.texts[lang]["title"]
        
        await interaction.response.send_message(f"Le titre est : {title}")
```

### ❌ Ancienne méthode (Obsolète)

N'utilisez **JAMAIS** l'ancienne méthode de gestion du bug `is_done()`. Ce code est maintenant inutile et doit être retiré.

```python
# ❌ MAUVAIS : Ce code est OBSOLÈTE et ne doit plus être utilisé !
await asyncio.sleep(0.1)
if interaction.response.is_done():
    # ... logique de followup ...
    return
```

De même, n'essayez jamais d'accéder à un attribut `user_lang` sur l'objet interaction.

```python
# ❌ MAUVAIS : N'existe plus !
lang = getattr(interaction, 'user_lang', 'EN')
```

## 📝 Checklist pour les commandes

Pour chaque commande slash :
- [ ] Importer `get_user_lang` depuis `cogs.language_manager`.
- [ ] Appeler `lang = get_user_lang(interaction, self.bot)` pour obtenir la langue.
- [ ] **Supprimer** toute ancienne logique avec `asyncio.sleep` ou `interaction.response.is_done()`.
- [ ] Utiliser la variable `lang` pour les textes et la logique de la commande.

# 📚 Documentation Base de Données Moddy

## 🎯 Vue d'ensemble

Moddy utilise PostgreSQL en local sur le VPS (plus Neon). La base de données est structurée en 3 parties principales :

1. **Gestion des erreurs** : Stockage des erreurs avec codes uniques
2. **Cache de lookups** : Informations sur les serveurs/utilisateurs pour les commandes lookup
3. **Données fonctionnelles** : Configuration et données des utilisateurs/serveurs

## 🏗️ Architecture des tables

### 1. Table `errors`
Stocke toutes les erreurs non-triviales avec un code unique (ex: `ABCD1234`)

```sql
errors:
- error_code (PRIMARY KEY) : Code unique à 8 caractères
- error_type : Type d'erreur (ValueError, KeyError, etc.)
- message : Message d'erreur
- file_source : Fichier où l'erreur s'est produite
- line_number : Ligne du code
- traceback : Stack trace complète
- user_id : ID Discord de l'utilisateur concerné
- guild_id : ID du serveur où c'est arrivé
- command : Commande qui a causé l'erreur
- timestamp : Moment de l'erreur
- context (JSONB) : Contexte additionnel flexible
```

### 2. Tables de cache pour lookups

#### `guilds_cache`
Cache les infos des serveurs que le bot ne peut pas obtenir via l'API (serveurs où il n'est pas)

```sql
guilds_cache:
- guild_id (PRIMARY KEY)
- name : Nom du serveur
- icon_url : URL de l'avatar
- features : Fonctionnalités Discord (COMMUNITY, etc.)
- member_count : Nombre de membres
- created_at : Date de création du serveur
- last_updated : Dernière mise à jour des infos
- update_source : Comment on a obtenu l'info (bot_join, user_profile, etc.)
- raw_data (JSONB) : Toutes les données brutes
```

**Sources d'information** :
- `bot_join` : Quand le bot rejoint le serveur
- `user_profile` : Via le profil d'un utilisateur qui a le bot en app perso
- `api_call` : Appel API direct
- `manual` : Ajouté manuellement

### 3. Tables fonctionnelles

#### `users`
Données persistantes des utilisateurs

```sql
users:
- user_id (PRIMARY KEY)
- attributes (JSONB) : Attributs système (voir section Attributs)
- data (JSONB) : Données utilisateur (voir section Data)
- created_at : Première interaction avec le bot
- updated_at : Dernière modification
```

#### `guilds`
Données persistantes des serveurs

```sql
guilds:
- guild_id (PRIMARY KEY)
- attributes (JSONB) : Attributs système
- data (JSONB) : Configuration et données du serveur
- created_at : Ajout du bot au serveur
- updated_at : Dernière modification
```

#### `attribute_changes`
Historique de tous les changements d'attributs (audit trail)

```sql
attribute_changes:
- id : ID auto-incrémenté
- entity_type : 'user' ou 'guild'
- entity_id : ID de l'entité modifiée
- attribute_name : Nom de l'attribut
- old_value : Ancienne valeur
- new_value : Nouvelle valeur
- changed_by : ID du développeur qui a fait le changement
- changed_at : Timestamp
- reason : Raison du changement
```

## 🏷️ Système d'Attributs (NOUVEAU)

Les **attributs** sont des propriétés système NON visibles par les utilisateurs, gérées uniquement par le bot ou les développeurs.

### Fonctionnement simplifié :
- **Attributs booléens** : Si présents = `true`, si absents = `false`
  - Exemple : Si un utilisateur a `PREMIUM` dans ses attributs, il a le premium
  - Pas besoin de stocker `PREMIUM: true`
- **Attributs avec valeur** : Stockent une valeur spécifique
  - Exemple : `LANG: "FR"` pour la langue

### Attributs utilisateur possibles :
- `BETA` : Accès aux fonctionnalités beta (booléen)
- `PREMIUM` : Utilisateur premium (booléen)
- `DEVELOPER` : Développeur du bot (booléen)
- `BLACKLISTED` : Utilisateur banni du bot (booléen)
- `VERIFIED` : Utilisateur vérifié (booléen)
- `SUPPORTER` : Supporte le projet (booléen)
- `TRACK` : Utilisateur suivi/tracké (booléen)
- `LANG` : Langue préférée (valeur : "FR", "EN", etc.)

### Attributs serveur possibles :
- `OFFICIAL_SERVER` : Serveur officiel/partenaire (booléen)
- `BETA_FEATURES` : Accès aux features beta (booléen)
- `PREMIUM_GUILD` : Serveur premium (booléen)
- `VERIFIED_GUILD` : Serveur vérifié (booléen)
- `LEGACY` : Serveur depuis les débuts (booléen)
- `LANG` : Langue du serveur (valeur : "FR", "EN", etc.)

### Format de stockage :
```json
{
  "BETA": true,
  "PREMIUM": true,
  "LANG": "FR"
}
```

Note : Les attributs booléens `false` ne sont PAS stockés. Si un attribut n'est pas présent, il est considéré comme `false`.

### Utilisation dans le code :
```python
# Vérifier un attribut booléen
if await db.has_attribute('user', user_id, 'BETA'):
    # L'utilisateur a accès aux features beta

# Vérifier un attribut avec valeur
lang = await db.get_attribute('user', user_id, 'LANG')
if lang == "FR":
    # L'utilisateur préfère le français

# Définir un attribut booléen
await db.set_attribute('user', user_id, 'PREMIUM', True, dev_id, "Achat premium")

# Supprimer un attribut booléen (= le mettre à false)
await db.set_attribute('user', user_id, 'PREMIUM', False, dev_id, "Fin du premium")
# ou
await db.set_attribute('user', user_id, 'PREMIUM', None, dev_id, "Fin du premium")

# Définir un attribut avec valeur
await db.set_attribute('user', user_id, 'LANG', 'FR', dev_id, "Préférence utilisateur")

# Récupérer tous les utilisateurs avec un attribut
beta_users = await db.get_users_with_attribute('BETA')  # Tous ceux qui ont BETA
french_users = await db.get_users_with_attribute('LANG', 'FR')  # Tous ceux qui ont LANG=FR
```

## 📦 Système de Data

La **data** contient les données utilisateur/serveur modifiables et structurées.

### Data utilisateur typique :
```json
{
  "reminders": [
    {
      "id": "reminder_123",
      "message": "Faire les courses",
      "time": "2024-01-15T14:00:00Z",
      "channel_id": 123456789
    }
  ],
  "preferences": {
    "dm_reminders": true,
    "timezone": "Europe/Paris"
  },
  "tags": {
    "work": "Je suis en réunion",
    "afk": "AFK pour 30 minutes"
  }
}
```

### Data serveur typique :
```json
{
  "config": {
    "prefix": "!",
    "welcome_channel": 123456789,
    "log_channel": 987654321,
    "features": {
      "welcome_message": true,
      "auto_roles": false,
      "logging": true
    }
  },
  "tags": {
    "rules": "1. Soyez respectueux\n2. Pas de spam",
    "help": "Utilisez !help pour l'aide",
    "faq": "Consultez #faq pour les questions"
  },
  "custom_commands": {
    "ping": "Pong! 🏓",
    "discord": "https://discord.gg/..."
  }
}
```

### Mise à jour de la data :
```python
# Mise à jour d'un chemin spécifique
await db.update_user_data(user_id, 'preferences.timezone', 'Europe/Paris')

# Récupération
user = await db.get_user(user_id)
timezone = user['data']['preferences']['timezone']
```

## 🔄 Flux de données

### 1. **Lookup d'un serveur** :
```
Commande /guild lookup
    ↓
Vérifie guilds_cache (données < 7 jours ?)
    ↓ Non ou pas trouvé
Tente via l'API Discord
    ↓ Succès
Met à jour guilds_cache avec update_source
    ↓
Retourne les infos
```

### 2. **Erreur dans une commande** :
```
Exception levée
    ↓
ErrorTracker génère un code unique
    ↓
Enregistre dans table errors
    ↓
Envoie log Discord avec le code
    ↓
User peut partager le code pour debug
```

### 3. **Configuration serveur** :
```
Admin utilise /config prefix ?
    ↓
Récupère guild via db.get_guild()
    ↓
Met à jour data.config.prefix
    ↓
Cache invalidé pour forcer reload
```

## 🛠️ Commandes utiles

### Pour les développeurs :
```python
# Voir les stats
stats = await db.get_stats()
# {'errors': 152, 'users': 4821, 'guilds': 234, 'beta_users': 45}

# Nettoyer les vieilles erreurs
await db.cleanup_old_errors(days=30)

# Bannir un utilisateur (ajouter l'attribut BLACKLISTED)
await db.set_attribute('user', user_id, 'BLACKLISTED', True, dev_id, "Spam")

# Retirer le ban (supprimer l'attribut)
await db.set_attribute('user', user_id, 'BLACKLISTED', False, dev_id, "Appel accepté")

# Donner le premium à un serveur
await db.set_attribute('guild', guild_id, 'PREMIUM_GUILD', True, dev_id, "Achat premium")

# Changer la langue d'un utilisateur
await db.set_attribute('user', user_id, 'LANG', 'EN', dev_id, "Changement de langue")
```

## 🔐 Sécurité et bonnes pratiques

1. **Seuls les devs** peuvent modifier les attributs
2. **Tout est tracé** dans attribute_changes
3. **Cache intelligent** avec TTL configurable
4. **JSONB** permet flexibilité sans migrations
5. **Index optimisés** pour performances
6. **Pas de DELETE** : on marque comme inactif

## 💡 Points clés à retenir

1. **Attributs = Système** (non visible users, géré par devs)
2. **Attributs booléens** : présent = true, absent = false
3. **Attributs avec valeur** : stockent une valeur spécifique (LANG=FR)
4. **Data = Utilisateur** (configs, préférences, données)
5. **Cache intelligent** pour les lookups
6. **Tout est tracé** pour l'audit
7. **PostgreSQL local** sur le VPS, pas cloud

# 🔒 Documentation - Système Incognito Moddy

## ✅ Méthode correcte d'intégration

Le système `incognito` permet de rendre les réponses du bot visibles uniquement pour l'utilisateur qui a exécuté la commande (messages éphémères). L'implémentation doit se faire manuellement dans chaque commande.

### 1. Structure de base pour une commande avec option incognito

```python
# cogs/mon_cog.py
import nextcord
from nextcord import app_commands
from nextcord.ext import commands
from typing import Optional

class MonCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="macommande",
        description="Description en français / English description"
    )
    @app_commands.describe(
        # ... autres paramètres ...
        incognito="Rendre la réponse visible uniquement pour vous / Make response visible only to you"
    )
    async def ma_commande(
        self,
        interaction: nextcord.Interaction,
        # ... autres paramètres ...
        incognito: Optional[bool] = None  # TOUJOURS à la fin, TOUJOURS Optional avec = None
    ):
        """Docstring de la commande"""
        
        # === BLOC INCOGNITO - À copier au début de chaque commande ===
        ephemeral = True # Par défaut sur True pour la sécurité
        if incognito is None and self.bot.db:
            try:
                user_pref = await self.bot.db.get_attribute('user', interaction.user.id, 'DEFAULT_INCOGNITO')
                if user_pref is not None:
                    ephemeral = user_pref
            except Exception:
                # En cas d'erreur, on reste sur True
                pass
        elif incognito is not None:
            ephemeral = incognito
        # === FIN DU BLOC INCOGNITO ===
        
        # ... reste du code ...
        
        # Utiliser ephemeral dans la réponse
        await interaction.response.send_message("Ceci est une réponse.", ephemeral=ephemeral)
```

### 2. Points critiques à respecter

- Le paramètre `incognito` doit être le **dernier** dans la signature de la fonction.
- Son type doit être `Optional[bool] = None`.
- La description doit être bilingue dans `@app_commands.describe`.
- Le bloc de code pour déterminer la valeur de `ephemeral` doit être copié au début de la commande.
- La variable `ephemeral` doit être utilisée dans **tous** les `send_message()` et `followup.send()`.

### ❌ Ce qu'il ne faut PAS faire

- **Ne pas utiliser de décorateur.** Tout système basé sur un décorateur comme `@add_incognito_option` est obsolète et a été supprimé.
- **Ne pas oublier `Optional[bool] = None`.** Si vous mettez seulement `bool`, l'option deviendra obligatoire pour l'utilisateur.

## 📊 Système de préférences

La préférence de l'utilisateur pour le mode incognito est stockée dans la base de données.
- **Table :** `users`
- **Colonne :** `attributes` (JSONB)
- **Clé :** `DEFAULT_INCOGNITO`
- **Valeurs :**
  - `true` : Messages privés par défaut.
  - `false` : Messages publics par défaut.
  - `null` ou absent : Considéré comme privé (`true`).

Cette préférence est modifiable via la commande `/preferences`.

# Emojis

Tu ne dois utiliser aucun émoji standard Unicode. On utilise uniquement les émojis personnalisés. Tu trouveras ci-dessous notre liste avec le nom, l’ID et la syntaxe à utiliser. L’équivalence en Unicode est là juste pour te donner une idée visuelle, mais elle ne doit jamais être utilisée. Même si tu corriges un code qui n’a rien à voir avec les émojis, pense à remplacer les embeds avec les bons émojis custom.
Si tu remarques qu’il en manque ou qu’il en faudrait un autre, n’hésite pas à me le dire.
sync – 1398729150885269546 – <:sync:1398729150885269546> – 🔄  
commit – 1398728993284296806 – <:commit:1398728993284296806> – *(pas d’équivalent)*   
suggestion – 1398720180237893803 – <:suggestion:1398720180237893803> – 💡  
panel – 1398720151980998789 – <:panel:1398720151980998789> – *(pas d’équivalent)*  
translate – 1398720130950627600 – <:translate:1398720130950627600> – 🌐  
moddy – 1396880909117947924 – <:moddy:1396880909117947924> – *(pas d’équivalent)* - Logo de moddy
translatemt – 1395048813353242735 – <:translatemt:1395048813353242735> – *(pas d’équivalent)*  
loading – 1395047662092550194 – <:loading:1395047662092550194> – *(pas d’équivalent)*  - à utiliser quand ça charge c'est le truc en rond qui tourne  
support – 1398734366670065726 – <:support:1398734366670065726> – 🛟  
snowflake – 1398729841938792458 – <:snowflake:1398729841938792458> – ❄️  
invalidsnowflake – 1398729819855913143 – <:invalidsnowflake:1398729819855913143> – *(pas d’équivalent)*  (flocon avec un point d'exclamation) (à utiliser quand un snowflake, donc un id discord, est invalide)
web – 1398729801061240883 – <:web:1398729801061240883> – 🌐  
time – 1398729780723060736 – <:time:1398729780723060736> – 🕒  
manageuser – 1398729745293774919 – <:manageuser:1398729745293774919> – *(pas d’équivalent)*  
user – 1398729712204779571 – <:user:1398729712204779571> – 👤  
verified – 1398729677601902635 – <:verified:1398729677601902635> – ✅ 
dev – 1398729645557285066 – <:dev:1398729645557285066> – *(pas d’équivalent)*   
explore – 1398729622320840834 – <:explore:1398729622320840834> – (ça sorrespond à une boussole)
look – 1398729593074094090 – <:look:1398729593074094090> – (cadenas fermé)
cooldown – 1398729573922767043 – <:cooldown:1398729573922767043> – *(pas d’équivalent)*  
settings – 1398729549323440208 – <:settings:1398729549323440208> – ⚙️  
done – 1398729525277229066 – <:done:1398729525277229066> – ✅ - à utiliser quand quelque chose s'est bien passé par exemple : <:done:1398729525277229066> Les permissions ont bien été configurés
undone – 1398729502028333218 – <:undone:1398729502028333218> – ❌ - à utiliser quand il y a un problème (de permission, un bug etc), par exemple <:undone:1398729502028333218> Tu n'as pas la permissions pour accéder à cette commande. 
label – 1398729473649676440 – <:label:1398729473649676440> – 🏷️  
color – 1398729435565396008 – <:color:1398729435565396008> – 🎨  
emoji – 1398729407065100359 – <:emoji:1398729407065100359> – 😄  
idea – 1398729314597343313 – <:idea:1398729314597343313> – 💡  
legal – 1398729293554782324 – <:legal:1398729293554782324> – ⚖️  
policy – 1398729271979020358 – <:policy:1398729271979020358> – 📜  
copyright – 1398729248063230014 – <:copyright:1398729248063230014> – ©️  
balance – 1398729232862941445 – <:balance:1398729232862941445> – ⚖️  
update – 1398729214064201922 – <:update:1398729214064201922> – 🔄  
import – 1398729171584421958 – <:import:1398729171584421958> – 📥  
back – 1401600847733067806 – <:back:1401600847733067806> – 🔙  
data_object – 1401600908323852318 – <:data_object:1401600908323852318> – {}  
premium – 1401602724801548381 – <:premium:1401602724801548381> – 💎  
logout – 1401603690858676224 – <:logout:1401603690858676224> – 🔚  
add – 1401608434230493254 – <:add:1401608434230493254> – ➕  
commands – 1401610449136648283 – <:commands:1401610449136648283> – *pas d'équivalent* 
code – 1401610523803652196 – <:code:1401610523803652196> – *pas d'équivalent*
bug – 1401614189482475551 – <:bug:1401614189482475551> – 🐞  
info – 1401614681440784477 – <:info:1401614681440784477> – ℹ️  
blacklist – 1401596864784777363 – <:blacklist:1401596864784777363> – *pas d'équivalent*
track – 140159633222695002 – <:track:140159633222695002> – *pas d'équivalent*
history – 1401600464587456512 – <:history:1401600464587456512> – *pas d'équivalent*  
download – 1401600503867248730 – <:download:1401600503867248730> – ⬇️  
ia – 1401600562906005564 – <:ia:1401600562906005564> – ✨  
person_off – 1401600620284219412 – <:person_off:1401600620284219412> – *pas d'équivalent*
edit – 1401600709824086169 – <:edit:1401600709824086169> – ✏️  
delete – 1401600770431909939 – <:delete:1401600770431909939> – 🗑️
notifications - 1402261437493022775 - <:notifications:1402261437493022775> - 🔔
eye_m - 1402261502492151878 - <:eye_m:1402261502492151878> - 👁️