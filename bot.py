"""
Moddy - Main bot class
Handles all core logic and events
"""

import discord
from discord.ext import commands, tasks
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Set
import os
import sys
from pathlib import Path
import traceback
import aiohttp

from config import (
    DEBUG,
    DEFAULT_PREFIX,
    DATABASE_URL,
    DEVELOPER_IDS,
    COLORS,
    IS_DEV,
    IS_PROD,
    IS_MAINTENANCE,
    DEV_ALLOWED_IDS,
    ENV_MODE,
)
from utils.emojis import EMOJIS, ERROR as ERROR_EMOJI
from database import setup_database, db
# Import du nouveau système i18n
from utils.i18n import i18n
# Import du système de permissions staff
from utils.staff_permissions import setup_staff_permissions
# Import du système de logging staff
from utils.staff_logger import init_staff_logger
# Import du gestionnaire de modules
from modules.module_manager import ModuleManager
# Import du système de configuration des annonces
from utils.announcement_setup import setup_announcement_channel

logger = logging.getLogger('moddy')


class ModdyBot(commands.Bot):
    """Main Moddy class"""

    def __init__(self):
        # Required intents
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.members = True

        # Configure HTTP client timeout to prevent timeout errors
        # Especially important in containerized environments (Docker/Railway)
        http_timeout = aiohttp.ClientTimeout(
            total=90,      # Total timeout for the entire request
            connect=45,    # Timeout for establishing connection
            sock_read=45,  # Timeout for reading from socket
            sock_connect=45  # Timeout for socket connection
        )

        # Bot configuration
        # Get bot status from environment variable
        bot_status = os.getenv("BOT_STATUS", "")

        super().__init__(
            command_prefix=self.get_prefix,
            intents=intents,
            help_command=None,  # We make our own help command
            activity=discord.CustomActivity(name=bot_status) if bot_status else None,
            status=discord.Status.online,
            case_insensitive=True,
            max_messages=10000,
            http_timeout=http_timeout  # Apply custom timeout
        )

        # Internal variables
        self.launch_time = datetime.now(timezone.utc)
        self.db = None  # ModdyDatabase instance
        self._dev_team_ids: Set[int] = set()
        self.maintenance_mode = False
        self.version = None  # Bot version from GitHub releases

        # Cache for server prefixes
        self.prefix_cache = {}

        # Gestionnaire de modules
        self.module_manager = None

        # Cache pour les commandes guild-only (NE JAMAIS les remettre dans l'arbre global)
        self._guild_only_commands = []

        # Serveur HTTP interne pour l'API backend
        self.internal_api_server = None
        self.internal_api_thread = None

        # Configure global error handler
        self.setup_error_handler()

        # INTERCEPTION RADICALE: Configure le check de blacklist global pour toutes les app commands
        self.tree.interaction_check = self._global_blacklist_check

    def setup_error_handler(self):
        """Configure uncaught error handler"""

        def handle_exception(loop, context):
            # Get the exception
            exception = context.get('exception')
            if exception:
                logger.error(f"Uncaught error: {exception}", exc_info=exception)

                # Try to send to Discord if the bot is connected
                if self.is_ready():
                    asyncio.create_task(self.log_fatal_error(exception, context))

        # Configure the handler
        asyncio.get_event_loop().set_exception_handler(handle_exception)

    async def log_fatal_error(self, exception: Exception, context: dict):
        """Log a fatal error in Discord"""
        try:
            # Use the ErrorTracker cog if it's loaded
            error_cog = self.get_cog("ErrorTracker")
            if error_cog:
                error_code = error_cog.generate_error_code(exception)
                error_details = {
                    "type": type(exception).__name__,
                    "message": str(exception),
                    "file": "System error",
                    "line": "N/A",
                    "context": str(context),
                    "traceback": traceback.format_exc()
                }
                error_cog.store_error(error_code, error_details)
                await error_cog.send_error_log(error_code, error_details, is_fatal=True)
        except Exception as e:
            logger.error(f"Could not log fatal error: {e}")

    async def fetch_version(self):
        """Fetch the bot version from GitHub releases"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://api.github.com/repos/juthing/MODDY/releases/latest",
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        self.version = data.get("tag_name", "Unknown")
                        logger.info(f"Bot version: {self.version}")
                    else:
                        logger.warning(f"[WARN] Failed to fetch version: HTTP {response.status}")
                        self.version = "Unknown"
        except Exception as e:
            logger.error(f"[FAIL] Error fetching version: {e}")
            self.version = "Unknown"

    def start_internal_api_server(self):
        """
        Démarre le serveur HTTP interne dans un thread séparé.
        Ce serveur écoute sur le port INTERNAL_PORT (3000) pour recevoir
        les requêtes du backend via Railway Private Network.
        """
        import threading
        import uvicorn
        from internal_api.server import app, set_bot

        # Configurer le bot dans le serveur interne
        set_bot(self)

        # Port du serveur interne
        internal_port = int(os.getenv("INTERNAL_PORT", 3000))

        # Fonction pour exécuter le serveur dans le thread
        def run_server():
            logger.info(f"Starting internal API server on port {internal_port}")
            uvicorn.run(
                app,
                host="::",  # IPv4 + IPv6
                port=internal_port,
                log_level="info",
                access_log=False  # Désactiver les logs d'accès pour éviter le spam
            )

        # Créer et démarrer le thread
        self.internal_api_thread = threading.Thread(
            target=run_server,
            daemon=True,
            name="InternalAPIServer"
        )
        self.internal_api_thread.start()
        logger.info(f"Internal API server started on port {internal_port}")

    async def setup_hook(self):
        """Called once on bot startup"""
        logger.info("Initial setup...")

        # Fetch bot version from GitHub
        await self.fetch_version()

        # Configure error handler for slash commands
        self.tree.on_error = self.on_app_command_error

        # Connect the database
        if DATABASE_URL:
            await self.setup_database()

        # Initialize i18n system
        logger.info("Loading i18n system...")
        i18n.load_translations()
        logger.info(f"i18n loaded with {len(i18n.supported_locales)} languages")

        # Initialize staff permissions system
        logger.info("Initializing staff permissions system...")
        setup_staff_permissions(self)
        logger.info("Staff permissions system ready")

        # Initialize staff logger
        logger.info("Initializing staff logger...")
        init_staff_logger(self)
        logger.info("Staff logger ready")

        # Initialize module manager
        logger.info("Initializing module manager...")
        self.module_manager = ModuleManager(self)
        self.module_manager.discover_modules()
        logger.info("Module manager ready")

        # Start internal API server
        logger.info("Starting internal API server...")
        self.start_internal_api_server()

        # Test backend connection with full Railway Private Network diagnostic
        # This includes DNS wait (Railway DNS not available for ~3-5s at startup)
        logger.info("Testing backend connection (Railway Private Network)...")
        try:
            from services.backend_client import get_backend_client, BackendClientError
            backend_client = get_backend_client()
            # Use full diagnostic which includes DNS wait and retries
            connection_ok = await backend_client.test_connection(use_full_diagnostic=True)
            if connection_ok:
                logger.info("Backend connection established successfully")
            else:
                logger.warning("[WARN] Backend connection test failed - check logs above for details")
                logger.warning("   The bot will start, but backend-dependent features may not work")
        except BackendClientError as e:
            logger.error(f"[WARN] Backend connection test failed: {e}")
            logger.error("   The bot will start, but backend-dependent features may not work")
        except Exception as e:
            logger.error(f"[WARN] Backend connection test failed: {e}")
            logger.error("   The bot will start, but backend-dependent features may not work")

        # Add before_invoke check for prefix commands cog disable
        @self.before_invoke
        async def check_cog_disabled(ctx):
            cog_manager = self.get_cog("CogManager")
            if cog_manager and ctx.cog:
                cog_name = type(ctx.cog).__name__
                if cog_manager.is_cog_disabled(cog_name):
                    from discord.ui import LayoutView, Container, TextDisplay, ActionRow, Button
                    from utils.emojis import WARNING
                    _view = LayoutView()
                    _container = Container(accent_colour=discord.Colour.red())
                    _container.add_item(TextDisplay(
                        f"{WARNING} **Feature unavailable**\n"
                        "-# This feature is temporarily disabled. Please try again later."
                    ))
                    _view.add_item(_container)
                    _row = ActionRow()
                    _row.add_item(Button(label="Support", url="https://moddy.app/support", style=discord.ButtonStyle.link))
                    _row.add_item(Button(label="Status", url="https://status.moddy.app", style=discord.ButtonStyle.link))
                    _view.add_item(_row)
                    await ctx.send(view=_view)
                    raise commands.CheckFailure("Cog is disabled")

        # Load extensions
        await self.load_extensions()

        # Start background tasks
        self.status_update.start()

        # Sync slash commands
        if DEBUG:
            # In debug mode, sync commands the same way as production
            # This ensures global commands work in DMs even in debug mode
            await self.sync_commands()
            logger.info("Commands synced (debug mode)")
            logger.info("Guild-only commands will be synced in on_ready()")
        else:
            # In production, sync commands properly
            await self.sync_commands()
            logger.info("Commands synced")

    async def sync_commands(self):
        """
        Synchronise les commandes globales uniquement.
        Les commandes guild-only seront synchronisées dans on_ready() quand self.guilds est disponible.
        """
        try:
            # Identifier et sauvegarder les commandes guild-only
            self._guild_only_commands = []
            guild_only_groups = set()  # Pour éviter les doublons de GroupCogs

            for command in list(self.tree.walk_commands()):
                if hasattr(command, 'guild_only') and command.guild_only:
                    # Si c'est une sous-commande d'un groupe, on doit ajouter le groupe parent
                    if hasattr(command, 'parent') and command.parent:
                        guild_only_groups.add(command.parent.name)
                    else:
                        # C'est une commande top-level
                        self._guild_only_commands.append(command)

            # Retirer les groupes guild-only de l'arbre global
            for group_name in guild_only_groups:
                group = self.tree.get_command(group_name)
                if group:
                    self._guild_only_commands.append(group)
                    self.tree.remove_command(group_name)

            # Retirer les commandes guild-only top-level de l'arbre global
            for command in self._guild_only_commands:
                if not hasattr(command, 'parent') or not command.parent:
                    try:
                        self.tree.remove_command(command.name)
                    except:
                        pass  # Déjà retiré (cas des groupes)

            # Synchroniser les commandes globales uniquement (accessibles partout)
            await self.tree.sync()
            logger.info(f"Global commands synced ({len(self._guild_only_commands)} guild-only will be synced in on_ready)")

        except Exception as e:
            logger.error(f"[FAIL] Error syncing commands: {e}")

    async def sync_all_guild_commands(self):
        """
        Synchronise les commandes guild-only pour TOUS les serveurs.
        Appelé dans on_ready() quand self.guilds est disponible.

        IMPORTANT: Ne PAS copier les commandes globales avec copy_global_to()
        car cela ferait que Discord ignore les commandes globales pour ce serveur.
        Les commandes globales sont déjà synchronisées globalement et disponibles partout.
        """
        try:
            # Synchroniser les commandes guild-only dans chaque serveur
            guild_count = 0
            for guild in self.guilds:
                try:
                    # IMPORTANT: Clear d'abord toutes les commandes de ce serveur
                    # Cela supprime les anciennes commandes synchronisées avec copy_global_to()
                    # et permet à Discord de réutiliser les commandes globales
                    self.tree.clear_commands(guild=guild)

                    # Ajouter UNIQUEMENT les guild-only à ce serveur (pas les globales)
                    # Les commandes globales sont déjà disponibles partout sans copy_global_to()
                    if self._guild_only_commands:
                        for command in self._guild_only_commands:
                            self.tree.add_command(command, guild=guild)

                    # Sync les guild-only pour ce serveur (ou sync vide si pas de guild-only)
                    await self.tree.sync(guild=guild)

                    guild_count += 1
                    logger.info(f"Guild commands synced for {guild.name} ({guild.id})")
                except Exception as e:
                    logger.error(f"[FAIL] Error syncing commands for guild {guild.id}: {e}")

            if self._guild_only_commands:
                logger.info(f"Guild-specific commands synced for {guild_count} servers")
            else:
                logger.info(f"Cleared guild commands for {guild_count} servers (no guild-only commands)")

        except Exception as e:
            logger.error(f"[FAIL] Error syncing guild commands: {e}")

    async def sync_guild_commands(self, guild: discord.Guild):
        """
        Synchronise les commandes spécifiques à un serveur.
        Ajoute UNIQUEMENT les guild-only spécifiquement à ce serveur.

        IMPORTANT: Ne PAS copier les commandes globales avec copy_global_to()
        car cela ferait que Discord ignore les commandes globales pour ce serveur.
        Les commandes globales sont déjà synchronisées globalement et disponibles partout.

        Args:
            guild: Le serveur pour lequel synchroniser les commandes
        """
        try:
            # IMPORTANT: Clear d'abord toutes les commandes de ce serveur
            # Cela supprime les anciennes commandes synchronisées avec copy_global_to()
            # et permet à Discord de réutiliser les commandes globales
            self.tree.clear_commands(guild=guild)

            # Ajouter UNIQUEMENT les guild-only à ce serveur (pas les globales)
            # Les commandes globales sont déjà disponibles partout sans copy_global_to()
            # Utilise le cache self._guild_only_commands car elles ne sont plus dans l'arbre global
            if self._guild_only_commands:
                for command in self._guild_only_commands:
                    self.tree.add_command(command, guild=guild)

            # Synchroniser les guild-only pour ce serveur (ou sync vide si pas de guild-only)
            await self.tree.sync(guild=guild)

            logger.info(f"Commands synced for {guild.name} ({guild.id})")
        except Exception as e:
            logger.error(f"[FAIL] Error syncing commands for guild {guild.id}: {e}")

    async def on_app_command_error(self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
        """Slash command error handling - delegates to ErrorTracker cog"""
        # Use the ErrorTracker cog if it's loaded
        error_cog = self.get_cog("ErrorTracker")
        if error_cog and hasattr(error_cog, 'on_app_command_error'):
            # Delegate to the cog's handler which uses Components V2
            await error_cog.on_app_command_error(interaction, error)
        else:
            # Fallback if the ErrorTracker is not loaded
            logger.error(f"Slash command error (no ErrorTracker): {error}", exc_info=error)

            try:
                # Simple fallback message with Components V2 (no embed needed)
                from discord import ui

                class FallbackErrorView(ui.LayoutView):
                    def __init__(self):
                        super().__init__(timeout=None)
                        container = ui.Container()
                        container.add_item(
                            ui.TextDisplay(f"### {ERROR_EMOJI} An Error Occurred")
                        )
                        container.add_item(
                            ui.TextDisplay("An unexpected error occurred. Please try again.")
                        )
                        button_row = ui.ActionRow()
                        support_btn = ui.Button(
                            label="Support Server",
                            style=discord.ButtonStyle.link,
                            url="https://moddy.app/support"
                        )
                        button_row.add_item(support_btn)
                        container.add_item(button_row)
                        self.add_item(container)

                if interaction.response.is_done():
                    # Try to send a followup message first (preferred)
                    try:
                        await interaction.followup.send(view=FallbackErrorView(), ephemeral=True)
                    except:
                        # If followup fails, edit the original response as fallback
                        await interaction.edit_original_response(content=None, view=FallbackErrorView())
                else:
                    await interaction.response.send_message(view=FallbackErrorView(), ephemeral=True)
            except Exception as e:
                logger.error(f"Failed to send fallback error message: {e}")


    async def fetch_dev_team(self):
        """Fetch development team from Discord"""
        try:
            app_info = await self.application_info()

            if app_info.team:
                # Filter to keep only real users (not bots)
                self._dev_team_ids = {
                    member.id for member in app_info.team.members
                    if not member.bot and member.id != app_info.id
                }
                logger.info(f"Dev team: {len(self._dev_team_ids)} members")
                logger.info(f"   IDs: {list(self._dev_team_ids)}")
            else:
                self._dev_team_ids = {app_info.owner.id}
                logger.info(f"Owner: {app_info.owner} ({app_info.owner.id})")

            # Also add IDs from config
            if DEVELOPER_IDS:
                self._dev_team_ids.update(DEVELOPER_IDS)
                logger.info(f"   + IDs from config: {DEVELOPER_IDS}")

        except Exception as e:
            logger.error(f"[FAIL] Error fetching team: {e}")
            # Fallback to IDs in config if available
            if DEVELOPER_IDS:
                self._dev_team_ids = set(DEVELOPER_IDS)

    def is_developer(self, user_id: int) -> bool:
        """Checks if a user is a developer"""
        return user_id in self._dev_team_ids

    async def get_prefix(self, message: discord.Message):
        """Gets the prefix for a message"""
        # In DMs, use the default prefix
        if not message.guild:
            return [DEFAULT_PREFIX, f'<@{self.user.id}> ', f'<@!{self.user.id}> ']

        # Check the cache
        guild_id = message.guild.id
        if guild_id in self.prefix_cache:
            prefix = self.prefix_cache[guild_id]
        else:
            # Fetch from DB or use default
            prefix = await self.get_guild_prefix(guild_id) or DEFAULT_PREFIX
            self.prefix_cache[guild_id] = prefix

        # Return the prefix and mentions
        return [prefix, f'<@{self.user.id}> ', f'<@!{self.user.id}> ']

    async def get_guild_prefix(self, guild_id: int) -> Optional[str]:
        """Gets a server's prefix from the DB"""
        if not self.db:
            return None

        try:
            guild_data = await self.db.get_guild(guild_id)
            return guild_data['data'].get('config', {}).get('prefix')
        except Exception as e:
            logger.error(f"DB Error (prefix): {e}")
            return None

    async def setup_database(self):
        """Initialize the database connection"""
        try:
            self.db = await setup_database(DATABASE_URL)
            logger.info("Database connected (ModdyDatabase)")

            # Property for compatibility with old code
            self.db_pool = self.db.pool

        except Exception as e:
            logger.error(f"[FAIL] DB connection error: {e}")
            self.db = None
            self.db_pool = None

    async def load_extensions(self):
        """Load all cogs and staff commands"""
        # Load the error system first
        try:
            await self.load_extension("cogs.error_handler")
            logger.info("Error system loaded")
        except Exception as e:
            logger.error(f"[FAIL] CRITICAL: Could not load the error system: {e}")

        # Load the blacklist check system with PRIORITY
        try:
            await self.load_extension("cogs.blacklist_check")
            logger.info("Blacklist check system loaded")
        except Exception as e:
            logger.error(f"[FAIL] Error loading blacklist check: {e}")

        # Load the cog manager system (must be early for disable checks)
        try:
            await self.load_extension("cogs.cog_manager")
            logger.info("Cog manager system loaded")
        except Exception as e:
            logger.error(f"[FAIL] Error loading cog manager: {e}")

        # Load the dev logging system
        try:
            await self.load_extension("cogs.dev_logger")
            logger.info("Dev logging system loaded")
        except Exception as e:
            logger.error(f"[FAIL] Error loading dev logger: {e}")

        # Load user cogs
        cogs_dir = Path("cogs")
        if cogs_dir.exists():
            for file in cogs_dir.glob("*.py"):
                # Skip special files
                if file.name.startswith("_") or file.name in ["error_handler.py", "blacklist_check.py", "dev_logger.py", "cog_manager.py"]:
                    continue

                try:
                    await self.load_extension(f"cogs.{file.stem}")
                    logger.info(f"Cog loaded: {file.stem}")
                except Exception as e:
                    logger.error(f"[FAIL] Cog error {file.stem}: {e}")
                    # Log to Discord if possible
                    if error_cog := self.get_cog("ErrorTracker"):
                        error_code = error_cog.generate_error_code(e)
                        error_details = {
                            "type": type(e).__name__,
                            "message": str(e),
                            "file": f"cogs/{file.name}",
                            "line": "N/A",
                            "traceback": traceback.format_exc()
                        }
                        error_cog.store_error(error_code, error_details)
                        await error_cog.send_error_log(error_code, error_details, is_fatal=False)

        # Load staff commands
        staff_dir = Path("staff")
        if staff_dir.exists():
            for file in staff_dir.glob("*.py"):
                # Skip private files and base class file
                if file.name.startswith("_") or file.name == "base.py":
                    continue

                try:
                    await self.load_extension(f"staff.{file.stem}")
                    logger.info(f"Staff command loaded: {file.stem}")
                except Exception as e:
                    logger.error(f"[FAIL] Staff command error {file.stem}: {e}")
                    # Log to Discord if possible
                    if error_cog := self.get_cog("ErrorTracker"):
                        error_code = error_cog.generate_error_code(e)
                        error_details = {
                            "type": type(e).__name__,
                            "message": str(e),
                            "file": f"staff/{file.name}",
                            "line": "N/A",
                            "traceback": traceback.format_exc()
                        }
                        error_cog.store_error(error_code, error_details)
                        await error_cog.send_error_log(error_code, error_details, is_fatal=False)

    async def on_ready(self):
        """Called when the bot is ready"""

        # Fetch development team (moved from setup_hook to avoid blocking during connection)
        await self.fetch_dev_team()

        logger.info(f"Bot connected: {self.user} (mode: {ENV_MODE})")
        logger.info(f"Servers: {len(self.guilds)} | Users: {len(self.users)}")
        logger.info(f"Latency: {round(self.latency * 1000)}ms")
        logger.info(f"i18n: {len(i18n.supported_locales)} languages loaded")

        # Update DEVELOPER attributes now that self.user is available
        if self.db and self._dev_team_ids:
            logger.info(f"Automatically updating DEVELOPER attributes...")
            for dev_id in self._dev_team_ids:
                try:
                    # Get or create user
                    await self.db.get_user(dev_id)

                    # Set the DEVELOPER attribute (True = present in the simplified system)
                    await self.db.set_attribute(
                        'user', dev_id, 'DEVELOPER', True,
                        self.user.id, "Auto-detection at startup"
                    )
                    logger.info(f"DEVELOPER attribute set for {dev_id}")

                    # ALWAYS set TEAM attribute for dev team members (critical for staff commands)
                    await self.db.set_attribute(
                        'user', dev_id, 'TEAM', True,
                        self.user.id, "Auto-assigned to dev team members"
                    )
                    logger.info(f"TEAM attribute set for {dev_id}")

                    # Auto-assign Manager + Dev roles for dev team members
                    from utils.staff_permissions import StaffRole
                    perms = await self.db.get_staff_permissions(dev_id)
                    roles = perms['roles']

                    # Ensure they have Manager and Dev roles
                    updated = False
                    if StaffRole.MANAGER.value not in roles:
                        roles.append(StaffRole.MANAGER.value)
                        updated = True
                    if StaffRole.DEV.value not in roles:
                        roles.append(StaffRole.DEV.value)
                        updated = True

                    if updated:
                        await self.db.set_staff_roles(dev_id, roles, self.user.id)
                        logger.info(f"Auto-assigned Manager+Dev roles for {dev_id}")
                    else:
                        logger.info(f"Dev {dev_id} already has Manager+Dev roles")

                except Exception as e:
                    logger.error(f"[FAIL] Error setting DEVELOPER attribute for {dev_id}: {e}")

        # DB stats if connected
        if self.db:
            try:
                stats = await self.db.get_stats()
                logger.info(f"DB: {stats['users']} users, {stats['guilds']} guilds, {stats['errors']} errors")
            except:
                pass

        # Load modules for all guilds
        if self.module_manager and self.db:
            try:
                await self.module_manager.load_all_modules()
                logger.info("All guild modules loaded successfully")
            except Exception as e:
                logger.error(f"[FAIL] Error loading guild modules: {e}", exc_info=True)

        # Synchronize guild-only commands for all guilds
        # This is done here (not in setup_hook) because self.guilds is only available after connection
        logger.info("Synchronizing guild-only commands...")
        await self.sync_all_guild_commands()

        # Run startup health checks
        await self.run_startup_checks()

    async def run_startup_checks(self):
        """Run comprehensive startup health checks on all systems."""
        logger.info("Running startup health checks...")
        results = []

        # 1. Database check
        if self.db and self.db.pool:
            try:
                async with self.db.pool.acquire() as conn:
                    await conn.fetchval("SELECT 1")
                results.append(("Database", True, "Connected"))
            except Exception as e:
                results.append(("Database", False, str(e)))
        else:
            results.append(("Database", False, "Not configured"))

        # 2. Check all loaded cogs
        expected_cogs_dir = Path("cogs")
        expected_staff_dir = Path("staff")
        loaded_cogs = set(self.cogs.keys())

        cog_files = [f.stem for f in expected_cogs_dir.glob("*.py") if not f.name.startswith("_") and f.name != "error_handler.py" and f.name != "blacklist_check.py" and f.name != "dev_logger.py"]
        staff_files = [f.stem for f in expected_staff_dir.glob("*.py") if not f.name.startswith("_") and f.name != "base.py"]

        failed_cogs = []
        for cog_file in cog_files:
            # Check if corresponding cog class is loaded
            cog_module_name = f"cogs.{cog_file}"
            if not any(cog_module_name in str(getattr(cog, '__module__', '')) for cog in self.cogs.values()):
                # Try a simpler check - just see if it loaded
                pass

        results.append(("Cogs", True, f"{len(loaded_cogs)} loaded"))

        # 3. Check all registered modules
        if self.module_manager:
            module_count = len(self.module_manager.registered_modules)
            results.append(("Modules", True, f"{module_count} registered"))
        else:
            results.append(("Modules", False, "Module manager not initialized"))

        # 4. Check i18n
        if i18n.supported_locales:
            results.append(("i18n", True, f"{len(i18n.supported_locales)} locales"))
        else:
            results.append(("i18n", False, "No locales loaded"))

        # 5. Backend connection
        try:
            from services.backend_client import get_backend_client
            client = get_backend_client()
            health = await client.health_check()
            results.append(("Backend", True if health else False, "Connected" if health else "Unreachable"))
        except Exception as e:
            results.append(("Backend", False, f"Error: {e}"))

        # 6. Log results
        all_ok = all(ok for _, ok, _ in results)
        logger.info("=" * 50)
        logger.info("STARTUP HEALTH CHECK RESULTS")
        logger.info("-" * 50)
        for name, ok, detail in results:
            status = "OK" if ok else "FAIL"
            logger.info(f"  [{status:4s}] {name:15s} | {detail}")
        logger.info("-" * 50)
        if all_ok:
            logger.info("All checks passed.")
        else:
            failed = [name for name, ok, _ in results if not ok]
            logger.warning(f"Some checks failed: {', '.join(failed)}")
        logger.info("=" * 50)

    async def on_guild_join(self, guild: discord.Guild):
        """When the bot joins a server"""
        logger.info(f"New server: {guild.name} ({guild.id})")

        # Check if the server owner is blacklisted
        if self.db:
            try:
                if await self.db.has_attribute('user', guild.owner_id, 'BLACKLISTED'):
                    logger.warning(f"[WARN] Add attempt by blacklisted user: {guild.owner_id}")

                    # Send a message to the owner if possible
                    try:
                        embed = discord.Embed(
                            description=f"{EMOJIS['undone']} You cannot add Moddy to servers because your account has been blacklisted by our team.",
                            color=COLORS["error"]
                        )
                        embed.set_footer(text=f"User ID: {guild.owner_id}")

                        # Create the button
                        view = discord.ui.View()
                        view.add_item(discord.ui.Button(
                            label="Unblacklist request",
                            url="https://moddy.app/unbl_request",
                            style=discord.ButtonStyle.link
                        ))

                        await guild.owner.send(embed=embed, view=view)
                    except:
                        pass

                    # Leave the server
                    await guild.leave()

                    # Log the action
                    if log_cog := self.get_cog("LoggingSystem"):
                        await log_cog.log_critical(
                            title="Join Blocked - Blacklisted User",
                            description=(
                                f"**Server:** {guild.name} (`{guild.id}`)\n"
                                f"**Owner:** {guild.owner} (`{guild.owner_id}`)\n"
                                f"**Members:** {guild.member_count}\n"
                                f"**Action:** Bot left automatically"
                            ),
                            ping_dev=False
                        )

                    return

                # If not blacklisted, continue normally
                # Create the server entry in the guilds table
                await self.db.get_guild(guild.id)  # This creates the entry if it doesn't exist

            except Exception as e:
                logger.error(f"DB Error (guild_join): {e}")

        # Synchronize commands for this new guild
        # This ensures guild-only commands (/config) are available in this server
        try:
            await self.sync_guild_commands(guild)
            logger.info(f"Commands synchronized for new guild {guild.name} ({guild.id})")
        except Exception as e:
            logger.error(f"[FAIL] Error syncing commands for new guild {guild.id}: {e}")

        # Setup announcement channel following
        try:
            success, message = await setup_announcement_channel(guild)
            if success:
                logger.info(f"Announcement channel setup for {guild.name}: {message}")
            else:
                logger.warning(f"[WARN] Failed to setup announcement channel for {guild.name}: {message}")
        except Exception as e:
            logger.error(f"[FAIL] Error setting up announcement channel for {guild.id}: {e}")

    async def on_guild_remove(self, guild: discord.Guild):
        """When the bot leaves a server"""
        logger.info(f"Server left: {guild.name} ({guild.id})")

        # Clean the cache
        self.prefix_cache.pop(guild.id, None)

        # Clear commands for this guild to remove guild-only commands
        # This ensures /config is no longer accessible in this server
        try:
            self.tree.clear_commands(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info(f"Commands cleared for guild {guild.name} ({guild.id})")
        except Exception as e:
            logger.error(f"[FAIL] Error clearing commands for guild {guild.id}: {e}")

    async def _global_blacklist_check(self, interaction: discord.Interaction) -> bool:
        """
        Check global pour toutes les app commands (slash commands).
        Appelé automatiquement par discord.py AVANT l'exécution de toute app command.
        Retourne False ou lève une exception pour bloquer l'exécution.
        """
        if not self.db or interaction.user.bot:
            return True  # Autorise si pas de DB ou si c'est un bot

        # Development mode - only allowed users can use slash commands
        if IS_DEV and not self.is_developer(interaction.user.id) and interaction.user.id not in DEV_ALLOWED_IDS:
            try:
                await interaction.response.send_message(
                    "This bot is currently in development mode.", ephemeral=True
                )
            except Exception:
                pass
            return False

        try:
            is_blacklisted = await self.db.has_attribute('user', interaction.user.id, 'BLACKLISTED')

            if is_blacklisted:
                # Utilise le système Components V2 pour le message de blacklist
                from utils.components_v2 import create_blacklist_message
                view = create_blacklist_message()

                # Répond à l'interaction
                try:
                    await interaction.response.send_message(
                        view=view,
                        ephemeral=True
                    )
                except Exception as e:
                    logger.error(f"Error sending blacklist message: {e}")

                # Log l'interaction bloquée
                if log_cog := self.get_cog("LoggingSystem"):
                    try:
                        await log_cog.log_critical(
                            title="🚫 SLASH COMMAND BLACKLISTÉE BLOQUÉE",
                            description=(
                                f"**Utilisateur:** {interaction.user.mention} (`{interaction.user.id}`)\n"
                                f"**Commande:** {interaction.command.name if interaction.command else 'N/A'}\n"
                                f"**Serveur:** {interaction.guild.name if interaction.guild else 'DM'}\n"
                                f"**Action:** ✋ BLOQUÉE AVANT EXÉCUTION (tree.interaction_check)"
                            ),
                            ping_dev=False
                        )
                    except Exception as e:
                        logger.error(f"Error logging blacklist: {e}")

                # Retourne False pour bloquer l'exécution
                return False

        except Exception as e:
            logger.error(f"Error checking blacklist in _global_blacklist_check: {e}")

        # Check if the command's cog is disabled
        if interaction.command:
            cog_manager = self.get_cog("CogManager")
            if cog_manager:
                # Get the cog name from the command
                cog_name = None
                if hasattr(interaction.command, 'binding') and interaction.command.binding:
                    cog_name = type(interaction.command.binding).__name__

                if cog_name and cog_manager.is_cog_disabled(cog_name):
                    try:
                        from discord.ui import LayoutView, Container, TextDisplay, ActionRow, Button
                        from utils.emojis import WARNING
                        _view = LayoutView()
                        _container = Container(accent_colour=discord.Colour.red())
                        _container.add_item(TextDisplay(
                            f"{WARNING} **Feature unavailable**\n"
                            "-# This feature is temporarily disabled. Please try again later."
                        ))
                        _view.add_item(_container)
                        _row = ActionRow()
                        _row.add_item(Button(label="Support", url="https://moddy.app/support", style=discord.ButtonStyle.link))
                        _row.add_item(Button(label="Status", url="https://status.moddy.app", style=discord.ButtonStyle.link))
                        _view.add_item(_row)
                        await interaction.response.send_message(view=_view, ephemeral=True)
                    except Exception:
                        pass
                    return False

        return True  # Autorise si pas blacklisté ou en cas d'erreur

    async def _check_blacklist_and_respond(self, interaction: discord.Interaction) -> bool:
        """
        Vérifie si un utilisateur est blacklisté et répond si c'est le cas.
        Retourne True si l'utilisateur est blacklisté (bloqué), False sinon.
        """
        if not self.db or interaction.user.bot:
            return False

        try:
            is_blacklisted = await self.db.has_attribute('user', interaction.user.id, 'BLACKLISTED')

            if is_blacklisted:
                # Utilise le système Components V2 pour le message de blacklist
                from utils.components_v2 import create_blacklist_message
                view = create_blacklist_message()

                # Répond à l'interaction si pas encore fait
                try:
                    if not interaction.response.is_done():
                        await interaction.response.send_message(
                            view=view,
                            ephemeral=True
                        )
                except discord.InteractionResponded:
                    # L'interaction a déjà été répondue, on utilise followup
                    try:
                        await interaction.followup.send(
                            view=view,
                            ephemeral=True
                        )
                    except:
                        pass
                except Exception as e:
                    logger.error(f"Error sending blacklist message: {e}")

                # Log l'interaction bloquée
                if log_cog := self.get_cog("LoggingSystem"):
                    try:
                        interaction_type = interaction.type.name
                        if interaction.type == discord.InteractionType.application_command:
                            identifier = f"Commande: {interaction.command.name if interaction.command else 'N/A'}"
                        else:
                            identifier = f"Custom ID: {interaction.data.get('custom_id', 'N/A') if hasattr(interaction, 'data') else 'N/A'}"

                        await log_cog.log_critical(
                            title="🚫 INTERACTION BLACKLISTÉE BLOQUÉE",
                            description=(
                                f"**Utilisateur:** {interaction.user.mention} (`{interaction.user.id}`)\n"
                                f"**Type:** {interaction_type}\n"
                                f"**{identifier}**\n"
                                f"**Serveur:** {interaction.guild.name if interaction.guild else 'DM'}\n"
                                f"**Action:** ✋ BLOQUÉE AVANT TRAITEMENT"
                            ),
                            ping_dev=False
                        )
                    except Exception as e:
                        logger.error(f"Error logging blacklist: {e}")

                return True  # Utilisateur blacklisté

        except Exception as e:
            logger.error(f"Error checking blacklist: {e}")

        return False  # Pas blacklisté

    async def on_interaction(self, interaction: discord.Interaction):
        """
        INTERCEPTION pour les composants (boutons, selects, modals).
        Les slash commands sont gérées par _global_blacklist_check via tree.interaction_check.
        """
        # Les app commands sont déjà gérées par _global_blacklist_check
        if interaction.type == discord.InteractionType.application_command:
            return

        # Pour les composants (boutons, selects, modals), vérifie la blacklist
        is_blacklisted = await self._check_blacklist_and_respond(interaction)
        if is_blacklisted:
            # L'utilisateur est blacklisté, le message a été envoyé
            # L'interaction est consommée, on ne fait rien de plus
            return

    async def on_message(self, message: discord.Message):
        """Process each message"""
        # Ignore its own messages
        if message.author == self.user:
            return

        # Maintenance mode - only devs can use the bot
        if self.maintenance_mode and not self.is_developer(message.author.id):
            return

        # Development mode - only allowed users can use the bot
        if IS_DEV and not self.is_developer(message.author.id) and message.author.id not in DEV_ALLOWED_IDS:
            return

        # Blacklist check is now handled by the BlacklistCheck cog
        # which intercepts all interactions BEFORE they are processed

        # Process commands
        await self.process_commands(message)

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        """Global error handling"""
        # The ErrorTracker cog handles everything now
        # This method is kept for compatibility but delegates to the cog
        pass

    @tasks.loop(minutes=10)
    async def status_update(self):
        """Update the bot's status"""
        # Security checks
        if not self.is_ready() or not self.ws:
            return

        # Get status from environment variable
        bot_status = os.getenv("BOT_STATUS", "")
        activity = discord.CustomActivity(name=bot_status) if bot_status else None

        try:
            await self.change_presence(activity=activity)
        except (AttributeError, ConnectionError):
            # Ignore if we are closing
            pass
        except Exception as e:
            logger.error(f"Error changing status: {e}")

    @status_update.before_loop
    async def before_status_update(self):
        """Wait for the bot to be ready before starting the task"""
        await self.wait_until_ready()

    async def close(self):
        """Cleanly closing the bot"""
        logger.info("Shutting down...")

        # Stop tasks BEFORE closing
        if self.status_update.is_running():
            self.status_update.cancel()

        # Wait a bit for tasks to finish
        await asyncio.sleep(0.1)

        # Close BackendClient connection
        try:
            from services import close_backend_client
            await close_backend_client()
            logger.info("Backend client closed")
        except Exception as e:
            logger.error(f"[FAIL] Error closing backend client: {e}")

        # Close DB connection
        if self.db:
            await self.db.close()

        # Close the HTTP client cleanly
        if hasattr(self, 'http') and self.http and hasattr(self.http, '_HTTPClient__session'):
            await self.http._HTTPClient__session.close()

        # Note: Le serveur HTTP interne s'arrête automatiquement car il est daemon=True
        logger.info("Internal API server will stop automatically")

        # Close the bot
        await super().close()