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

    def get_required_fields(self) -> List[str]:
        """
        Retourne la liste des champs obligatoires du module
        Par défaut, aucun champ n'est obligatoire
        Les sous-classes peuvent override cette méthode

        Returns:
            Liste des clés de configuration obligatoires
        """
        return []

    def get_field_label(self, field_name: str, locale: str = 'en-US') -> str:
        """
        Retourne le label traduit d'un champ pour les messages d'erreur
        Les sous-classes peuvent override cette méthode pour personnaliser les labels

        Args:
            field_name: Nom du champ
            locale: Langue pour la traduction

        Returns:
            Label du champ traduit
        """
        from utils.i18n import t
        # Par défaut, utilise les traductions du module si disponibles
        try:
            return t(f'modules.{self.MODULE_ID}.config.{field_name}.section_title', locale=locale)
        except:
            # Fallback sur le nom brut du champ
            return field_name.replace('_', ' ').title()

    async def enable(self):
        """Active le module"""
        self.enabled = True
        await self.on_enable()
        logger.info(f"Module {self.MODULE_ID} enabled for guild {self.guild_id}")

    async def disable(self):
        """Désactive le module"""
        self.enabled = False
        await self.on_disable()
        logger.info(f"Module {self.MODULE_ID} disabled for guild {self.guild_id}")

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
            logger.warning(f"[WARN] Module {module_id} already registered, overwriting")

        self.registered_modules[module_id] = module_class
        logger.info(f"Module registered: {module_id} ({module_class.MODULE_NAME})")

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

    async def load_guild_modules(self, guild_id: int):
        """
        Charge tous les modules configurés pour un serveur depuis la DB

        Args:
            guild_id: ID du serveur
        """
        if not self.bot.db:
            logger.warning("[WARN] No database connection, cannot load modules")
            return

        try:
            # Récupère les données du serveur
            guild_data = await self.bot.db.get_guild(guild_id)
            modules_config = guild_data.get('data', {}).get('modules', {})

            # Initialise le dictionnaire pour ce serveur
            if guild_id not in self.active_modules:
                self.active_modules[guild_id] = {}

            # Charge chaque module configuré
            for module_id, config_data in modules_config.items():
                if module_id not in self.registered_modules:
                    # Ignore silently old/obsolete module configurations
                    # This can happen when modules are renamed or removed
                    logger.debug(f"Skipping module {module_id} (configured but not registered - likely obsolete)")
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
                    logger.info(f"Module loaded: {module_id} (guild: {guild_id}, enabled: {module_instance.enabled})")
                else:
                    logger.error(f"[FAIL] Failed to load module {module_id} for guild {guild_id}")

            logger.info(f"Loaded {len(self.active_modules[guild_id])} modules for guild {guild_id}")

        except Exception as e:
            logger.error(f"[FAIL] Error loading modules for guild {guild_id}: {e}", exc_info=True)

    async def unload_guild_modules(self, guild_id: int):
        """Remove guild module cache so next access reloads from DB."""
        if guild_id in self.active_modules:
            for module in self.active_modules[guild_id].values():
                try:
                    await module.disable()
                except Exception:
                    pass
            del self.active_modules[guild_id]

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
            # Crée une instance temporaire du module
            module_class = self.registered_modules[module_id]
            temp_instance = module_class(self.bot, guild_id)

            # Vérifie que tous les champs obligatoires sont remplis
            required_fields = temp_instance.get_required_fields()
            missing_fields = []

            for field in required_fields:
                # Vérifie si le champ est présent et non vide
                if field not in config_data or config_data[field] is None:
                    missing_fields.append(field)

            if missing_fields:
                # Récupère la locale du serveur (par défaut en-US)
                try:
                    guild = self.bot.get_guild(guild_id)
                    locale = str(guild.preferred_locale) if guild and guild.preferred_locale else 'en-US'
                except:
                    locale = 'en-US'

                # Construit le message d'erreur avec les labels traduits
                from utils.i18n import t
                field_labels = [temp_instance.get_field_label(field, locale) for field in missing_fields]
                fields_str = "\n• ".join(field_labels)

                error_msg = t('modules.config.errors.required_fields', locale=locale, fields=fields_str)
                return False, error_msg

            # Valide la configuration (permissions, existence des ressources, etc.)
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

            logger.info(f"Config saved to DB for module {module_id} in guild {guild_id}: {config_data}")

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

            logger.info(f"Configuration saved for module {module_id} in guild {guild_id} (enabled: {module_instance.enabled})")
            return True, None

        except Exception as e:
            logger.error(f"[FAIL] Error saving module config: {e}", exc_info=True)
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

            logger.info(f"Configuration deleted for module {module_id} in guild {guild_id}")
            return True

        except Exception as e:
            logger.error(f"[FAIL] Error deleting module config: {e}", exc_info=True)
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
            logger.error(f"[FAIL] Error getting module config: {e}", exc_info=True)
            return None

    async def load_all_modules(self):
        """
        Charge tous les modules pour tous les serveurs
        Appelé au démarrage du bot
        """
        if not self.bot.db:
            logger.warning("[WARN] No database connection, cannot load modules")
            return

        logger.info("Loading modules for all guilds...")

        # Récupère tous les serveurs
        for guild in self.bot.guilds:
            await self.load_guild_modules(guild.id)

        logger.info("All guild modules loaded")

    def discover_modules(self):
        """
        Découvre et enregistre automatiquement tous les modules disponibles
        """
        modules_dir = Path(__file__).parent

        logger.info("Discovering modules...")

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
                logger.error(f"[FAIL] Error loading module {file.stem}: {e}", exc_info=True)

        logger.info(f"Discovered {len(self.registered_modules)} modules")
