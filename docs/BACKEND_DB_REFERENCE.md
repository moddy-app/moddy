# Moddy — Référence complète base de données (Backend)

> Document destiné au développeur backend.
> Couvre le schéma intégral, les structures JSONB, le système de permissions staff, et les conventions à respecter.

---

## Table des matières

1. [Connexion](#1-connexion)
2. [Vue d'ensemble des tables](#2-vue-densemble-des-tables)
3. [Table `users`](#3-table-users)
4. [Table `guilds`](#4-table-guilds)
5. [Table `staff_permissions`](#5-table-staff_permissions)
6. [Table `moderation_cases`](#6-table-moderation_cases)
7. [Table `attribute_changes`](#7-table-attribute_changes)
8. [Table `errors`](#8-table-errors)
9. [Table `reminders`](#9-table-reminders)
10. [Table `saved_messages`](#10-table-saved_messages)
11. [Table `saved_roles`](#11-table-saved_roles)
12. [Table `interserver_messages`](#12-table-interserver_messages)
13. [Système d'attributs](#13-système-dattributs)
14. [Configurations des modules (guilds.data)](#14-configurations-des-modules-guildsdata)
15. [Système de permissions staff](#15-système-de-permissions-staff)
16. [Règles métier importantes](#16-règles-métier-importantes)
17. [Requêtes de référence](#17-requêtes-de-référence)

---

## 1. Connexion

- **Technologie** : PostgreSQL (hébergé sur Railway)
- **Variable d'environnement** : `DATABASE_URL`
- **Format** : `postgresql://user:password@host/dbname`
- **Driver Python côté bot** : `asyncpg` avec pool de connexions (min 1, max 10)

---

## 2. Vue d'ensemble des tables

| Table | Rôle |
|---|---|
| `users` | Données des utilisateurs Discord |
| `guilds` | Données des serveurs Discord |
| `staff_permissions` | Rôles et permissions des membres du staff |
| `moderation_cases` | Cases de modération (sanctions globales/inter-serveur) |
| `attribute_changes` | Audit de tous les changements d'attributs |
| `errors` | Log des erreurs du bot |
| `reminders` | Rappels planifiés des utilisateurs |
| `saved_messages` | Messages sauvegardés par les utilisateurs |
| `saved_roles` | Rôles Discord sauvegardés par serveur/utilisateur (module auto-restore) |
| `interserver_messages` | Messages du système de réseau inter-serveur |

---

## 3. Table `users`

Stocke chaque utilisateur Discord qui a interagi avec le bot. **Créée automatiquement** lors du premier contact.

### Schéma

```sql
CREATE TABLE users (
    user_id             BIGINT PRIMARY KEY,           -- ID Discord (snowflake)
    attributes          JSONB DEFAULT '{}'::jsonb,    -- Flags et états de l'utilisateur
    data                JSONB DEFAULT '{}'::jsonb,    -- Données structurées (préférences, etc.)
    stripe_customer_id  VARCHAR(50),                  -- ID client Stripe (ex: cus_UAf6a2WKTw6yCI)
    email               VARCHAR(255),                 -- Adresse email
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_users_attributes ON users USING GIN (attributes);
```

### Champ `attributes` — structure

Les attributs sont des **flags** ou des **valeurs courtes**. Règle fondamentale :
- `true` → la clé existe avec la valeur `true`
- `false` → **la clé n'existe pas** (ne jamais stocker `false`)
- Valeur texte → stockée directement

```json
{
  "TEAM": true,
  "PREMIUM": true,
  "BETA": true,
  "BLACKLISTED": true,
  "LANG": "FR"
}
```

| Attribut | Type | Description |
|---|---|---|
| `TEAM` | `true` | Membre du staff (positionné automatiquement lors du ranking) |
| `PREMIUM` | `true` | Utilisateur avec abonnement premium |
| `BETA` | `true` | Testeur bêta |
| `BLACKLISTED` | `true` | Utilisateur blacklisté globalement |
| `LANG` | `"FR"` / `"EN"` | Langue préférée de l'utilisateur |

> **Important** : L'attribut `TEAM` est géré **automatiquement** par le système staff — il est ajouté lors d'un `add_staff_role` et retiré quand le staff n'a plus aucun rôle. Ne pas le manipuler directement.

### Champ `data` — structure

Utilisé pour des données structurées plus complexes (préférences, états internes).

```json
{
  "preferences": {
    "timezone": "Europe/Paris",
    "notifications": true
  }
}
```

### Exemples de requêtes

```sql
-- Récupérer un utilisateur
SELECT * FROM users WHERE user_id = 123456789;

-- Vérifier si premium
SELECT attributes ? 'PREMIUM' FROM users WHERE user_id = 123456789;

-- Tous les utilisateurs premium
SELECT user_id FROM users WHERE attributes ? 'PREMIUM';

-- Tous les utilisateurs blacklistés
SELECT user_id FROM users WHERE attributes ? 'BLACKLISTED';

-- Utilisateurs avec langue FR
SELECT user_id FROM users WHERE attributes @> '{"LANG": "FR"}';

-- Chercher par stripe_customer_id
SELECT * FROM users WHERE stripe_customer_id = 'cus_UAf6a2WKTw6yCI';

-- Chercher par email
SELECT * FROM users WHERE email = 'user@example.com';

-- Lier un customer Stripe à un user
UPDATE users
SET stripe_customer_id = 'cus_UAf6a2WKTw6yCI', updated_at = NOW()
WHERE user_id = 123456789;
```

---

## 4. Table `guilds`

Stocke chaque serveur Discord où Moddy est installé. **Créée automatiquement** lors du premier événement sur le serveur.

### Schéma

```sql
CREATE TABLE guilds (
    guild_id    BIGINT PRIMARY KEY,
    attributes  JSONB DEFAULT '{}'::jsonb,
    data        JSONB DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_guilds_attributes ON guilds USING GIN (attributes);
```

### Champ `attributes`

Même logique que `users.attributes`.

| Attribut | Type | Description |
|---|---|---|
| `PREMIUM` | `true` | Serveur avec abonnement premium |
| `BETA` | `true` | Serveur testeur bêta |
| `BLACKLISTED` | `true` | Serveur blacklisté globalement |

### Champ `data` — structure complète

Contient toutes les **configurations des modules** activés sur le serveur. Voir [Section 14](#14-configurations-des-modules-guildsdata) pour le détail complet de chaque module.

```json
{
  "modules": {
    "starboard": { ... },
    "welcome_channel": { ... },
    "welcome_dm": { ... },
    "auto_role": { ... },
    "auto_restore_roles": { ... },
    "interserver": { ... },
    "youtube_notifications": { ... }
  }
}
```

---

## 5. Table `staff_permissions`

Contient les rôles et permissions de chaque membre du staff. Une entrée par staff.

### Schéma

```sql
CREATE TABLE staff_permissions (
    user_id             BIGINT PRIMARY KEY,
    roles               JSONB DEFAULT '[]'::jsonb,    -- Array de rôles
    denied_commands     JSONB DEFAULT '[]'::jsonb,    -- Commandes explicitement interdites
    role_permissions    JSONB DEFAULT '{}'::jsonb,    -- Permissions custom par rôle
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    created_by          BIGINT,                       -- ID Discord du créateur
    updated_by          BIGINT                        -- ID Discord du dernier modificateur
);

CREATE INDEX idx_staff_permissions_roles ON staff_permissions USING GIN (roles);
```

### Champ `roles` — valeurs possibles

```json
["Manager", "Dev"]
```

| Valeur | Niveau | Description |
|---|---|---|
| `"Dev"` | 1000 (hors hiérarchie) | Développeur — accès total, bypass toutes les vérifications |
| `"Manager"` | 100 | Manager — peut rank/unrank, accès à toutes les commandes métier |
| `"Supervisor_Mod"` | 50 | Superviseur Modération |
| `"Supervisor_Com"` | 50 | Superviseur Communication |
| `"Supervisor_Sup"` | 50 | Superviseur Support |
| `"Moderator"` | 10 | Modérateur |
| `"Communication"` | 10 | Équipe Communication |
| `"Support"` | 10 | Équipe Support |

> Un utilisateur peut avoir **plusieurs rôles simultanément** (ex: `["Manager", "Dev"]`).

### Champ `denied_commands` — format

Liste de commandes explicitement refusées, même si le rôle y donnerait normalement accès.
Format : `"<prefix>.<commande>"`

```json
["d.sql", "mod.blacklist", "t.flex"]
```

Préfixes disponibles :
- `t.` — commandes team (tous les staffs)
- `d.` — commandes dev
- `m.` — commandes management
- `mod.` — commandes modération
- `sup.` — commandes support
- `com.` — commandes communication

### Champ `role_permissions` — format

Permissions **custom** accordées ou gérées par rôle. Structure objet : `{ role: [permission1, permission2] }`.

```json
{
  "Moderator": ["case_create", "case_view", "interserver_delete"],
  "Support": ["ticket_view", "subscription_view"]
}
```

### Exemples de requêtes

```sql
-- Récupérer les permissions d'un staff
SELECT * FROM staff_permissions WHERE user_id = 123456789;

-- Tous les managers
SELECT user_id FROM staff_permissions
WHERE roles @> '["Manager"]'::jsonb;

-- Tous les membres du staff (toutes roles)
SELECT user_id, roles FROM staff_permissions ORDER BY created_at;

-- Vérifier si Dev
SELECT EXISTS(
  SELECT 1 FROM staff_permissions
  WHERE user_id = 123456789 AND roles @> '["Dev"]'::jsonb
);

-- Compter les staffs par rôle
SELECT jsonb_array_elements_text(roles) AS role, COUNT(*) AS count
FROM staff_permissions
GROUP BY role
ORDER BY count DESC;
```

---

## 6. Table `moderation_cases`

Cases de modération créées par le staff (sanctions globales ou inter-serveur).

### Schéma

```sql
CREATE TABLE moderation_cases (
    case_id         VARCHAR(8) PRIMARY KEY,   -- ID hex unique ex: "A1B2C3D4"
    case_type       VARCHAR(20) NOT NULL,     -- "interserver" | "global"
    sanction_type   VARCHAR(50) NOT NULL,     -- voir valeurs ci-dessous
    entity_type     VARCHAR(10) NOT NULL,     -- "user" | "guild"
    entity_id       BIGINT NOT NULL,          -- ID Discord de l'entité sanctionnée
    status          VARCHAR(20) DEFAULT 'open',  -- "open" | "closed"
    reason          TEXT NOT NULL,
    evidence        TEXT,                     -- Liens, screenshots, etc.
    duration        INTEGER,                  -- En secondes (pour timeouts)
    staff_notes     JSONB DEFAULT '[]'::jsonb,
    created_by      BIGINT,                   -- ID Discord du staff créateur
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_by      BIGINT,
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    closed_by       BIGINT,
    closed_at       TIMESTAMPTZ,
    close_reason    TEXT
);
```

### Valeurs de `case_type` et `sanction_type`

| `case_type` | `sanction_type` | Description |
|---|---|---|
| `"global"` | `"global_blacklist"` | Blacklist globale d'un utilisateur ou serveur |
| `"global"` | `"guild_blacklist"` | Blacklist d'un serveur spécifique |
| `"interserver"` | `"interserver_blacklist"` | Blacklist du réseau inter-serveur |
| `"interserver"` | `"interserver_timeout"` | Timeout temporaire sur le réseau inter-serveur |

### Champ `staff_notes` — format

```json
[
  {
    "staff_id": 123456789,
    "note": "Utilisateur averti, récidive possible",
    "timestamp": "2025-12-10T10:30:00+00:00"
  }
]
```

### Exemples de requêtes

```sql
-- Récupérer une case par ID
SELECT * FROM moderation_cases WHERE case_id = 'A1B2C3D4';

-- Toutes les cases ouvertes
SELECT * FROM moderation_cases WHERE status = 'open' ORDER BY created_at DESC;

-- Cases d'un utilisateur
SELECT * FROM moderation_cases
WHERE entity_type = 'user' AND entity_id = 123456789
ORDER BY created_at DESC;

-- Vérifier si un utilisateur est blacklisté globalement
SELECT EXISTS(
  SELECT 1 FROM moderation_cases
  WHERE entity_type = 'user'
    AND entity_id = 123456789
    AND sanction_type = 'global_blacklist'
    AND status = 'open'
);

-- Statistiques
SELECT sanction_type, status, COUNT(*) AS count
FROM moderation_cases
GROUP BY sanction_type, status;
```

---

## 7. Table `attribute_changes`

Audit automatique de tous les changements d'attributs (users et guilds). **Ne jamais écrire manuellement** depuis le backend — le bot le fait via `set_attribute()`.

### Schéma

```sql
CREATE TABLE attribute_changes (
    id              SERIAL PRIMARY KEY,
    entity_type     VARCHAR(10) CHECK (entity_type IN ('user', 'guild')),
    entity_id       BIGINT NOT NULL,
    attribute_name  VARCHAR(50),
    old_value       TEXT,        -- NULL si attribut créé
    new_value       TEXT,        -- NULL si attribut supprimé
    changed_by      BIGINT,      -- ID Discord du staff ayant effectué le changement
    changed_at      TIMESTAMPTZ DEFAULT NOW(),
    reason          TEXT
);

CREATE INDEX idx_attribute_changes_entity ON attribute_changes(entity_type, entity_id);
```

### Exemples de requêtes

```sql
-- Historique d'un utilisateur
SELECT * FROM attribute_changes
WHERE entity_type = 'user' AND entity_id = 123456789
ORDER BY changed_at DESC;

-- Qui a blacklisté un user
SELECT * FROM attribute_changes
WHERE entity_type = 'user' AND entity_id = 123456789
  AND attribute_name = 'BLACKLISTED'
ORDER BY changed_at DESC;

-- Actions d'un staff
SELECT * FROM attribute_changes
WHERE changed_by = 123456789
ORDER BY changed_at DESC;
```

---

## 8. Table `errors`

Log automatique de toutes les erreurs du bot.

### Schéma

```sql
CREATE TABLE errors (
    error_code      VARCHAR(8) PRIMARY KEY,   -- Code alphanumérique unique ex: "A1B2C3D4"
    error_type      VARCHAR(100),             -- Type Python ex: "ValueError"
    message         TEXT,
    file_source     VARCHAR(255),
    line_number     INTEGER,
    traceback       TEXT,
    user_id         BIGINT,
    guild_id        BIGINT,
    command         VARCHAR(100),
    timestamp       TIMESTAMPTZ DEFAULT NOW(),
    context         JSONB DEFAULT '{}'::jsonb,
    sentry_event_id VARCHAR(32),
    sentry_issue_id VARCHAR(20)
);
```

---

## 9. Table `reminders`

Rappels planifiés des utilisateurs.

### Schéma

```sql
CREATE TABLE reminders (
    id              SERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL,
    guild_id        BIGINT,
    channel_id      BIGINT,
    message         TEXT NOT NULL,
    remind_at       TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    sent            BOOLEAN DEFAULT FALSE,
    sent_at         TIMESTAMPTZ,
    failed          BOOLEAN DEFAULT FALSE,
    send_in_channel BOOLEAN DEFAULT FALSE    -- TRUE = salon, FALSE = DM
);
```

---

## 10. Table `saved_messages`

Messages sauvegardés par les utilisateurs via la commande `/save`.

### Schéma

```sql
CREATE TABLE saved_messages (
    id               SERIAL PRIMARY KEY,
    user_id          BIGINT NOT NULL,        -- Qui a sauvegardé
    message_id       BIGINT NOT NULL,        -- ID du message Discord
    channel_id       BIGINT NOT NULL,
    guild_id         BIGINT,
    author_id        BIGINT NOT NULL,        -- Auteur du message original
    author_username  TEXT,
    content          TEXT,
    attachments      JSONB DEFAULT '[]'::jsonb,
    embeds           JSONB DEFAULT '[]'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL,   -- Date du message original
    saved_at         TIMESTAMPTZ DEFAULT NOW(),
    message_url      TEXT,
    note             TEXT,
    raw_message_data JSONB DEFAULT '{}'::jsonb
);
```

---

## 11. Table `saved_roles`

Rôles Discord sauvegardés par utilisateur et par serveur. Utilisé par le module **Auto Restore Roles** pour réattribuer les rôles lors d'un retour.

### Schéma

```sql
CREATE TABLE saved_roles (
    id          SERIAL PRIMARY KEY,
    guild_id    BIGINT NOT NULL,
    user_id     BIGINT NOT NULL,
    roles       BIGINT[] NOT NULL,    -- Array d'IDs de rôles Discord
    username    TEXT,
    saved_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(guild_id, user_id)         -- Un seul enregistrement par (serveur, utilisateur)
);
```

---

## 12. Table `interserver_messages`

Messages du réseau inter-serveur (système de relay de messages entre serveurs).

### Schéma

```sql
CREATE TABLE interserver_messages (
    moddy_id             VARCHAR(8) PRIMARY KEY,  -- ID Moddy unique ex: "A1B2C3D4"
    original_message_id  BIGINT NOT NULL,
    original_guild_id    BIGINT NOT NULL,
    original_channel_id  BIGINT NOT NULL,
    author_id            BIGINT NOT NULL,
    author_username      TEXT,
    content              TEXT,
    timestamp            TIMESTAMPTZ DEFAULT NOW(),
    status               VARCHAR(20) DEFAULT 'active',  -- "active" | "deleted"
    is_moddy_team        BOOLEAN DEFAULT FALSE,
    relayed_messages     JSONB DEFAULT '[]'::jsonb,
    created_at           TIMESTAMPTZ DEFAULT NOW()
);
```

### Champ `relayed_messages` — format

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

## 13. Système d'attributs

### Principe fondamental

Les attributs sont stockés dans le champ JSONB `attributes` des tables `users` et `guilds`.

**Règle d'or :**
- Attribut actif (`true`) → **clé présente** avec valeur `true`
- Attribut inactif (`false`) → **clé absente** (jamais de `false` stocké)
- Attribut avec valeur → clé présente avec la valeur (`"LANG": "FR"`)

```json
-- Utilisateur PREMIUM + TEAM + langue FR
{"PREMIUM": true, "TEAM": true, "LANG": "FR"}

-- Utilisateur sans attribut
{}
```

### Opérateurs JSONB utiles

```sql
-- Vérifier l'existence d'une clé
attributes ? 'PREMIUM'

-- Vérifier une valeur exacte
attributes @> '{"LANG": "FR"}'

-- Plusieurs clés en même temps (ET logique)
attributes ?& array['TEAM', 'PREMIUM']

-- Au moins une des clés (OU logique)
attributes ?| array['PREMIUM', 'BETA']

-- Ajouter/modifier une clé (fusion)
attributes || '{"PREMIUM": true}'::jsonb

-- Supprimer une clé
attributes - 'BLACKLISTED'
```

### Comment modifier un attribut (depuis le backend)

```sql
-- Activer PREMIUM
UPDATE users
SET attributes = attributes || '{"PREMIUM": true}'::jsonb,
    updated_at = NOW()
WHERE user_id = 123456789;

-- Désactiver PREMIUM (supprimer la clé)
UPDATE users
SET attributes = attributes - 'PREMIUM',
    updated_at = NOW()
WHERE user_id = 123456789;

-- Définir la langue
UPDATE users
SET attributes = attributes || '{"LANG": "EN"}'::jsonb,
    updated_at = NOW()
WHERE user_id = 123456789;
```

> **Important** : Penser à insérer une ligne dans `attribute_changes` pour l'audit si le changement est significatif (premium, blacklist, etc.).

---

## 14. Configurations des modules (guilds.data)

Toutes les configurations de modules sont dans `guilds.data.modules.<module_id>`. Chaque module a un champ `enabled` (boolean).

### Module `starboard`

```json
{
  "channel_id": 123456789,
  "reaction_count": 5,
  "emoji": "⭐"
}
```

| Champ | Type | Défaut | Description |
|---|---|---|---|
| `channel_id` | `integer` | `null` | ID du salon starboard (obligatoire pour activer) |
| `reaction_count` | `integer` | `5` | Nombre de réactions requises |
| `emoji` | `string` | `"⭐"` | Emoji déclencheur |

### Module `welcome_channel`

```json
{
  "channel_id": 123456789,
  "message_template": "Bienvenue {user} sur le serveur !",
  "mention_user": true,
  "embed_enabled": false,
  "embed_title": "Bienvenue !",
  "embed_description": null,
  "embed_color": 5793266,
  "embed_footer": null,
  "embed_image_url": null,
  "embed_thumbnail_enabled": true,
  "embed_author_enabled": false
}
```

| Champ | Type | Défaut | Description |
|---|---|---|---|
| `channel_id` | `integer` | `null` | Salon de bienvenue (obligatoire) |
| `message_template` | `string` | `"Bienvenue {user}..."` | Template avec `{user}` pour la mention |
| `mention_user` | `bool` | `true` | Mentionner le nouvel arrivant |
| `embed_enabled` | `bool` | `false` | Activer le mode embed |
| `embed_title` | `string` | `"Bienvenue !"` | Titre de l'embed |
| `embed_color` | `integer` | `5793266` (0x5865F2) | Couleur hex de l'embed en décimal |

### Module `welcome_dm`

```json
{
  "message_template": "Bienvenue sur {server} !",
  "embed_enabled": false,
  "embed_title": "Bienvenue !",
  "embed_description": null,
  "embed_color": 5793266
}
```

### Module `auto_role`

```json
{
  "role_ids": [123456789, 987654321]
}
```

| Champ | Type | Description |
|---|---|---|
| `role_ids` | `integer[]` | Liste des IDs de rôles à attribuer automatiquement |

### Module `auto_restore_roles`

```json
{
  "enabled": true,
  "ignored_role_ids": [111222333]
}
```

| Champ | Type | Description |
|---|---|---|
| `ignored_role_ids` | `integer[]` | Rôles à ne pas restaurer |

### Module `interserver`

```json
{
  "channel_id": 123456789,
  "network_id": "default",
  "webhook_url": "https://discord.com/api/webhooks/..."
}
```

### Accéder/modifier une configuration de module

```sql
-- Lire la configuration starboard d'un serveur
SELECT data->'modules'->'starboard' AS config
FROM guilds WHERE guild_id = 123456789;

-- Vérifier si le starboard est actif (channel_id non null = actif)
SELECT data->'modules'->'starboard'->>'channel_id' IS NOT NULL
FROM guilds WHERE guild_id = 123456789;

-- Lire le channel_id du welcome
SELECT (data->'modules'->'welcome_channel'->>'channel_id')::BIGINT
FROM guilds WHERE guild_id = 123456789;

-- Mettre à jour un champ spécifique d'un module
UPDATE guilds
SET data = jsonb_set(data, '{modules,starboard,reaction_count}', '3'::jsonb),
    updated_at = NOW()
WHERE guild_id = 123456789;

-- Désactiver un module (supprimer sa config)
UPDATE guilds
SET data = data #- '{modules,starboard}',
    updated_at = NOW()
WHERE guild_id = 123456789;
```

---

## 15. Système de permissions staff

### Hiérarchie des rôles

```
Dev (1000)          — Hors hiérarchie, accès total
    │
Manager (100)       — Peut rank/unrank, accès toutes commandes métier
    │
Supervisor_Mod (50) — Supervise les modérateurs
Supervisor_Com (50) — Supervise la communication
Supervisor_Sup (50) — Supervise le support
    │
Moderator (10)      — Gestion des cases et inter-serveur
Communication (10)  — Annonces et broadcast
Support (10)        — Tickets et abonnements
```

### Accès par type de commande

| Préfixe | Commandes | Rôles autorisés |
|---|---|---|
| `t.` | Équipe (flex, invite, serverinfo) | **Tous les staffs** |
| `d.` | Développeur (reload, sql, stats…) | `Dev` uniquement |
| `m.` | Management (rank, unrank, setstaff…) | `Manager` uniquement |
| `mod.` | Modération (blacklist, cases…) | `Manager`, `Supervisor_Mod`, `Moderator` |
| `sup.` | Support (tickets, abonnements…) | `Manager`, `Supervisor_Sup`, `Support` |
| `com.` | Communication (annonces…) | `Manager`, `Supervisor_Com`, `Communication` |

### Permissions disponibles par rôle

#### Rôle `Moderator`
- `case_create` — Créer des cases de modération
- `case_view` — Voir les cases
- `case_list` — Lister les cases
- `case_edit` — Modifier les cases
- `case_close` — Fermer les cases
- `case_note` — Ajouter des notes aux cases
- `interserver_info` — Info sur les messages inter-serveur
- `interserver_delete` — Supprimer des messages inter-serveur

#### Rôle `Support`
- `ticket_view` — Voir les tickets
- `ticket_close` — Fermer les tickets
- `ticket_create` — Créer des tickets
- `subscription_view` — Voir les abonnements
- `subscription_manage` — Gérer les abonnements (remboursements)

#### Rôle `Communication`
- `announce` — Envoyer des annonces
- `broadcast` — Broadcast de messages

#### Rôle `Supervisor_Mod`
Tout ce que `Moderator` peut faire + `manage_mod` (gérer les modérateurs)

#### Rôle `Supervisor_Sup`
Tout ce que `Support` peut faire + `manage_sup` (gérer les agents support)

#### Rôle `Supervisor_Com`
Tout ce que `Communication` peut faire + `manage_com` (gérer l'équipe com)

#### Rôle `Manager`
- `rank` — Ajouter des membres au staff
- `unrank` — Retirer des membres du staff
- `setstaff` — Gérer les permissions staff
- `stafflist` — Voir la liste du staff
- `staffinfo` — Voir les informations d'un staff

#### Tous les staffs (commandes `t.`)
- `flex` — Vérification d'appartenance à l'équipe
- `invite` — Créer des invitations
- `serverinfo` — Voir les infos d'un serveur

### Logique d'autorisation (à implémenter côté backend)

```
1. L'utilisateur a-t-il l'attribut TEAM dans users.attributes ?
   → Non + pas Dev → Refusé

2. Le préfixe de commande correspond-il à l'un de ses rôles ?
   → Non → Refusé

3. La commande est-elle dans denied_commands ?
   → Oui → Refusé

4. → Autorisé
```

### Modification des rôles staff

```sql
-- Ajouter un rôle à un staff existant
UPDATE staff_permissions
SET roles = roles || '["Moderator"]'::jsonb,
    updated_by = <staff_id>,
    updated_at = NOW()
WHERE user_id = 123456789;

-- Retirer un rôle
UPDATE staff_permissions
SET roles = (
  SELECT jsonb_agg(r)
  FROM jsonb_array_elements_text(roles) r
  WHERE r != 'Moderator'
),
    updated_by = <staff_id>,
    updated_at = NOW()
WHERE user_id = 123456789;

-- Créer un nouveau staff
INSERT INTO staff_permissions (user_id, roles, created_by, updated_by)
VALUES (123456789, '["Support"]'::jsonb, <creator_id>, <creator_id>);

-- Ne pas oublier de mettre le flag TEAM sur l'utilisateur
UPDATE users
SET attributes = attributes || '{"TEAM": true}'::jsonb,
    updated_at = NOW()
WHERE user_id = 123456789;

-- Supprimer complètement un staff
DELETE FROM staff_permissions WHERE user_id = 123456789;

-- Retirer le flag TEAM
UPDATE users
SET attributes = attributes - 'TEAM',
    updated_at = NOW()
WHERE user_id = 123456789;
```

---

## 16. Règles métier importantes

1. **Création auto des entités** : `users` et `guilds` sont créées automatiquement par le bot lors du premier accès via `INSERT ... ON CONFLICT DO NOTHING`. Le backend peut faire de même.

2. **Attribut TEAM synchronisé avec staff_permissions** : Quand un utilisateur est ajouté au staff → `TEAM: true` dans `users.attributes`. Quand retiré de tous les rôles → supprimer la clé `TEAM`. Ces deux opérations doivent toujours se faire ensemble.

3. **FALSE non stocké** : Ne jamais insérer `{"PREMIUM": false}`. Pour "désactiver", supprimer la clé.

4. **case_id en hex** : Les IDs de cases sont des chaînes hex de 8 caractères en majuscules (`secrets.token_hex(4).upper()`). Vérifier l'unicité avant insertion.

5. **audit attribute_changes** : Toute modification d'attribut significative (PREMIUM, BLACKLISTED, TEAM) doit être loguée dans `attribute_changes` pour la traçabilité.

6. **Timestamps toujours en UTC** : Toutes les colonnes `*_at` utilisent `TIMESTAMPTZ`. Toujours stocker en UTC.

7. **IDs Discord en BIGINT** : Les snowflakes Discord dépassent 32 bits, toujours utiliser `BIGINT`.

---

## 17. Requêtes de référence

### Statistiques générales

```sql
SELECT
  (SELECT COUNT(*) FROM users) AS total_users,
  (SELECT COUNT(*) FROM users WHERE attributes ? 'PREMIUM') AS premium_users,
  (SELECT COUNT(*) FROM users WHERE attributes ? 'BLACKLISTED') AS blacklisted_users,
  (SELECT COUNT(*) FROM users WHERE stripe_customer_id IS NOT NULL) AS stripe_users,
  (SELECT COUNT(*) FROM guilds) AS total_guilds,
  (SELECT COUNT(*) FROM guilds WHERE attributes ? 'PREMIUM') AS premium_guilds,
  (SELECT COUNT(*) FROM staff_permissions) AS total_staff,
  (SELECT COUNT(*) FROM moderation_cases WHERE status = 'open') AS open_cases;
```

### Profil complet d'un utilisateur

```sql
SELECT
  u.user_id,
  u.attributes,
  u.stripe_customer_id,
  u.email,
  u.created_at,
  sp.roles AS staff_roles,
  sp.denied_commands,
  (SELECT COUNT(*) FROM moderation_cases
   WHERE entity_type = 'user' AND entity_id = u.user_id) AS total_cases,
  (SELECT COUNT(*) FROM moderation_cases
   WHERE entity_type = 'user' AND entity_id = u.user_id AND status = 'open') AS open_cases
FROM users u
LEFT JOIN staff_permissions sp ON sp.user_id = u.user_id
WHERE u.user_id = 123456789;
```

### Staffs avec leurs niveaux

```sql
SELECT
  sp.user_id,
  sp.roles,
  sp.denied_commands,
  u.attributes,
  sp.created_at,
  sp.created_by
FROM staff_permissions sp
JOIN users u ON u.user_id = sp.user_id
ORDER BY sp.created_at;
```

### Serveurs avec modules actifs

```sql
SELECT
  guild_id,
  data->'modules' AS modules_config,
  (SELECT COUNT(*) FROM jsonb_object_keys(data->'modules')) AS modules_count
FROM guilds
WHERE data ? 'modules'
  AND data->'modules' != '{}'::jsonb
ORDER BY guild_id;
```
