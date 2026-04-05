# Documentation de la Base de Données Moddy

Cette documentation explique la structure de la base de données PostgreSQL de Moddy et comment y accéder depuis un bot externe (par exemple, un bot de support).

## Table des matières

1. [Connexion à la base de données](#connexion-à-la-base-de-données)
2. [Structure des tables](#structure-des-tables)
3. [Système d'attributs et de données](#système-dattributs-et-de-données)
4. [Syntaxe PostgreSQL et JSONB](#syntaxe-postgresql-et-jsonb)
5. [Exemples de requêtes utiles](#exemples-de-requêtes-utiles)

---

## Connexion à la base de données

Moddy utilise PostgreSQL avec la bibliothèque `asyncpg`. L'URL de connexion se trouve dans la variable d'environnement `DATABASE_URL`.

### Format de l'URL
```
postgresql://utilisateur:motdepasse@hote/nom_base
```

### Connexion avec asyncpg

```python
import asyncpg

# Créer une connexion
conn = await asyncpg.connect('postgresql://moddy:password@localhost/moddy')

# Ou utiliser un pool de connexions (recommandé pour production)
pool = await asyncpg.create_pool(
    'postgresql://moddy:password@localhost/moddy',
    min_size=5,
    max_size=20
)

# Exécuter une requête
async with pool.acquire() as connection:
    result = await connection.fetch('SELECT * FROM users WHERE user_id = $1', user_id)
```

---

## Structure des tables

### 1. Table `errors`

Stocke tous les codes d'erreur générés par le bot.

**Colonnes:**
- `error_code` (VARCHAR(8), PRIMARY KEY) - Code unique de l'erreur (ex: "A1B2C3D4")
- `error_type` (VARCHAR(100)) - Type d'erreur Python (ex: "ValueError", "HTTPException")
- `message` (TEXT) - Message d'erreur
- `file_source` (VARCHAR(255)) - Fichier source où l'erreur s'est produite
- `line_number` (INTEGER) - Numéro de ligne
- `traceback` (TEXT) - Traceback complet
- `user_id` (BIGINT) - ID de l'utilisateur concerné
- `guild_id` (BIGINT) - ID du serveur concerné
- `command` (VARCHAR(100)) - Commande qui a causé l'erreur
- `timestamp` (TIMESTAMPTZ) - Date et heure de l'erreur
- `context` (JSONB) - Contexte additionnel (JSON)

**Index:**
- `idx_errors_timestamp` sur `timestamp`
- `idx_errors_user` sur `user_id`

**Exemple de requête:**
```sql
-- Récupérer une erreur par son code
SELECT * FROM errors WHERE error_code = 'A1B2C3D4';

-- Récupérer toutes les erreurs des dernières 24h
SELECT * FROM errors
WHERE timestamp > NOW() - INTERVAL '24 hours'
ORDER BY timestamp DESC;
```

---

### 2. Table `users`

Stocke les données des utilisateurs Discord.

**Colonnes:**
- `user_id` (BIGINT, PRIMARY KEY) - ID Discord de l'utilisateur
- `attributes` (JSONB) - Attributs/flags de l'utilisateur
- `data` (JSONB) - Données supplémentaires (préférences, etc.)
- `stripe_customer_id` (VARCHAR(50)) - ID client Stripe (ex: `cus_UAf6a2WKTw6yCI`)
- `email` (VARCHAR(255)) - Adresse email de l'utilisateur
- `created_at` (TIMESTAMPTZ) - Date de création
- `updated_at` (TIMESTAMPTZ) - Dernière mise à jour

**Index:**
- `idx_users_attributes` (GIN) sur `attributes` - Permet des recherches rapides dans le JSON

**Attributs courants:**
- `TEAM` (bool) - Membre du staff
- `PREMIUM` (bool) - Utilisateur premium
- `BETA` (bool) - Testeur beta
- `BLACKLISTED` (bool) - Utilisateur blacklisté
- `LANG` (string) - Langue préférée (ex: "FR", "EN")

**Exemple de requête:**
```sql
-- Récupérer un utilisateur
SELECT * FROM users WHERE user_id = 123456789;

-- Vérifier si un utilisateur est blacklisté
SELECT attributes->'BLACKLISTED' FROM users WHERE user_id = 123456789;

-- Récupérer tous les utilisateurs premium
SELECT user_id FROM users WHERE attributes ? 'PREMIUM';

-- Récupérer tous les utilisateurs avec langue FR
SELECT user_id FROM users WHERE attributes @> '{"LANG": "FR"}';
```

---

### 3. Table `guilds`

Stocke les données des serveurs Discord.

**Colonnes:**
- `guild_id` (BIGINT, PRIMARY KEY) - ID Discord du serveur
- `attributes` (JSONB) - Attributs/flags du serveur
- `data` (JSONB) - Configurations des modules
- `created_at` (TIMESTAMPTZ) - Date de création
- `updated_at` (TIMESTAMPTZ) - Dernière mise à jour

**Index:**
- `idx_guilds_attributes` (GIN) sur `attributes`

**Attributs courants:**
- `BLACKLISTED` (bool) - Serveur blacklisté
- `PREMIUM` (bool) - Serveur premium
- `BETA` (bool) - Serveur testeur beta

**Structure de `data`:**
Le champ `data` contient les configurations des modules activés. Exemple:
```json
{
  "modules": {
    "starboard": {
      "enabled": true,
      "channel_id": 123456789,
      "threshold": 3,
      "emoji": "⭐"
    },
    "welcome": {
      "enabled": true,
      "channel_id": 987654321,
      "message": "Bienvenue {user}"
    }
  }
}
```

**Exemple de requête:**
```sql
-- Récupérer un serveur
SELECT * FROM guilds WHERE guild_id = 123456789;

-- Vérifier si un module est activé
SELECT data->'modules'->'starboard'->>'enabled'
FROM guilds WHERE guild_id = 123456789;

-- Récupérer tous les serveurs blacklistés
SELECT guild_id FROM guilds WHERE attributes ? 'BLACKLISTED';
```

---

### 4. Table `staff_permissions`

Stocke les permissions des membres du staff.

**Colonnes:**
- `user_id` (BIGINT, PRIMARY KEY) - ID Discord du membre du staff
- `roles` (JSONB) - Liste des rôles du staff (array)
- `denied_commands` (JSONB) - Liste des commandes interdites (array)
- `role_permissions` (JSONB) - Permissions spécifiques par rôle (object)
- `created_at` (TIMESTAMPTZ) - Date de création
- `updated_at` (TIMESTAMPTZ) - Dernière mise à jour
- `created_by` (BIGINT) - ID du créateur
- `updated_by` (BIGINT) - ID du dernier modificateur

**Index:**
- `idx_staff_permissions_roles` (GIN) sur `roles`

**Rôles disponibles:**
- `"Dev"` - Développeur (niveau max, hors hiérarchie)
- `"Manager"` - Manager (niveau 100)
- `"Supervisor_Mod"` - Superviseur Modération (niveau 50)
- `"Supervisor_Com"` - Superviseur Communication (niveau 50)
- `"Supervisor_Sup"` - Superviseur Support (niveau 50)
- `"Moderator"` - Modérateur (niveau 10)
- `"Communication"` - Communication (niveau 10)
- `"Support"` - Support (niveau 10)

**Structure de `roles`:**
```json
["Manager", "Dev"]
```

**Structure de `denied_commands`:**
```json
["d.sql", "mod.ban"]
```

**Exemple de requête:**
```sql
-- Récupérer les permissions d'un staff
SELECT * FROM staff_permissions WHERE user_id = 123456789;

-- Récupérer tous les managers
SELECT user_id FROM staff_permissions
WHERE roles @> '["Manager"]'::jsonb;

-- Vérifier si un staff a un rôle spécifique
SELECT EXISTS(
  SELECT 1 FROM staff_permissions
  WHERE user_id = 123456789 AND roles @> '["Dev"]'::jsonb
);

-- Récupérer tous les membres du staff
SELECT user_id, roles FROM staff_permissions
ORDER BY created_at;
```

---

### 5. Table `moderation_cases`

Stocke les cases de modération (sanctions).

**Colonnes:**
- `case_id` (VARCHAR(8), PRIMARY KEY) - ID unique de la case (hex)
- `case_type` (VARCHAR(20)) - Type de case ("interserver", "global")
- `sanction_type` (VARCHAR(50)) - Type de sanction (voir ci-dessous)
- `entity_type` (VARCHAR(10)) - Type d'entité ("user", "guild")
- `entity_id` (BIGINT) - ID de l'entité sanctionnée
- `status` (VARCHAR(20)) - Statut ("open", "closed")
- `reason` (TEXT) - Raison de la sanction
- `evidence` (TEXT) - Preuves (liens, screenshots, etc.)
- `duration` (INTEGER) - Durée en secondes (pour timeout)
- `staff_notes` (JSONB) - Notes du staff (array d'objets)
- `created_by` (BIGINT) - ID du créateur
- `created_at` (TIMESTAMPTZ) - Date de création
- `updated_by` (BIGINT) - ID du dernier modificateur
- `updated_at` (TIMESTAMPTZ) - Dernière mise à jour
- `closed_by` (BIGINT) - ID du staff qui a fermé la case
- `closed_at` (TIMESTAMPTZ) - Date de fermeture
- `close_reason` (TEXT) - Raison de fermeture

**Index:**
- `idx_moderation_cases_entity` sur `(entity_type, entity_id)`
- `idx_moderation_cases_status` sur `status`
- `idx_moderation_cases_type` sur `(case_type, sanction_type)`
- `idx_moderation_cases_created_at` sur `created_at DESC`

**Types de sanctions:**
- `"interserver_blacklist"` - Blacklist inter-serveur
- `"interserver_timeout"` - Timeout inter-serveur
- `"global_blacklist"` - Blacklist globale
- `"guild_blacklist"` - Blacklist d'un serveur

**Structure de `staff_notes`:**
```json
[
  {
    "staff_id": 123456789,
    "note": "Utilisateur avertis, récidive possible",
    "timestamp": "2025-12-10T10:30:00Z"
  }
]
```

**Exemple de requête:**
```sql
-- Récupérer une case
SELECT * FROM moderation_cases WHERE case_id = 'A1B2C3D4';

-- Récupérer toutes les cases d'un utilisateur
SELECT * FROM moderation_cases
WHERE entity_type = 'user' AND entity_id = 123456789
ORDER BY created_at DESC;

-- Récupérer toutes les cases ouvertes
SELECT * FROM moderation_cases
WHERE status = 'open'
ORDER BY created_at DESC;

-- Vérifier si un utilisateur a une sanction active
SELECT EXISTS(
  SELECT 1 FROM moderation_cases
  WHERE entity_type = 'user'
    AND entity_id = 123456789
    AND status = 'open'
    AND sanction_type = 'global_blacklist'
);

-- Compter les cases par type
SELECT case_type, sanction_type, COUNT(*)
FROM moderation_cases
GROUP BY case_type, sanction_type;
```

---

### 6. Table `attribute_changes`

Audit des changements d'attributs.

**Colonnes:**
- `id` (SERIAL, PRIMARY KEY)
- `entity_type` (VARCHAR(10)) - "user" ou "guild"
- `entity_id` (BIGINT) - ID de l'entité
- `attribute_name` (VARCHAR(50)) - Nom de l'attribut modifié
- `old_value` (TEXT) - Ancienne valeur
- `new_value` (TEXT) - Nouvelle valeur
- `changed_by` (BIGINT) - ID du modificateur
- `changed_at` (TIMESTAMPTZ) - Date du changement
- `reason` (TEXT) - Raison du changement

**Index:**
- `idx_attribute_changes_entity` sur `(entity_type, entity_id)`

**Exemple de requête:**
```sql
-- Historique des changements d'un utilisateur
SELECT * FROM attribute_changes
WHERE entity_type = 'user' AND entity_id = 123456789
ORDER BY changed_at DESC;

-- Voir qui a modifié l'attribut BLACKLISTED
SELECT * FROM attribute_changes
WHERE attribute_name = 'BLACKLISTED'
ORDER BY changed_at DESC;
```

---

### 7. Table `reminders`

Stocke les rappels des utilisateurs.

**Colonnes:**
- `id` (SERIAL, PRIMARY KEY)
- `user_id` (BIGINT) - ID de l'utilisateur
- `guild_id` (BIGINT) - ID du serveur
- `channel_id` (BIGINT) - ID du canal
- `message` (TEXT) - Message du rappel
- `remind_at` (TIMESTAMPTZ) - Quand envoyer le rappel
- `created_at` (TIMESTAMPTZ) - Date de création
- `sent` (BOOLEAN) - Rappel envoyé ou non
- `sent_at` (TIMESTAMPTZ) - Date d'envoi
- `failed` (BOOLEAN) - Échec d'envoi
- `send_in_channel` (BOOLEAN) - Envoyer dans le canal ou en DM

**Index:**
- `idx_reminders_user_id` sur `user_id`
- `idx_reminders_remind_at` sur `remind_at`
- `idx_reminders_sent` sur `sent`

---

### 8. Table `saved_messages`

Messages sauvegardés par les utilisateurs.

**Colonnes:**
- `id` (SERIAL, PRIMARY KEY)
- `user_id` (BIGINT) - ID de l'utilisateur qui a sauvegardé
- `message_id` (BIGINT) - ID du message Discord
- `channel_id` (BIGINT) - ID du canal
- `guild_id` (BIGINT) - ID du serveur
- `author_id` (BIGINT) - ID de l'auteur du message
- `author_username` (TEXT) - Nom de l'auteur
- `content` (TEXT) - Contenu du message
- `attachments` (JSONB) - Pièces jointes
- `embeds` (JSONB) - Embeds
- `created_at` (TIMESTAMPTZ) - Date du message original
- `saved_at` (TIMESTAMPTZ) - Date de sauvegarde
- `message_url` (TEXT) - URL du message
- `note` (TEXT) - Note personnelle
- `raw_message_data` (JSONB) - Données brutes du message

**Index:**
- `idx_saved_messages_user_id` sur `user_id`
- `idx_saved_messages_saved_at` sur `saved_at`
- `idx_saved_messages_author_id` sur `author_id`

---

### 9. Table `interserver_messages`

Messages du système inter-serveur.

**Colonnes:**
- `moddy_id` (VARCHAR(8), PRIMARY KEY) - ID Moddy unique
- `original_message_id` (BIGINT) - ID du message original
- `original_guild_id` (BIGINT) - ID du serveur original
- `original_channel_id` (BIGINT) - ID du canal original
- `author_id` (BIGINT) - ID de l'auteur
- `author_username` (TEXT) - Nom de l'auteur
- `content` (TEXT) - Contenu du message
- `timestamp` (TIMESTAMPTZ) - Date du message
- `status` (VARCHAR(20)) - Statut ("active", "deleted")
- `is_moddy_team` (BOOLEAN) - Message de l'équipe Moddy
- `relayed_messages` (JSONB) - Messages relayés (array)
- `created_at` (TIMESTAMPTZ) - Date de création

**Index:**
- `idx_interserver_original_message` sur `original_message_id`
- `idx_interserver_author` sur `author_id`
- `idx_interserver_status` sur `status`

**Structure de `relayed_messages`:**
```json
[
  {
    "guild_id": 123456789,
    "channel_id": 987654321,
    "message_id": 111222333
  }
]
```

---

## Système d'attributs et de données

Moddy utilise deux types de champs JSONB pour stocker les informations:

### Attributs (`attributes`)

Les **attributs** sont des flags/booléens ou des valeurs courtes qui définissent l'état d'une entité.

**Stockage:**
- Attributs booléens TRUE: stockés comme `{"ATTRIBUT": true}`
- Attributs booléens FALSE: l'attribut n'existe pas dans le JSON
- Attributs avec valeur: stockés comme `{"ATTRIBUT": "valeur"}`

**Exemples:**
```json
{
  "TEAM": true,
  "PREMIUM": true,
  "LANG": "FR",
  "BLACKLISTED": true
}
```

### Données (`data`)

Les **données** contiennent des informations structurées plus complexes, comme les configurations de modules.

**Structure imbriquée:**
```json
{
  "modules": {
    "starboard": {
      "enabled": true,
      "channel_id": 123456789,
      "threshold": 3
    }
  },
  "preferences": {
    "timezone": "Europe/Paris",
    "notifications": true
  }
}
```

---

## Syntaxe PostgreSQL et JSONB

### Opérateurs JSONB

| Opérateur | Description | Exemple |
|-----------|-------------|---------|
| `->` | Accès à une clé (retourne JSON) | `data->'modules'` |
| `->>` | Accès à une clé (retourne TEXT) | `data->>'lang'` |
| `?` | Vérifie l'existence d'une clé | `attributes ? 'PREMIUM'` |
| `@>` | Contient (inclusion) | `attributes @> '{"LANG": "FR"}'` |
| `<@` | Est contenu dans | `'{"a": 1}' <@ data` |
| `?|` | Contient une des clés | `attributes ?| array['PREMIUM', 'BETA']` |
| `?&` | Contient toutes les clés | `attributes ?& array['TEAM', 'PREMIUM']` |

### Exemples pratiques

```sql
-- Vérifier si un attribut existe
SELECT user_id FROM users WHERE attributes ? 'PREMIUM';

-- Récupérer la valeur d'un attribut
SELECT attributes->>'LANG' as lang FROM users WHERE user_id = 123;

-- Vérifier une valeur spécifique
SELECT * FROM users WHERE attributes @> '{"LANG": "FR"}';

-- Navigation dans des JSON imbriqués
SELECT data->'modules'->'starboard'->>'channel_id'
FROM guilds WHERE guild_id = 123;

-- Mettre à jour un champ JSONB (remplace tout)
UPDATE users
SET attributes = '{"PREMIUM": true, "LANG": "FR"}'::jsonb
WHERE user_id = 123;

-- Mettre à jour un champ JSONB (fusion)
UPDATE users
SET attributes = attributes || '{"NEW_ATTR": true}'::jsonb
WHERE user_id = 123;

-- Supprimer une clé
UPDATE users
SET attributes = attributes - 'BLACKLISTED'
WHERE user_id = 123;

-- Vérifier plusieurs attributs
SELECT user_id FROM users
WHERE attributes ?& array['TEAM', 'PREMIUM'];
```

---

## Exemples de requêtes utiles

### Dashboard du Support Bot

#### 1. Statistiques générales
```sql
-- Nombre total d'utilisateurs
SELECT COUNT(*) FROM users;

-- Nombre d'utilisateurs premium
SELECT COUNT(*) FROM users WHERE attributes ? 'PREMIUM';

-- Nombre d'utilisateurs blacklistés
SELECT COUNT(*) FROM users WHERE attributes ? 'BLACKLISTED';

-- Nombre de serveurs
SELECT COUNT(*) FROM guilds;

-- Nombre de serveurs premium
SELECT COUNT(*) FROM guilds WHERE attributes ? 'PREMIUM';

-- Nombre d'erreurs dans les dernières 24h
SELECT COUNT(*) FROM errors
WHERE timestamp > NOW() - INTERVAL '24 hours';

-- Nombre de cases de modération ouvertes
SELECT COUNT(*) FROM moderation_cases WHERE status = 'open';
```

#### 2. Informations sur les staffs
```sql
-- Liste de tous les staffs avec leurs rôles
SELECT
  user_id,
  roles,
  denied_commands,
  created_at,
  updated_at
FROM staff_permissions
ORDER BY created_at;

-- Compter les staffs par rôle
SELECT
  jsonb_array_elements_text(roles) as role,
  COUNT(*) as count
FROM staff_permissions
GROUP BY role;

-- Trouver tous les managers
SELECT user_id, roles
FROM staff_permissions
WHERE roles @> '["Manager"]'::jsonb;

-- Vérifier les permissions d'un staff spécifique
SELECT * FROM staff_permissions WHERE user_id = 123456789;
```

#### 3. Cases de modération
```sql
-- Toutes les cases ouvertes
SELECT
  case_id,
  case_type,
  sanction_type,
  entity_type,
  entity_id,
  reason,
  created_by,
  created_at
FROM moderation_cases
WHERE status = 'open'
ORDER BY created_at DESC;

-- Cases d'un utilisateur spécifique
SELECT * FROM moderation_cases
WHERE entity_type = 'user' AND entity_id = 123456789
ORDER BY created_at DESC;

-- Statistiques des sanctions
SELECT
  sanction_type,
  status,
  COUNT(*) as count
FROM moderation_cases
GROUP BY sanction_type, status;

-- Cases créées par un staff
SELECT * FROM moderation_cases
WHERE created_by = 123456789
ORDER BY created_at DESC;
```

#### 4. Erreurs récentes
```sql
-- 50 dernières erreurs
SELECT
  error_code,
  error_type,
  message,
  file_source,
  user_id,
  guild_id,
  timestamp
FROM errors
ORDER BY timestamp DESC
LIMIT 50;

-- Erreurs par type
SELECT
  error_type,
  COUNT(*) as count
FROM errors
WHERE timestamp > NOW() - INTERVAL '7 days'
GROUP BY error_type
ORDER BY count DESC;

-- Erreurs d'un utilisateur
SELECT * FROM errors
WHERE user_id = 123456789
ORDER BY timestamp DESC;
```

#### 5. Configurations des serveurs
```sql
-- Serveurs avec le module starboard activé
SELECT
  guild_id,
  data->'modules'->'starboard' as starboard_config
FROM guilds
WHERE data->'modules'->'starboard'->>'enabled' = 'true';

-- Serveurs avec interserveur activé
SELECT
  guild_id,
  data->'modules'->'interserver' as interserver_config
FROM guilds
WHERE data->'modules'->'interserver'->>'enabled' = 'true';

-- Récupérer toutes les configurations d'un serveur
SELECT
  guild_id,
  attributes,
  data,
  created_at,
  updated_at
FROM guilds
WHERE guild_id = 123456789;
```

#### 6. Audit et historique
```sql
-- Historique des changements d'attributs récents
SELECT
  entity_type,
  entity_id,
  attribute_name,
  old_value,
  new_value,
  changed_by,
  changed_at,
  reason
FROM attribute_changes
ORDER BY changed_at DESC
LIMIT 100;

-- Voir qui a blacklisté un utilisateur
SELECT * FROM attribute_changes
WHERE entity_type = 'user'
  AND entity_id = 123456789
  AND attribute_name = 'BLACKLISTED'
ORDER BY changed_at DESC;

-- Modifications faites par un staff
SELECT * FROM attribute_changes
WHERE changed_by = 123456789
ORDER BY changed_at DESC;
```

---

## Connexion depuis un bot externe

Voici un exemple complet de comment se connecter et requêter la base de données depuis un autre bot Discord:

```python
import discord
from discord.ext import commands
import asyncpg
import json

class SupportBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix='!', intents=discord.Intents.all())
        self.db_pool = None

    async def setup_hook(self):
        # Connexion à la base de données
        self.db_pool = await asyncpg.create_pool(
            'postgresql://moddy:password@localhost/moddy',
            min_size=5,
            max_size=20
        )
        print("✅ Connected to Moddy database")

    async def close(self):
        if self.db_pool:
            await self.db_pool.close()
        await super().close()

bot = SupportBot()

@bot.command()
async def staff_info(ctx, user_id: int):
    """Affiche les informations d'un staff"""
    async with bot.db_pool.acquire() as conn:
        # Récupérer les permissions
        perms = await conn.fetchrow(
            "SELECT * FROM staff_permissions WHERE user_id = $1",
            user_id
        )

        if not perms:
            await ctx.send("❌ Cet utilisateur n'est pas un membre du staff")
            return

        # Parser les données JSON
        roles = json.loads(perms['roles'])
        denied = json.loads(perms['denied_commands'])

        # Créer l'embed
        embed = discord.Embed(
            title=f"📋 Informations Staff",
            color=0x5865F2
        )
        embed.add_field(name="User ID", value=user_id, inline=False)
        embed.add_field(name="Rôles", value=", ".join(roles), inline=False)
        embed.add_field(
            name="Commandes interdites",
            value=", ".join(denied) if denied else "Aucune",
            inline=False
        )
        embed.add_field(
            name="Créé le",
            value=f"<t:{int(perms['created_at'].timestamp())}:F>",
            inline=True
        )

        await ctx.send(embed=embed)

@bot.command()
async def open_cases(ctx):
    """Affiche toutes les cases ouvertes"""
    async with bot.db_pool.acquire() as conn:
        cases = await conn.fetch("""
            SELECT
                case_id,
                case_type,
                sanction_type,
                entity_type,
                entity_id,
                reason,
                created_at
            FROM moderation_cases
            WHERE status = 'open'
            ORDER BY created_at DESC
            LIMIT 10
        """)

        if not cases:
            await ctx.send("✅ Aucune case ouverte")
            return

        embed = discord.Embed(
            title="📋 Cases ouvertes",
            description=f"{len(cases)} case(s) ouverte(s)",
            color=0xED4245
        )

        for case in cases:
            embed.add_field(
                name=f"Case {case['case_id']}",
                value=(
                    f"**Type:** {case['sanction_type']}\n"
                    f"**Entité:** {case['entity_type']} {case['entity_id']}\n"
                    f"**Raison:** {case['reason'][:100]}\n"
                    f"**Créée:** <t:{int(case['created_at'].timestamp())}:R>"
                ),
                inline=False
            )

        await ctx.send(embed=embed)

@bot.command()
async def error_info(ctx, error_code: str):
    """Affiche les détails d'un code erreur"""
    async with bot.db_pool.acquire() as conn:
        error = await conn.fetchrow(
            "SELECT * FROM errors WHERE error_code = $1",
            error_code.upper()
        )

        if not error:
            await ctx.send("❌ Code erreur introuvable")
            return

        embed = discord.Embed(
            title=f"🔴 Erreur {error['error_code']}",
            color=0xED4245
        )
        embed.add_field(name="Type", value=error['error_type'], inline=True)
        embed.add_field(
            name="Timestamp",
            value=f"<t:{int(error['timestamp'].timestamp())}:F>",
            inline=True
        )
        embed.add_field(name="Message", value=error['message'][:1024], inline=False)
        embed.add_field(
            name="Source",
            value=f"{error['file_source']}:{error['line_number']}",
            inline=False
        )

        if error['user_id']:
            embed.add_field(name="User ID", value=error['user_id'], inline=True)
        if error['guild_id']:
            embed.add_field(name="Guild ID", value=error['guild_id'], inline=True)
        if error['command']:
            embed.add_field(name="Commande", value=error['command'], inline=True)

        await ctx.send(embed=embed)

@bot.command()
async def guild_config(ctx, guild_id: int):
    """Affiche la configuration d'un serveur"""
    async with bot.db_pool.acquire() as conn:
        guild = await conn.fetchrow(
            "SELECT * FROM guilds WHERE guild_id = $1",
            guild_id
        )

        if not guild:
            await ctx.send("❌ Serveur non trouvé dans la base de données")
            return

        # Parser les JSONB
        attributes = guild['attributes'] if isinstance(guild['attributes'], dict) else {}
        data = guild['data'] if isinstance(guild['data'], dict) else {}

        embed = discord.Embed(
            title=f"⚙️ Configuration du serveur {guild_id}",
            color=0x5865F2
        )

        # Attributs
        attr_list = []
        if attributes.get('PREMIUM'):
            attr_list.append("⭐ Premium")
        if attributes.get('BLACKLISTED'):
            attr_list.append("🚫 Blacklisté")
        if attributes.get('BETA'):
            attr_list.append("🧪 Beta")

        embed.add_field(
            name="Attributs",
            value=", ".join(attr_list) if attr_list else "Aucun",
            inline=False
        )

        # Modules
        modules = data.get('modules', {})
        if modules:
            modules_text = []
            for module_name, module_config in modules.items():
                enabled = "✅" if module_config.get('enabled') else "❌"
                modules_text.append(f"{enabled} {module_name}")

            embed.add_field(
                name="Modules",
                value="\n".join(modules_text),
                inline=False
            )

        await ctx.send(embed=embed)

bot.run('TOKEN')
```

---

## Bonnes pratiques

1. **Utilisez toujours des requêtes paramétrées** pour éviter les injections SQL:
   ```python
   # ✅ Bon
   await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)

   # ❌ Mauvais
   await conn.fetchrow(f"SELECT * FROM users WHERE user_id = {user_id}")
   ```

2. **Utilisez un pool de connexions** pour de meilleures performances

3. **Gérez les erreurs de connexion** proprement:
   ```python
   try:
       async with pool.acquire() as conn:
           result = await conn.fetchrow(...)
   except asyncpg.PostgresError as e:
       logger.error(f"Database error: {e}")
   ```

4. **Vérifiez toujours si les résultats existent** avant de les utiliser:
   ```python
   row = await conn.fetchrow(...)
   if not row:
       # Gérer le cas où l'entité n'existe pas
   ```

5. **Utilisez les index GIN** pour les recherches JSONB efficaces

---

## Support

Pour toute question sur la base de données, contactez l'équipe de développement de Moddy.

**Version de la documentation:** 1.0
**Date:** 2025-12-10
**Auteur:** juthing (avec l'aide de Claude)
