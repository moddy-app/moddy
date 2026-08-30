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

# Modules whose MODULE_ID changed: the stored config key is migrated in place the
# first time a guild is loaded, so a rename never orphans an existing setup.
# old module id -> new module id
LEGACY_MODULE_IDS: Dict[str, str] = {
    # The AI automod became "automod_ai" when a classic (rule-based) automod
    # module was planned under the "automod" name.
    'automod': 'automod_ai',
}

# What a configuration change pushed from outside the bot did to the config.
# The dashboard writes straight to the DB, so the bot only learns about it from
# a Redis notification and has to re-derive everything else itself.
EXTERNAL_UPDATED = "updated"
EXTERNAL_DELETED = "deleted"


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
    # Position dans le menu déroulant /config, du plus important (0) au moins
    # important. Fixe et explicite — ne dépend ni de l'ordre de découverte
    # des fichiers (non déterministe) ni du nom affiché (qui varie par
    # module mais ne reflète pas son importance).
    MODULE_ORDER: int = 100
    # True when the module keeps *messages* written in the server language
    # (a verification panel, a ticket panel). Changing the server language in
    # /config → Server settings has to re-post them, exactly like a dashboard
    # push does — see ModuleManager.apply_language_change() and
    # utils/guild_language.py.
    LANGUAGE_DEPENDENT_MESSAGES: bool = False

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

    async def on_external_config_change(self, action: str) -> Dict[str, Any]:
        """Apply the Discord-side consequences of a config change made outside the bot.

        The dashboard and the backend write module configurations straight into
        ``guilds.data.modules.<id>`` and then notify the bot over Redis. Reloading
        the config is not enough for a module whose configuration is *visible* in
        Discord: a panel message to re-post, channel overwrites to re-apply, an
        external service to notify. ``/config`` does that work in its save handler;
        without this hook the same change coming from the dashboard would update
        the database and leave Discord showing the previous setup.

        Called by :meth:`ModuleManager.reload_module` after the new configuration
        has been loaded (``action`` is :data:`EXTERNAL_UPDATED`), or on the outgoing
        instance just before it is dropped (:data:`EXTERNAL_DELETED`) — in that case
        the stored config is already gone, so the hook must only clean up Discord
        and never write anything back.

        Returns a small JSON-serialisable recap relayed to the dashboard.
        """
        return {}


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
        # Sort by MODULE_ORDER (most important first) so the /config module
        # picker has a fixed position per module — not the display name
        # (which doesn't reflect importance) nor the filesystem discovery
        # order (which is not deterministic across restarts).
        return [
            {
                'id': module_class.MODULE_ID,
                'name': module_class.MODULE_NAME,
                'description': module_class.MODULE_DESCRIPTION,
                'emoji': module_class.MODULE_EMOJI
            }
            for module_class in sorted(
                self.registered_modules.values(),
                key=lambda m: (m.MODULE_ORDER, m.MODULE_NAME.lower()),
            )
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
            # Cache vidé (ex. event Pub/Sub "module_updated" du dashboard) :
            # on relit la config depuis la DB au lieu de rester aveugle.
            await self.load_guild_modules(guild_id)

        return self.active_modules.get(guild_id, {}).get(module_id)

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

            # Migre les configurations stockées sous un ancien module id
            modules_config = await self._migrate_legacy_ids(guild_id, modules_config)

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

    async def _migrate_legacy_ids(self, guild_id: int,
                                  modules_config: Dict[str, Any]) -> Dict[str, Any]:
        """Rewrite configs stored under a renamed module id (in DB and in memory).

        A renamed module would otherwise silently lose its stored configuration
        (the old key is no longer a registered module, so it is skipped). The
        migration runs once per guild: the old key is copied to the new id and
        then emptied. An existing config under the new id always wins.
        """
        migrated = dict(modules_config)
        for old_id, new_id in LEGACY_MODULE_IDS.items():
            legacy = migrated.get(old_id)
            if not legacy or migrated.get(new_id):
                continue
            try:
                await self.bot.db.update_guild_data(guild_id, f"modules.{new_id}", legacy)
                await self.bot.db.update_guild_data(guild_id, f"modules.{old_id}", {})
            except Exception as e:
                logger.error(f"[FAIL] Could not migrate module {old_id} -> {new_id} "
                             f"for guild {guild_id}: {e}")
                continue
            migrated[new_id] = legacy
            migrated.pop(old_id, None)
            logger.info(f"Migrated module config {old_id} -> {new_id} for guild {guild_id}")
        return migrated

    async def reload_module(self, guild, module_id: str, *,
                           action: str = EXTERNAL_UPDATED) -> Dict[str, Any]:
        """Re-read one module's config from the DB and apply the change in Discord.

        This is the entry point for a configuration pushed by the backend or the
        dashboard (Redis ``module_updated`` event, or the ``update_panel`` task on
        the ``moddy:tasks`` stream). Dropping the guild cache is not enough on its
        own: the cache only makes the bot *read* the new values, while the visible
        half of a configuration — a verification panel, channel overwrites — has to
        be re-applied, which is what the module's ``on_external_config_change``
        hook does.

        Args:
            guild: guild id, or a ``discord.Guild``
            module_id: module to reload
            action: :data:`EXTERNAL_UPDATED` or :data:`EXTERNAL_DELETED`

        Returns:
            ``{"ok": bool, ...}`` — the recap relayed back to the dashboard.
        """
        guild_id = getattr(guild, 'id', guild)
        try:
            guild_id = int(guild_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": "invalid_guild"}

        if module_id not in self.registered_modules:
            return {"ok": False, "error": "unknown_module"}
        if not self.bot.db:
            return {"ok": False, "error": "no_database"}

        try:
            config = await self.get_module_config(guild_id, module_id) or {}
        except Exception as e:
            logger.error(f"[FAIL] Could not read {module_id} config for guild {guild_id}: {e}")
            return {"ok": False, "error": "config_unreadable"}

        # ``delete_module_config`` stores an empty object rather than removing the
        # key, so an empty config *is* a deletion however the event labelled it.
        if not config:
            action = EXTERNAL_DELETED

        # The outgoing instance still holds what the new config no longer carries
        # — the id of the panel message to take down. Rebuild one from the stored
        # config when the cache is cold (a restart between the two events), so a
        # deletion right after a restart still cleans up Discord.
        module = self.active_modules.get(guild_id, {}).get(module_id)
        if module is None and (config or action == EXTERNAL_UPDATED):
            module = self.registered_modules[module_id](self.bot, guild_id)
            if config:
                await module.load_config(config)

        if action == EXTERNAL_DELETED:
            if module is None:
                logger.warning(
                    f"[Modules] {module_id} deleted for guild {guild_id} but no instance "
                    f"was cached: anything it left in Discord stays there"
                )
                return {"ok": True, "action": action, "cleaned": False}
            recap = await self._run_external_hook(module, action, guild_id, module_id)
            try:
                await module.disable()
            except Exception as e:
                logger.error(f"[FAIL] Error disabling {module_id} for guild {guild_id}: {e}")
            self.active_modules.get(guild_id, {}).pop(module_id, None)
            # AFTER the instance is dropped: the command sync asks the cache
            # whether the module is still enabled, and the outgoing instance
            # would still answer yes.
            await self._sync_module_commands(guild_id, module_id)
            logger.info(f"Module {module_id} unloaded for guild {guild_id} (external delete)")
            return {"ok": True, "action": action, "cleaned": True, **recap}

        if not await module.load_config(config):
            logger.error(f"[FAIL] Could not load pushed {module_id} config for guild {guild_id}")
            return {"ok": False, "error": "invalid_config"}

        self.active_modules.setdefault(guild_id, {})[module_id] = module
        if module.enabled:
            await module.enable()
        else:
            await module.disable()

        recap = await self._run_external_hook(module, action, guild_id, module_id)
        await self._sync_module_commands(guild_id, module_id)
        logger.info(
            f"Module {module_id} reloaded from a pushed config for guild {guild_id} "
            f"(enabled: {module.enabled})"
        )
        return {"ok": True, "action": action, "enabled": module.enabled, **recap}

    async def apply_language_change(self, guild_id: int) -> Dict[str, Any]:
        """Re-apply what the server language is *baked into* after it changed.

        Most of the bot reads the language when it writes a message, so a new
        language simply takes effect. A panel is different: it is a message
        already sitting in a channel, written in the previous language, and
        only a re-post brings it in line. Those modules declare themselves
        with ``LANGUAGE_DEPENDENT_MESSAGES`` and are reloaded here through the
        same path a dashboard push uses.

        Returns ``{module_id: recap}`` for the modules that were refreshed.
        """
        recaps: Dict[str, Any] = {}
        for module_id, module_class in self.registered_modules.items():
            if not getattr(module_class, 'LANGUAGE_DEPENDENT_MESSAGES', False):
                continue
            try:
                config = await self.get_module_config(guild_id, module_id)
            except Exception as e:
                logger.error(f"[FAIL] Could not read {module_id} config for guild "
                             f"{guild_id} on a language change: {e}")
                continue
            if not config:
                continue  # nothing posted in Discord, nothing to re-post
            recaps[module_id] = await self.reload_module(
                guild_id, module_id, action=EXTERNAL_UPDATED)
        return recaps

    async def _sync_module_commands(self, guild_id: int, module_id: str) -> None:
        """Publish/unpublish a module's slash commands after a config change.

        A module can own guild commands that must only exist where it is
        enabled (``/ticket``). Enabling or disabling it therefore changes the
        guild's command tree, and nothing else in the save path would notice.
        The bot skips the sync itself when the enabled set did not change, so
        this is safe to call after every save.
        """
        if module_id not in getattr(self.bot, 'module_slash_commands', {}):
            return
        try:
            await self.bot.resync_module_commands(guild_id)
        except Exception as e:
            logger.error(f"[FAIL] Could not re-sync {module_id} commands for guild "
                         f"{guild_id}: {e}")

    async def _run_external_hook(self, module: ModuleBase, action: str,
                                 guild_id: int, module_id: str) -> Dict[str, Any]:
        """Run ``on_external_config_change`` without letting it break the reload.

        The config is already stored and loaded by the time the hook runs, so a
        failure to re-post a panel must be reported, not turned into a failed
        reload that would leave the bot reading stale values.
        """
        try:
            return await module.on_external_config_change(action) or {}
        except Exception as e:
            logger.error(
                f"[FAIL] {module_id}.on_external_config_change failed for guild "
                f"{guild_id}: {e}", exc_info=True,
            )
            return {"hook_error": str(e)}

    async def unload_guild_modules(self, guild_id: int):
        """Remove guild module cache so next access reloads from DB."""
        if guild_id in self.active_modules:
            for module in self.active_modules[guild_id].values():
                try:
                    await module.disable()
                except Exception:
                    pass
            del self.active_modules[guild_id]

    async def _blocked_as_new_module(self, guild_id: int, module_id: str,
                                     actor_id: Optional[int] = None) -> Optional[str]:
        """Return an error message when a *new* module may not be configured.

        A global ``limited`` sanction (Moddy-team) leaves the modules a server
        already configured running, but forbids setting up any module that was
        never configured before. A ``suspended`` subject is blocked outright by
        the interaction gate, so it never reaches this point — but the check
        covers it anyway for the dashboard-driven paths.
        """
        from utils import global_sanctions
        from utils.i18n import t

        try:
            level = await global_sanctions.get_context_level(
                self.bot, user_id=actor_id, guild_id=guild_id)
        except Exception as e:
            logger.error(f"Global sanction check failed for guild {guild_id}: {e}")
            return None  # fail open — a lookup error must not break /config

        if not global_sanctions.at_least(level, global_sanctions.GlobalLevel.LIMITED):
            return None

        # Already configured? Then it is not a new module — let it through.
        # A deleted config is stored as ``{}``, which counts as "never set up".
        existing = await self.get_module_config(guild_id, module_id)
        if existing:
            return None

        from utils.guild_language import guild_locale
        locale = await guild_locale(self.bot, guild_id)
        return t('global_sanctions.limited.no_new_modules', locale=locale)

    async def save_module_config(self, guild_id: int, module_id: str,
                                 config_data: Dict[str, Any],
                                 actor_id: Optional[int] = None) -> tuple[bool, Optional[str]]:
        """
        Sauvegarde la configuration d'un module dans la DB

        Args:
            guild_id: ID du serveur
            module_id: ID du module
            config_data: Configuration à sauvegarder
            actor_id: ID de l'utilisateur à l'origine du changement (optionnel) —
                une limitation globale le concernant bloque les nouveaux modules
                comme si le serveur était limité

        Returns:
            (success, error_message)
        """
        if not self.bot.db:
            return False, "No database connection"

        # Vérifie que le module existe
        if module_id not in self.registered_modules:
            return False, f"Module {module_id} not found"

        # Sanction globale "limité" : le serveur (ou l'auteur) garde ses modules
        # déjà configurés mais ne peut plus en configurer de NOUVEAUX.
        blocked = await self._blocked_as_new_module(guild_id, module_id, actor_id)
        if blocked:
            return False, blocked

        # A genuinely unexpected failure (DB down, a broken module class) is not
        # a validation error: it propagates so the central handler shows the user
        # an error card with a code instead of a bare "Internal error" string.

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
            # Langue du serveur (/config → Paramètres du serveur)
            from utils.guild_language import guild_locale
            locale = await guild_locale(self.bot, guild_id)

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

        # A module that owns slash commands has just appeared in (or
        # vanished from) this guild's command tree.
        await self._sync_module_commands(guild_id, module_id)
        return True, None

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

        # An unexpected failure here is not an expected outcome: it must reach
        # the central error handler (BaseView.on_error) so the user gets an
        # error card with a code, not a bare "deletion failed" message.

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

        # Its commands must disappear from the guild along with it.
        await self._sync_module_commands(guild_id, module_id)
        return True

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
            config = modules_config.get(module_id)
            if config:
                return config
            # Fall back to a not-yet-migrated legacy key (see LEGACY_MODULE_IDS).
            for old_id, new_id in LEGACY_MODULE_IDS.items():
                if new_id == module_id and modules_config.get(old_id):
                    return modules_config[old_id]
            return config
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
        # (trié pour un ordre de découverte déterministe)
        for file in sorted(modules_dir.glob("*.py")):
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
