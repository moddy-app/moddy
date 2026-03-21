# 📦 Système de Modules de Serveur - Moddy

## Table des matières
1. [Vue d'ensemble](#vue-densemble)
2. [Architecture](#architecture)
3. [Comment ça marche](#comment-ça-marche)
4. [Créer un nouveau module](#créer-un-nouveau-module)
5. [Exemple complet : Module Welcome](#exemple-complet--module-welcome)
6. [Commande /config](#commande-config)
7. [Internationalisation (i18n)](#internationalisation-i18n)
8. [Stockage en base de données](#stockage-en-base-de-données)

---

## Vue d'ensemble

Le système de modules de Moddy permet de créer des fonctionnalités configurables par serveur (tickets, auto-rôle, messages de bienvenue, etc.). Chaque module peut être configuré indépendamment via la commande `/config` avec une interface moderne en Composants V2.

### Principes fondamentaux

- **Séparation des préoccupations** : La logique métier (module) est séparée de la configuration (UI)
- **Configuration en JSON** : Toutes les configurations sont stockées dans la colonne `data` de la table `guilds` en JSON
- **Interface moderne** : Utilisation des Composants V2 de Discord pour une meilleure UX
- **Chargement au démarrage** : Les modules sont automatiquement chargés depuis la DB au démarrage du bot
- **Multilingue** : Support complet de l'i18n via le système de traductions

---

## Architecture

### Structure des dossiers

```
MODDY/
├── modules/                          # Modules de serveur
│   ├── __init__.py
│   ├── module_manager.py            # Gestionnaire central des modules
│   ├── welcome.py                   # Exemple : Module Welcome
│   ├── ticket.py                    # Futur : Module Ticket
│   └── configs/                     # Configurations UI
│       ├── __init__.py
│       ├── welcome_config.py        # UI de config pour Welcome
│       └── ticket_config.py         # Futur : UI de config pour Ticket
├── cogs/
│   ├── config.py                    # Commande /config (point d'entrée)
│   └── module_events.py             # Gestionnaire d'événements pour modules
└── locales/
    ├── fr.json                      # Traductions françaises
    └── en-US.json                   # Traductions anglaises
```

### Composants principaux

1. **ModuleManager** (`modules/module_manager.py`)
   - Enregistre et gère tous les modules
   - Charge les configurations depuis la DB
   - Sauvegarde et valide les configurations

2. **ModuleBase** (`modules/module_manager.py`)
   - Classe de base abstraite pour tous les modules
   - Définit l'interface commune (load_config, validate_config, etc.)

3. **Cog Config** (`cogs/config.py`)
   - Commande `/config` principale
   - Affiche le menu de sélection des modules
   - Vérifie les permissions

4. **Module Events** (`cogs/module_events.py`)
   - Écoute les événements Discord (on_member_join, etc.)
   - Transmet les événements aux modules concernés

---

## Comment ça marche

### 1. Au démarrage du bot

```python
# Dans bot.py - setup_hook()
self.module_manager = ModuleManager(self)
self.module_manager.discover_modules()  # Découvre tous les modules disponibles
```

```python
# Dans bot.py - on_ready()
await self.module_manager.load_all_modules()  # Charge les configs depuis la DB
```

### 2. Quand un utilisateur utilise `/config`

1. Vérification que c'est dans un serveur
2. Vérification des permissions (Moddy = admin, user = manage_guild)
3. Affichage du menu principal avec liste des modules
4. L'utilisateur sélectionne un module
5. Affichage de l'UI de configuration spécifique au module
6. L'utilisateur configure et enregistre
7. Sauvegarde dans la DB via `ModuleManager.save_module_config()`
8. Le module devient actif immédiatement

### 3. Quand un événement se produit

```python
# Dans cogs/module_events.py
@commands.Cog.listener()
async def on_member_join(self, member):
    welcome_module = await self.bot.module_manager.get_module_instance(
        member.guild.id, 'welcome'
    )

    if welcome_module and welcome_module.enabled:
        await welcome_module.on_member_join(member)
```

### 4. Stockage en base de données

Les configurations sont stockées dans PostgreSQL :

```sql
-- Table guilds
{
  "guild_id": 123456789,
  "data": {
    "modules": {
      "welcome": {
        "enabled": true,
        "channel_id": 987654321,
        "message_template": "Bienvenue {user} !",
        "embed_enabled": true
      },
      "ticket": {
        "enabled": false,
        ...
      }
    }
  }
}
```

---

## Créer un nouveau module

### Étape 1 : Créer la classe du module

Créez un fichier dans `/modules/` (exemple : `ticket.py`)

```python
"""
Module Ticket - Système de tickets pour le support
"""

import discord
from typing import Dict, Any, Optional
import logging

from modules.module_manager import ModuleBase

logger = logging.getLogger('moddy.modules.ticket')


class TicketModule(ModuleBase):
    """
    Module de gestion des tickets
    """

    # Métadonnées du module
    MODULE_ID = "ticket"
    MODULE_NAME = "Tickets"
    MODULE_DESCRIPTION = "Système de tickets pour le support utilisateur"
    MODULE_EMOJI = "🎫"

    def __init__(self, bot, guild_id: int):
        super().__init__(bot, guild_id)
        # Vos variables de configuration ici
        self.category_id: Optional[int] = None
        self.support_role_id: Optional[int] = None

    async def load_config(self, config_data: Dict[str, Any]) -> bool:
        """Charge la configuration depuis la DB"""
        try:
            self.config = config_data
            self.enabled = config_data.get('enabled', False)
            self.category_id = config_data.get('category_id')
            self.support_role_id = config_data.get('support_role_id')
            return True
        except Exception as e:
            logger.error(f"Error loading ticket config: {e}")
            return False

    async def validate_config(self, config_data: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Valide la configuration avant enregistrement"""
        # Vérifiez que la catégorie existe
        if 'category_id' in config_data and config_data['category_id']:
            guild = self.bot.get_guild(self.guild_id)
            category = guild.get_channel(config_data['category_id'])

            if not category:
                return False, "Catégorie introuvable"

            if not isinstance(category, discord.CategoryChannel):
                return False, "Ce n'est pas une catégorie"

        return True, None

    def get_default_config(self) -> Dict[str, Any]:
        """Configuration par défaut"""
        return {
            'enabled': False,
            'category_id': None,
            'support_role_id': None
        }

    async def create_ticket(self, user: discord.Member):
        """Logique métier : créer un ticket"""
        if not self.enabled or not self.category_id:
            return

        # Votre logique de création de ticket ici
        pass
```

### Étape 2 : Créer l'UI de configuration

Créez un fichier dans `/modules/configs/` (exemple : `ticket_config.py`)

```python
"""
Configuration UI pour le module Ticket
"""

import discord
from discord import ui
from typing import Optional, Dict, Any

from utils.i18n import t


class TicketConfigView(ui.LayoutView):
    """Interface de configuration du module Ticket"""

    def __init__(self, bot, guild_id: int, user_id: int, locale: str, current_config: Optional[Dict[str, Any]] = None):
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id
        self.locale = locale

        # Configuration actuelle ou par défaut
        if current_config and current_config.get('enabled') is not None:
            self.current_config = current_config.copy()
            self.has_existing_config = True
        else:
            from modules.ticket import TicketModule
            self.current_config = TicketModule(bot, guild_id).get_default_config()
            self.has_existing_config = False

        self.working_config = self.current_config.copy()
        self.has_changes = False

        self._build_view()

    def _build_view(self):
        """Construit l'interface"""
        self.clear_items()

        container = ui.Container()

        # Titre
        container.add_item(ui.TextDisplay(
            f"## {t('modules.ticket.config.title', locale=self.locale)}"
        ))

        # Description
        container.add_item(ui.TextDisplay(
            t('modules.ticket.config.description', locale=self.locale)
        ))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Affichage de la config actuelle
        # ... (comme dans welcome_config.py)

        # Sélecteur de catégorie
        category_row = ui.ActionRow()
        category_select = ui.CategorySelect(
            placeholder=t('modules.ticket.config.category.placeholder', locale=self.locale),
            min_values=0,
            max_values=1
        )
        category_select.callback = self.on_category_select
        category_row.add_item(category_select)
        container.add_item(category_row)

        self.add_item(container)
        self._add_action_buttons()

    def _add_action_buttons(self):
        """Ajoute les boutons Back/Save/Cancel/Delete"""
        # Même logique que welcome_config.py
        pass

    async def on_category_select(self, interaction: discord.Interaction):
        """Callback quand une catégorie est sélectionnée"""
        if not await self.check_user(interaction):
            return

        if interaction.data['values']:
            category_id = int(interaction.data['values'][0])
            self.working_config['category_id'] = category_id
        else:
            self.working_config['category_id'] = None

        self.has_changes = True
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_save(self, interaction: discord.Interaction):
        """Sauvegarde la configuration"""
        await interaction.response.defer()

        module_manager = self.bot.module_manager
        success, error_msg = await module_manager.save_module_config(
            self.guild_id, 'ticket', self.working_config
        )

        if success:
            self.current_config = self.working_config.copy()
            self.has_changes = False
            self.has_existing_config = True
            self._build_view()
            await interaction.followup.send(
                t('modules.config.save.success', locale=self.locale),
                ephemeral=True
            )
            await interaction.edit_original_response(view=self)
        else:
            await interaction.followup.send(
                t('modules.config.save.error', locale=self.locale, error=error_msg),
                ephemeral=True
            )

    # Autres callbacks : on_back, on_cancel, on_delete (identiques à welcome_config.py)

    async def check_user(self, interaction: discord.Interaction) -> bool:
        """Vérifie que c'est le bon utilisateur"""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                t('modules.config.errors.wrong_user', locale=self.locale),
                ephemeral=True
            )
            return False
        return True
```

### Étape 3 : Enregistrer le module dans le cog config

Dans `/cogs/config.py`, ajoutez votre module dans le callback `on_module_select` :

```python
async def on_module_select(self, interaction: discord.Interaction):
    module_id = interaction.data['values'][0]
    module_config = await self.bot.module_manager.get_module_config(self.guild_id, module_id)

    if module_id == 'welcome':
        from modules.configs.welcome_config import WelcomeConfigView
        config_view = WelcomeConfigView(...)
    elif module_id == 'ticket':  # AJOUTER ICI
        from modules.configs.ticket_config import TicketConfigView
        config_view = TicketConfigView(
            self.bot, self.guild_id, self.user_id, self.locale, module_config
        )

    if config_view:
        await interaction.edit_original_response(view=config_view)
```

### Étape 4 : Ajouter les traductions

Dans `/locales/fr.json` et `/locales/en-US.json`, ajoutez :

```json
{
  "modules": {
    "ticket": {
      "config": {
        "title": "Configuration du module Tickets",
        "description": "Configurez le système de tickets de support",
        "category": {
          "label": "Catégorie des tickets",
          "placeholder": "Sélectionnez une catégorie"
        }
      }
    }
  }
}
```

### Étape 5 : Ajouter les événements (optionnel)

Si votre module nécessite des événements Discord, ajoutez-les dans `/cogs/module_events.py`.

---

## Exemple complet : Module Welcome

Le module Welcome est un exemple complet incluant :

### Fichiers

- `/modules/welcome.py` - Logique métier
- `/modules/configs/welcome_config.py` - Interface de configuration
- `/cogs/module_events.py` - Event listener `on_member_join`

### Fonctionnalités

- Envoie un message de bienvenue personnalisable
- Supporte les embeds
- Variables dynamiques : `{user}`, `{username}`, `{server}`, `{member_count}`
- Validation du salon et des permissions
- Configuration sauvegardée en DB

### Flux d'utilisation

1. Admin utilise `/config`
2. Sélectionne "Welcome"
3. Choisit un salon via le ChannelSelect
4. Configure le message (TODO : ajouter modal pour éditer le message)
5. Active l'embed si souhaité
6. Clique sur "Enregistrer"
7. Quand un membre rejoint → message automatique envoyé

---

## Commande /config

### Permissions requises

- **Bot** : Permissions administrateur sur le serveur
- **Utilisateur** : Permission "Gérer le serveur"

### Interface

L'interface utilise les **Composants V2** pour une expérience moderne :

1. **Page principale**
   - Titre avec emoji settings
   - Description de bienvenue
   - Menu déroulant avec tous les modules disponibles
   - Chaque option affiche : emoji + nom + description

2. **Page de configuration d'un module**
   - Titre et description du module
   - Affichage de la configuration actuelle
   - Selects/inputs pour modifier la config
   - Boutons d'action en bas

### États des boutons

| État | Back | Save | Cancel | Delete |
|------|------|------|--------|--------|
| **Aucune modification** | ✅ Actif | ❌ Caché | ❌ Caché | ✅ Actif (si config existe) |
| **Modifications en cours** | ❌ Désactivé | ✅ Actif | ✅ Actif | ❌ Caché |
| **Première configuration** | ✅ Actif | ❌ Caché | ❌ Caché | ❌ Désactivé |

### Workflow

```
/config → Menu principal
  ↓
Sélection module → Page de config du module
  ↓
Modification → Boutons Save/Cancel apparaissent, Back désactivé
  ↓
Save → Validation → Sauvegarde DB → Rechargement instance → Feedback
  ↓
Cancel → Restauration config originale → Rechargement UI
  ↓
Delete → Suppression DB → Config par défaut → Rechargement UI
  ↓
Back → Retour au menu principal
```

---

## Internationalisation (i18n)

### Structure des traductions

Toutes les traductions se trouvent dans `/locales/[langue].json` :

```json
{
  "modules": {
    "config": {
      "main": { ... },
      "status": { ... },
      "buttons": { ... }
    },
    "welcome": {
      "config": { ... }
    },
    "ticket": {
      "config": { ... }
    }
  }
}
```

### Utilisation dans le code

```python
from utils.i18n import t

# Traduction simple
title = t('modules.config.main.title', locale=locale)

# Avec variables
message = t('modules.config.save.error', locale=locale, error=error_msg)

# Avec interaction (détecte la langue automatiquement)
title = t('modules.config.main.title', interaction=interaction)
```

### Langues supportées

- `fr` - Français
- `en-US` - Anglais (US)
- Autres langues : ajouter un fichier JSON correspondant

---

## Stockage en base de données

### Schéma

```sql
CREATE TABLE guilds (
    guild_id BIGINT PRIMARY KEY,
    attributes JSONB DEFAULT '{}'::jsonb,
    data JSONB DEFAULT '{}'::jsonb,  -- ← Les configs modules sont ici
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Format de stockage

```json
{
  "data": {
    "modules": {
      "welcome": {
        "enabled": true,
        "channel_id": 123456789,
        "message_template": "Bienvenue {user} sur {server} !",
        "embed_enabled": true,
        "embed_color": 5865242,
        "embed_title": "Bienvenue !",
        "mention_user": true
      },
      "ticket": {
        "enabled": false,
        "category_id": null
      }
    }
  }
}
```

### Fonctions DB utilisées

```python
# Récupérer la config d'un module
guild_data = await bot.db.get_guild(guild_id)
config = guild_data['data'].get('modules', {}).get('welcome')

# Sauvegarder une config
await bot.db.update_guild_data(
    guild_id,
    "modules.welcome",  # Chemin JSON
    config_data          # Nouvelles données
)
```

### ⚠️ Pièges courants et solutions

#### 1. PostgreSQL JSONB peut retourner dict OU string

**Problème :** `asyncpg` peut retourner les champs JSONB soit comme `dict` soit comme `str` JSON selon la configuration.

**Symptôme :**
```python
# Peut échouer avec: AttributeError: 'str' object has no attribute 'get'
config = guild_data['data'].get('modules')
```

**Solution :** Utiliser `_parse_jsonb()` partout dans `database.py`
```python
def _parse_jsonb(self, value: Any) -> dict:
    """Parse JSONB value that can be either a dict or a JSON string"""
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}

# Utilisation dans get_guild()
return {
    'guild_id': row['guild_id'],
    'attributes': self._parse_jsonb(row['attributes']),
    'data': self._parse_jsonb(row['data']),  # ← Toujours un dict
}
```

#### 2. jsonb_set ne peut pas créer les chemins imbriqués

**Problème :** `jsonb_set` de PostgreSQL échoue silencieusement si les clés parentes n'existent pas.

**Exemple d'échec :**
```sql
-- Si data = '{}', cette requête NE MARCHE PAS
UPDATE guilds
SET data = jsonb_set(data, '{modules,welcome}', '{"enabled": true}', true)
WHERE guild_id = 123;
-- Résultat: data reste '{}'
```

**Solution :** Construire la structure en Python avant de sauvegarder
```python
async def update_guild_data(self, guild_id: int, path: str, value: Any):
    # 1. Lire les données actuelles
    row = await conn.fetchrow("SELECT data FROM guilds WHERE guild_id = $1", guild_id)
    current_data = self._parse_jsonb(row['data']) if row else {}

    # 2. Construire la structure imbriquée en Python
    def set_nested_value(data: dict, parts: list, val: Any) -> dict:
        if len(parts) == 1:
            data[parts[0]] = val
            return data

        # Créer la clé parente si elle n'existe pas
        if parts[0] not in data:
            data[parts[0]] = {}

        # Récursion
        data[parts[0]] = set_nested_value(data[parts[0]], parts[1:], val)
        return data

    path_parts = path.split('.')
    updated_data = set_nested_value(copy.deepcopy(current_data), path_parts, value)

    # 3. Sauvegarder la structure complète
    await conn.execute(
        "UPDATE guilds SET data = $1::jsonb WHERE guild_id = $2",
        json.dumps(updated_data),
        guild_id
    )
```

#### 3. Toujours vérifier que les données sont sauvegardées

**Problème :** Les opérations DB peuvent échouer silencieusement (UPDATE sur ligne inexistante, etc.)

**Solution :** Ajouter une vérification après chaque sauvegarde
```python
# Après l'UPDATE
after = await conn.fetchrow("SELECT data FROM guilds WHERE guild_id = $1", guild_id)
saved_data = self._parse_jsonb(after['data'])

# Vérifier que le chemin existe
current = saved_data
for part in path_parts:
    if isinstance(current, dict) and part in current:
        current = current[part]
    else:
        logger.error(f"[DB] ❌ Verification failed! Path {path} not found")
        raise Exception(f"Data verification failed: path {path} not found")

logger.info(f"[DB] ✅ Verification successful: data saved at path {path}")
```

#### 4. UPSERT pour garantir l'existence de l'entité

**Problème :** Faire un `UPDATE` sur une ligne qui n'existe pas ne fait rien.

**Solution :** Toujours faire un `INSERT ... ON CONFLICT DO NOTHING` avant l'UPDATE
```python
# 1. Garantir que le guild existe
await conn.execute("""
    INSERT INTO guilds (guild_id, data, attributes, created_at, updated_at)
    VALUES ($1, '{}'::jsonb, '{}'::jsonb, NOW(), NOW())
    ON CONFLICT (guild_id) DO NOTHING
""", guild_id)

# 2. Maintenant l'UPDATE marchera toujours
await conn.execute(
    "UPDATE guilds SET data = $1::jsonb WHERE guild_id = $2",
    json.dumps(updated_data),
    guild_id
)
```

#### 5. Utiliser copy.deepcopy() pour les structures imbriquées

**Problème :** `.copy()` ne fait qu'une copie superficielle, modifier les objets imbriqués modifie l'original.

**Solution :**
```python
import copy

# ❌ MAUVAIS - copie superficielle
updated_data = current_data.copy()

# ✅ BON - copie profonde
updated_data = copy.deepcopy(current_data)
```

#### 6. Détecter une config existante avec les bonnes clés

**Problème :** L'UI de configuration doit détecter si une config existe, mais certaines clés sont calculées dynamiquement et ne sont pas sauvegardées.

**Exemple d'échec :**
```python
# Dans WelcomeConfigView.__init__()
# ❌ MAUVAIS - vérifie 'enabled' qui n'est pas sauvegardé
if current_config and current_config.get('enabled') is not None:
    self.has_existing_config = True

# Dans WelcomeModule.load_config()
# 'enabled' est calculé, pas sauvegardé !
self.enabled = self.channel_id is not None
```

**Résultat :** L'UI pense qu'il n'y a pas de config même si elle existe dans la DB.

**Solution :** Vérifier une clé qui est **toujours sauvegardée** dans la config
```python
# ✅ BON - vérifie channel_id qui est toujours dans la config sauvegardée
if current_config and current_config.get('channel_id') is not None:
    self.current_config = current_config.copy()
    self.has_existing_config = True
else:
    # Nouvelle config
    self.current_config = Module(bot, guild_id).get_default_config()
    self.has_existing_config = False
```

**Règle générale :**
- Détecter la config existante avec une clé **obligatoire et persistée**
- Ne pas utiliser de clés calculées dynamiquement (`enabled`, `is_configured`, etc.)
- Utiliser des clés de configuration essentielles (`channel_id`, `category_id`, etc.)

### Checklist pour les modules et la DB

**Base de données :**
- [ ] Utiliser `_parse_jsonb()` pour lire les champs JSONB
- [ ] Construire les structures imbriquées en Python, pas avec `jsonb_set`
- [ ] Faire `INSERT ... ON CONFLICT DO NOTHING` avant les UPDATE
- [ ] Utiliser `copy.deepcopy()` pour copier les structures imbriquées
- [ ] Vérifier que les données sont sauvegardées après chaque UPDATE
- [ ] Logger les états avant/après pour faciliter le debug

**Interface de configuration :**
- [ ] Détecter config existante avec une clé **sauvegardée** (pas `enabled`)
- [ ] Ne calculer `enabled` que dans `load_config()`, ne pas le sauvegarder
- [ ] Utiliser des clés obligatoires pour la détection (`channel_id`, etc.)
- [ ] Tester le rechargement de config après sauvegarde

---

## Bonnes pratiques

### 1. Validation stricte

Toujours valider la configuration dans `validate_config()` :
- Vérifier que les salons/rôles existent
- Vérifier les permissions du bot
- Valider les formats (longueur de texte, etc.)

### 2. Gestion des erreurs

- Logger toutes les erreurs avec le module `logging`
- Retourner des messages d'erreur clairs à l'utilisateur
- Ne jamais faire crasher le bot

### 3. Sécurité

- Toujours vérifier les permissions utilisateur
- Vérifier que `interaction.user.id == view.user_id` dans les callbacks
- Ne pas exposer d'informations sensibles

### 4. Performance

- Mettre en cache les instances de modules (déjà fait par ModuleManager)
- Ne pas faire de requêtes DB inutiles
- Utiliser `defer()` pour les opérations longues

### 5. UX

- Feedback immédiat sur les actions (messages ephemeral)
- Messages d'erreur clairs et en français/anglais
- UI responsive (mise à jour immédiate après modification)

---

## Dépannage

### Le module ne se charge pas au démarrage

1. Vérifier les logs : `logger.info` dans `module_manager.py`
2. Vérifier que la classe hérite bien de `ModuleBase`
3. Vérifier que `discover_modules()` est appelé dans `setup_hook()`

### La configuration ne se sauvegarde pas

1. Vérifier le retour de `validate_config()`
2. Vérifier les logs de `save_module_config()`
3. Vérifier la connexion DB
4. Vérifier le format JSON de la config

### L'UI ne s'affiche pas

1. Vérifier l'import du module de config dans `config.py`
2. Vérifier les traductions i18n
3. Vérifier la console pour les erreurs Python
4. Vérifier que les emojis existent sur le serveur de test

### Le module ne réagit pas aux événements

1. Vérifier que le cog `module_events.py` est chargé
2. Vérifier que `module.enabled == True`
3. Vérifier que l'événement est bien écouté dans `module_events.py`
4. Ajouter des logs dans la méthode du module

---

## Conclusion

Le système de modules de Moddy offre une architecture propre, extensible et facile à maintenir. Suivez ce guide pour créer de nouveaux modules et enrichir les fonctionnalités du bot !

**Points clés à retenir :**
- Séparer logique métier (module) et configuration (UI)
- Valider rigoureusement toutes les configurations
- Utiliser l'i18n pour tous les textes
- Documenter chaque nouveau module
- Tester en local avant de déployer

**Ressources :**
- Documentation Composants V2 : `/docs/COMPONENTS_V2.md`
- Exemple complet : `/modules/welcome.py` + `/modules/configs/welcome_config.py`
- Système i18n : `/utils/i18n.py`
