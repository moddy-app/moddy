"""
Module Manager pour Moddy
Gère le chargement, la configuration et le fonctionnement des modules de serveur
"""

import logging
from typing import Dict, Any, Optional, List, Type
from abc import ABC, abstractmethod
import discord
from pathlib import Path
import importlib
import inspect

logger = logging.getLogger('moddy.modules')


class ModuleBase(ABC):
    """
    Classe de base pour tous les modules de serveur
    Chaque module doit hériter de cette classe
    """

    # Métadonnées du module (à définir dans chaque sous-classe)
    MODULE_ID: str = "base"  # Identifiant unique du module
    MODULE_NAME: str = "Base Module"  # Nom affiché du module
    MODULE_DESCRIPTION: str = "Base module description"  # Description du module
    MODULE_EMOJI: str = "⚙️"  # Emoji représentant le module

    def __init__(self, bot, guild_id: int):
        """
        Initialise le module pour un serveur spécifique

        Args:
            bot: Instance du bot Moddy
            guild_id: ID du serveur Discord
        """
        self.bot = bot
        self.guild_id = guild_id
        self.config: Dict[str, Any] = {}
        self.enabled = False

    @abstractmethod
    async def load_config(self, config_data: Dict[str, Any]) -> bool:
        """
        Charge la configuration du module depuis les données de la DB

        Args:
            config_data: Données de configuration depuis la DB

        Returns:
            True si la configuration est valide, False sinon
        """
        pass

    @abstractmethod
    async def validate_config(self, config_data: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """
        Valide une configuration avant de l'enregistrer

        Args:
            config_data: Données de configuration à valider

        Returns:
            (is_valid, error_message) - True si valide avec None, False avec message d'erreur
        """
        pass

    @abstractmethod
    def get_default_config(self) -> Dict[str, Any]:
        """
        Retourne la configuration par défaut du module

        Returns:
            Dictionnaire de configuration par défaut
        """
        pass

    async def enable(self):
        """Active le module"""
        self.enabled = True
        await self.on_enable()
        logger.info(f"✅ Module {self.MODULE_ID} activé pour le serveur {self.guild_id}")

    async def disable(self):
        """Désactive le module"""
        self.enabled = False
        await self.on_disable()
        logger.info(f"❌ Module {self.MODULE_ID} désactivé pour le serveur {self.guild_id}")

    async def on_enable(self):
        """Hook appelé quand le module est activé"""
        pass

    async def on_disable(self):
        """Hook appelé quand le module est désactivé"""
        pass


class ModuleManager:
    """
    Gestionnaire central de tous les modules de serveur
    Charge, configure et gère les modules
    """

    def __init__(self, bot):
        """
        Initialise le gestionnaire de modules

        Args:
            bot: Instance du bot Moddy
        """
        self.bot = bot
        self.registered_modules: Dict[str, Type[ModuleBase]] = {}  # module_id -> Module class
        self.active_modules: Dict[int, Dict[str, ModuleBase]] = {}  # guild_id -> {module_id -> Module instance}

    def register_module(self, module_class: Type[ModuleBase]):
        """
        Enregistre un nouveau type de module

        Args:
            module_class: Classe du module à enregistrer
        """
        if not issubclass(module_class, ModuleBase):
            raise ValueError(f"{module_class} must inherit from ModuleBase")

        module_id = module_class.MODULE_ID
        if module_id in self.registered_modules:
            logger.warning(f"⚠️ Module {module_id} already registered, overwriting")

        self.registered_modules[module_id] = module_class
        logger.info(f"✅ Module registered: {module_id} ({module_class.MODULE_NAME})")

    def get_available_modules(self) -> List[Dict[str, str]]:
        """
        Retourne la liste de tous les modules disponibles

        Returns:
            Liste de dictionnaires avec les informations des modules
        """
        return [
            {
                'id': module_class.MODULE_ID,
                'name': module_class.MODULE_NAME,
                'description': module_class.MODULE_DESCRIPTION,
                'emoji': module_class.MODULE_EMOJI
            }
            for module_class in self.registered_modules.values()
        ]

    async def get_module_instance(self, guild_id: int, module_id: str) -> Optional[ModuleBase]:
        """
        Récupère l'instance d'un module pour un serveur

        Args:
            guild_id: ID du serveur
            module_id: ID du module

        Returns:
            Instance du module ou None si non trouvé
        """
        if guild_id not in self.active_modules:
            return None

        return self.active_modules[guild_id].get(module_id)

    async def _migrate_welcome_split(self, guild_id: int, guild_data: Dict[str, Any]) -> bool:
        """
        Migre l'ancienne configuration 'welcome' vers 'welcome_channel' et 'welcome_dm'

        Args:
            guild_id: ID du serveur
            guild_data: Données du serveur

        Returns:
            True si une migration a été effectuée, False sinon
        """
        modules_config = guild_data.get('data', {}).get('modules', {})

        # Vérifie si l'ancienne config existe
        if 'welcome' not in modules_config:
            return False

        try:
            old_welcome_config = modules_config['welcome']
            logger.info(f"🔄 Migrating old welcome config for guild {guild_id}")

            # Crée la config pour welcome_channel
            welcome_channel_config = {
                'channel_id': old_welcome_config.get('channel_id'),
                'message_template': old_welcome_config.get('message_template', "Bienvenue {user} sur le serveur !"),
                'mention_user': old_welcome_config.get('mention_user', True),
                'embed_enabled': old_welcome_config.get('embed_enabled', False),
                'embed_title': old_welcome_config.get('embed_title', "Bienvenue !"),
                'embed_description': old_welcome_config.get('embed_description'),
                'embed_color': old_welcome_config.get('embed_color', 0x5865F2),
                'embed_footer': old_welcome_config.get('embed_footer'),
                'embed_image_url': old_welcome_config.get('embed_image_url'),
                'embed_thumbnail_enabled': old_welcome_config.get('embed_thumbnail_enabled', True),
                'embed_author_enabled': old_welcome_config.get('embed_author_enabled', False)
            }

            # Pour le DM, adapte le message (pas de mention dans les DMs)
            dm_message = old_welcome_config.get('message_template', "Bienvenue sur le serveur {server} !")
            if '{user}' in dm_message:
                dm_message = dm_message.replace('{user}', '{username}')

            welcome_dm_config = {
                'message_template': dm_message,
                'embed_enabled': old_welcome_config.get('embed_enabled', False),
                'embed_title': old_welcome_config.get('embed_title', "Bienvenue !"),
                'embed_description': old_welcome_config.get('embed_description'),
                'embed_color': old_welcome_config.get('embed_color', 0x5865F2),
                'embed_footer': old_welcome_config.get('embed_footer'),
                'embed_image_url': old_welcome_config.get('embed_image_url'),
                'embed_thumbnail_enabled': old_welcome_config.get('embed_thumbnail_enabled', True),
                'embed_author_enabled': old_welcome_config.get('embed_author_enabled', False)
            }

            # Met à jour les modules
            modules_config['welcome_channel'] = welcome_channel_config
            modules_config['welcome_dm'] = welcome_dm_config
            del modules_config['welcome']

            # Sauvegarde dans la DB
            await self.bot.db.update_guild_data(guild_id, 'modules', modules_config)
            logger.info(f"✅ Welcome config migrated successfully for guild {guild_id}")

            return True

        except Exception as e:
            logger.error(f"❌ Error migrating welcome config for guild {guild_id}: {e}", exc_info=True)
            return False

    async def load_guild_modules(self, guild_id: int):
        """
        Charge tous les modules configurés pour un serveur depuis la DB

        Args:
            guild_id: ID du serveur
        """
        if not self.bot.db:
            logger.warning("⚠️ No database connection, cannot load modules")
            return

        try:
            # Récupère les données du serveur
            guild_data = await self.bot.db.get_guild(guild_id)

            # Migre l'ancienne config welcome si nécessaire
            migrated = await self._migrate_welcome_split(guild_id, guild_data)
            if migrated:
                # Recharge les données après migration
                guild_data = await self.bot.db.get_guild(guild_id)

            modules_config = guild_data.get('data', {}).get('modules', {})

            # Initialise le dictionnaire pour ce serveur
            if guild_id not in self.active_modules:
                self.active_modules[guild_id] = {}

            # Charge chaque module configuré
            for module_id, config_data in modules_config.items():
                if module_id not in self.registered_modules:
                    logger.warning(f"⚠️ Module {module_id} configured but not registered")
                    continue

                # Crée une instance du module
                module_class = self.registered_modules[module_id]
                module_instance = module_class(self.bot, guild_id)

                # Charge la configuration
                if await module_instance.load_config(config_data):
                    self.active_modules[guild_id][module_id] = module_instance
                    # Active le module si la config est valide (enabled est déterminé dans load_config)
                    if module_instance.enabled:
                        await module_instance.enable()
                    logger.info(f"✅ Module {module_id} loaded for guild {guild_id} (enabled: {module_instance.enabled})")
                else:
                    logger.error(f"❌ Failed to load module {module_id} for guild {guild_id}")

            logger.info(f"📦 Loaded {len(self.active_modules[guild_id])} modules for guild {guild_id}")

        except Exception as e:
            logger.error(f"❌ Error loading modules for guild {guild_id}: {e}", exc_info=True)

    async def save_module_config(self, guild_id: int, module_id: str, config_data: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """
        Sauvegarde la configuration d'un module dans la DB

        Args:
            guild_id: ID du serveur
            module_id: ID du module
            config_data: Configuration à sauvegarder

        Returns:
            (success, error_message)
        """
        if not self.bot.db:
            return False, "No database connection"

        # Vérifie que le module existe
        if module_id not in self.registered_modules:
            return False, f"Module {module_id} not found"

        try:
            # Valide la configuration
            module_class = self.registered_modules[module_id]
            temp_instance = module_class(self.bot, guild_id)
            is_valid, error_msg = await temp_instance.validate_config(config_data)

            if not is_valid:
                return False, error_msg

            # S'assure que le serveur existe dans la DB
            await self.bot.db.get_guild(guild_id)

            # Sauvegarde dans la DB
            await self.bot.db.update_guild_data(
                guild_id,
                f"modules.{module_id}",
                config_data
            )

            logger.info(f"📝 Config saved to DB for module {module_id} in guild {guild_id}: {config_data}")

            # Met à jour ou crée l'instance active
            if guild_id not in self.active_modules:
                self.active_modules[guild_id] = {}

            # Crée ou met à jour l'instance
            if module_id in self.active_modules[guild_id]:
                # Met à jour l'instance existante
                module_instance = self.active_modules[guild_id][module_id]
                await module_instance.load_config(config_data)
            else:
                # Crée une nouvelle instance
                module_instance = module_class(self.bot, guild_id)
                await module_instance.load_config(config_data)
                self.active_modules[guild_id][module_id] = module_instance

            # Active/désactive selon la validité de la config (enabled est déterminé dans load_config)
            if module_instance.enabled:
                await module_instance.enable()
            else:
                await module_instance.disable()

            logger.info(f"✅ Configuration saved for module {module_id} in guild {guild_id} (enabled: {module_instance.enabled})")
            return True, None

        except Exception as e:
            logger.error(f"❌ Error saving module config: {e}", exc_info=True)
            return False, f"Internal error: {str(e)}"

    async def delete_module_config(self, guild_id: int, module_id: str) -> bool:
        """
        Supprime la configuration d'un module

        Args:
            guild_id: ID du serveur
            module_id: ID du module

        Returns:
            True si succès, False sinon
        """
        if not self.bot.db:
            return False

        try:
            # Désactive le module s'il est actif
            if guild_id in self.active_modules and module_id in self.active_modules[guild_id]:
                module_instance = self.active_modules[guild_id][module_id]
                await module_instance.disable()
                del self.active_modules[guild_id][module_id]

            # Supprime de la DB en mettant un objet vide
            await self.bot.db.update_guild_data(
                guild_id,
                f"modules.{module_id}",
                {}
            )

            logger.info(f"🗑️ Configuration deleted for module {module_id} in guild {guild_id}")
            return True

        except Exception as e:
            logger.error(f"❌ Error deleting module config: {e}", exc_info=True)
            return False

    async def get_module_config(self, guild_id: int, module_id: str) -> Optional[Dict[str, Any]]:
        """
        Récupère la configuration d'un module depuis la DB

        Args:
            guild_id: ID du serveur
            module_id: ID du module

        Returns:
            Configuration du module ou None
        """
        if not self.bot.db:
            return None

        try:
            guild_data = await self.bot.db.get_guild(guild_id)
            modules_config = guild_data.get('data', {}).get('modules', {})
            return modules_config.get(module_id)
        except Exception as e:
            logger.error(f"❌ Error getting module config: {e}", exc_info=True)
            return None

    async def load_all_modules(self):
        """
        Charge tous les modules pour tous les serveurs
        Appelé au démarrage du bot
        """
        if not self.bot.db:
            logger.warning("⚠️ No database connection, cannot load modules")
            return

        logger.info("📦 Loading modules for all guilds...")

        # Récupère tous les serveurs
        for guild in self.bot.guilds:
            await self.load_guild_modules(guild.id)

        logger.info("✅ All guild modules loaded")

    def discover_modules(self):
        """
        Découvre et enregistre automatiquement tous les modules disponibles
        """
        modules_dir = Path(__file__).parent

        logger.info("🔍 Discovering modules...")

        # Parcourt tous les fichiers Python dans le dossier modules
        for file in modules_dir.glob("*.py"):
            # Ignore les fichiers spéciaux
            if file.name.startswith("_") or file.name == "module_manager.py":
                continue

            try:
                # Import le module
                module_name = f"modules.{file.stem}"
                module = importlib.import_module(module_name)

                # Cherche les classes qui héritent de ModuleBase
                for name, obj in inspect.getmembers(module, inspect.isclass):
                    if issubclass(obj, ModuleBase) and obj != ModuleBase:
                        self.register_module(obj)

            except Exception as e:
                logger.error(f"❌ Error loading module {file.stem}: {e}", exc_info=True)

        logger.info(f"✅ Discovered {len(self.registered_modules)} modules")
