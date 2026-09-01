"""
Moddy - Main bot class
Handles all core logic and events
"""

import discord
from discord.ext import commands, tasks
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Optional, Set
import os
import sys
from pathlib import Path
import traceback
import aiohttp

import math

from config import (
    DEBUG,
    DEFAULT_PREFIX,
    DATABASE_URL,
    REDIS_URL,
    REDIS_PASSWORD,
    DEVELOPER_IDS,
    COLORS,
    IS_DEV,
    IS_PROD,
    IS_MAINTENANCE,
    DEV_ALLOWED_IDS,
    ENV_MODE,
    HM_URL,
    HM_INGEST_TOKEN,
    BETTERSTACK_HEARTBEAT_URL,
)
from utils.emojis import EMOJIS, ERROR as ERROR_EMOJI

from utils import global_sanctions
from utils.components_v2 import create_suspension_message
from database import setup_database, db
# Import du nouveau système i18n
from utils.i18n import i18n
# Slash command name/description localization (see docs/COMMAND_LOCALIZATION.md)
from utils.command_translator import ModdyCommandTranslator
# Import du système de permissions staff
from utils.staff_permissions import setup_staff_permissions
# Import du système de logging staff
from utils.staff_logger import init_staff_logger
# Import du gestionnaire de modules
from modules.module_manager import ModuleManager
# Import du système de configuration des annonces
from utils.announcement_setup import setup_announcement_channel
from moddy import Bot as ModdyFrameworkBot

logger = logging.getLogger('moddy')

# Branded display name style (font/effect/color) applied to Moddy's own
# member profile whenever it joins a new server. Requires "Change Nickname".
NAME_STYLE_FONT_ID = 11
NAME_STYLE_EFFECT_ID = 5
NAME_STYLE_COLORS = [0x0004FF]


class ModdyBot(ModdyFrameworkBot):
    """Main Moddy class"""

    def __init__(self):
        # Required intents. `presences` stays off (Intents.default() excludes it):
        # nothing reads member presence from the gateway — /invite takes its online
        # count from the REST API's approximate_presence_count instead.
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.members = True

        # Cache joined members (get_member() is used across appeals, automod,
        # interserver, reminders and the staff commands) but not voice state:
        # there is no on_voice_state_update listener anywhere in the codebase, so
        # the voice cache is paid for and never read.
        member_cache_flags = discord.MemberCacheFlags(joined=True, voice=False)

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
            # Global (all-guild) message cache, evicted by count, not by age.
            # Its only consumers are the non-raw on_message_delete listeners
            # (interserver relay cleanup, staff auto-delete, the deleted-message
            # content cache behind the AI-suggested sanction reason) and the
            # non-raw on_reaction_add feeding the automod relationship graph
            # (REACTION_WAIT_SECONDS = 20). All of those act on messages seconds
            # to minutes old, so 10000 was far past the useful window while
            # costing ~1.5-3 KB per resident Message.
            max_messages=5000,
            member_cache_flags=member_cache_flags,
            http_timeout=http_timeout  # Apply custom timeout
        )

        # Internal variables
        self.launch_time = datetime.now(timezone.utc)
        self._start_time = None  # Set in setup_hook once event loop is running
        self.db = None  # ModdyDatabase instance
        from services.case_service import CaseService
        self.cases = CaseService(self)  # scalable sanction -> case entry point
        from services.appeal_service import AppealService
        self.appeals = AppealService(self)  # sanction appeals (server / Moddy team)
        from services.global_sanction_service import GlobalSanctionService
        # Moddy-team global sanctions: grouped cases, one notice, one countdown
        self.global_sanctions = GlobalSanctionService(self)
        from services.expiration_notifier import ExpirationNotifier
        # Expired sanctions: lift the Discord action + notify the subject
        self.expirations = ExpirationNotifier(self)
        from services.precedent_service import PrecedentService
        self.precedents = PrecedentService(self)  # automod server precedents (RAG)
        from services.transcription_service import TranscriptionService
        self.transcription = TranscriptionService(self)  # voice message speech-to-text
        from services.altguard_client import AltGuardClient
        # AltGuard anti multi-account verification (HTTP + altguard:* Pub/Sub)
        self.altguard = AltGuardClient(self)
        from services.ticket_service import TicketService
        self.tickets = TicketService(self)  # ticket lifecycle (open/close/escalate…)
        from services.invoice_notifier import InvoiceNotifier
        # Stripe invoices (notify_invoice): one DM per invoice, trials included
        self.invoices = InvoiceNotifier(self)
        from services.stripe_admin_client import StripeAdminClient
        # Signed Stripe admin actions (cancel/resume/refund/trial) over
        # moddy:dashboard <-> moddy:bot, correlated by request_id.
        self.stripe_admin = StripeAdminClient(self)
        from notifications import NotificationService
        # Every DM / server notice Moddy sends goes through this: stored,
        # attributed, reportable (see docs/NOTIFICATIONS.md).
        self.notifications = NotificationService(self)
        from services.support_request_service import SupportRequestService
        # Bug reports (/bug-report) and "configure it for me" requests: opened
        # by users, answered by the team (see docs/SUPPORT_REQUESTS.md).
        self.support_requests = SupportRequestService(self)
        from gateway import Gateway
        self.gateway = Gateway()
        from services.heartbeat import HeartbeatClient
        # Dead man's switch push to the Moddy Health Monitor (docs/HEALTH_MONITOR.md).
        # Started in on_ready (a bot that was never ready has nothing to report).
        self.heartbeat = HeartbeatClient(
            "moddy-bot",
            url=HM_URL,
            token=HM_INGEST_TOKEN,
            build=self._build_heartbeat_checks,
        )
        from services.betterstack_heartbeat import BetterStackHeartbeat
        # Better Stack cron/heartbeat monitor: a plain ping every 3 minutes,
        # `/fail` when the bot itself thinks it is unhealthy (docs/HEALTH_MONITOR.md).
        self.betterstack_heartbeat = BetterStackHeartbeat(
            url=BETTERSTACK_HEARTBEAT_URL,
            healthy=self._is_bot_healthy,
        )
        self.redis = None  # Redis client (shared with backend)
        self._dev_team_ids: Set[int] = set()
        self.maintenance_mode = False
        self.version = None  # Bot version from GitHub releases

        # Cache for server prefixes
        self.prefix_cache = {}

        # Gestionnaire de modules
        self.module_manager = None

        # Cache pour les commandes guild-only (NE JAMAIS les remettre dans l'arbre global)
        self._guild_only_commands = []

        # Staff slash command groups (/dev, /team, ...). Published by the staff
        # framework cog; synced ONLY to OFFICIAL guilds. Never added to the
        # global tree, so they can never leak to non-official servers.
        self.staff_slash_groups = []

        # Commands owned by a server module (e.g. /ticket), published ONLY in
        # the guilds where that module is enabled — see
        # register_module_commands(). module_id -> [app command objects].
        self.module_slash_commands: Dict[str, list] = {}

        # What each guild's module command set was the last time it was synced.
        # Discord rate-limits guild command syncs hard, so a config save that
        # does not change which modules own commands must not spend one.
        self._guild_module_commands: Dict[int, frozenset] = {}

        # Serveur HTTP interne pour /status
        self.internal_api_server = None
        self.internal_api_thread = None

        # Configure global error handler
        self.setup_error_handler()

        # INTERCEPTION RADICALE: check des sanctions globales pour toutes les app commands
        self.tree.interaction_check = self._global_sanction_check

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
        Expose GET /health et GET /status (appelé par le backend pour les métriques).
        """
        import threading
        import uvicorn
        from internal_api.server import app, set_bot

        set_bot(self)

        port = int(os.getenv("PORT", 3000))

        def run_server():
            logger.info(f"Starting internal API server on port {port}")
            uvicorn.run(
                app,
                host="::",  # IPv4 + IPv6 dual-stack
                port=port,
                log_level="warning",
                access_log=False,
            )

        self.internal_api_thread = threading.Thread(
            target=run_server,
            daemon=True,
            name="InternalAPIServer"
        )
        self.internal_api_thread.start()
        logger.info(f"Internal API server started on port {port}")

    async def _setup_redis(self):
        """Initialize Redis connection and start background listeners."""
        import redis.asyncio as aioredis
        try:
            self.redis = aioredis.from_url(
                REDIS_URL,
                password=REDIS_PASSWORD,
                decode_responses=True,
            )
            await self.redis.ping()
            logger.info("Redis connected")
            # Start background tasks
            asyncio.create_task(self._listen_pubsub())
            asyncio.create_task(self._consume_task_stream())
        except Exception as e:
            logger.error(f"[FAIL] Redis connection error: {e}")
            self.redis = None

    async def _listen_pubsub(self):
        """Listen to Redis Pub/Sub channels (non-critical backend notifications)."""
        import json
        while True:
            try:
                pubsub = self.redis.pubsub()
                await pubsub.subscribe(
                    "moddy:bot", "moddy:subscription:updates", "moddy:blacklist:updates",
                    "altguard:verdict",
                )
                logger.info(
                    "Pub/Sub subscribed to moddy:bot, moddy:subscription:updates, "
                    "moddy:blacklist:updates and altguard:verdict"
                )
                async for message in pubsub.listen():
                    if message["type"] != "message":
                        continue
                    try:
                        data = json.loads(message["data"])
                        channel = message.get("channel", "")
                        if channel == "moddy:subscription:updates":
                            await self._handle_subscription_event(data)
                        elif channel == "moddy:blacklist:updates":
                            await self._handle_blacklist_event(data)
                        elif channel == "altguard:verdict":
                            await self._handle_altguard_verdict(data)
                        else:
                            await self._handle_bot_event(data)
                    except Exception as e:
                        logger.error(f"[PubSub] Error handling message: {e}")
            except Exception as e:
                logger.error(f"[PubSub] Connection error: {e}")
                await asyncio.sleep(5)

    async def _handle_subscription_event(self, data: dict):
        """Handle events from the moddy:subscription:updates channel."""
        from utils.subscription import invalidate_cache
        event_type = data.get("type")
        user_id_raw = data.get("user_id")

        if not user_id_raw:
            logger.warning(f"[SubPubSub] Missing user_id in event: {data}")
            return

        try:
            user_id = int(user_id_raw)
        except (ValueError, TypeError):
            logger.warning(f"[SubPubSub] Invalid user_id: {user_id_raw}")
            return

        if event_type == "refresh":
            await invalidate_cache(self, user_id)
            logger.info(f"[SubPubSub] Cache invalidated for user {user_id}")

        elif event_type == "notify_payment_late":
            await invalidate_cache(self, user_id)
            await self._send_subscription_dm(user_id, "payment_late")

        elif event_type == "notify_subscription_started":
            tier = data.get("tier")
            await invalidate_cache(self, user_id)
            await self._send_subscription_dm(user_id, "subscription_started", tier=tier)

        elif event_type == "notify_subscription_renewed":
            tier = data.get("tier")
            await invalidate_cache(self, user_id)
            await self._send_subscription_dm(user_id, "subscription_renewed", tier=tier)

        elif event_type == "notify_subscription_cancelled":
            tier = data.get("tier")
            await invalidate_cache(self, user_id)
            await self._send_subscription_dm(user_id, "subscription_cancelled", tier=tier)

        elif event_type == "notify_subscription_updated":
            tier = data.get("tier")
            await invalidate_cache(self, user_id)
            await self._send_subscription_dm(user_id, "subscription_updated", tier=tier)

        elif event_type == "notify_subscription_upgraded":
            tier = data.get("tier")
            await invalidate_cache(self, user_id)
            await self._send_subscription_dm(user_id, "subscription_upgraded", tier=tier)

        elif event_type == "notify_subscription_downgraded":
            tier = data.get("tier")
            await invalidate_cache(self, user_id)
            await self._send_subscription_dm(user_id, "subscription_downgraded", tier=tier)

        elif event_type == "notify_invoice":
            # A Stripe invoice was issued — including at 0, because a free
            # trial produces a real invoice. The backend already mailed it and
            # deduplicated on the invoice id; the bot only DMs it.
            await invalidate_cache(self, user_id)
            await self.invoices.handle(data)

        else:
            logger.debug(f"[SubPubSub] Unknown subscription event type: {event_type}")

    async def _send_subscription_dm(self, user_id: int, event: str, tier: str | None = None):
        """Send a DM to a user for a subscription lifecycle event."""
        import discord
        from discord import ui

        try:
            user = await self.fetch_user(user_id)
        except discord.NotFound:
            logger.warning(f"[SubDM] User {user_id} not found, cannot send DM")
            return
        except Exception as e:
            logger.error(f"[SubDM] Error fetching user {user_id}: {e}")
            return

        MANAGE_URL = "https://dashboard.moddy.app/billing"
        SELECT_URL = "https://dashboard.moddy.app/select-premium-servers"
        SUPPORT_URL = "https://moddy.app/support"
        ACCENT = discord.Colour(0x245F9F)

        def _action_row(include_select: bool = False) -> ui.ActionRow:
            row = ui.ActionRow()
            row.add_item(ui.Button(
                url=MANAGE_URL,
                style=discord.ButtonStyle.link,
                label="Manage subscription",
            ))
            if include_select:
                row.add_item(ui.Button(
                    url=SELECT_URL,
                    style=discord.ButtonStyle.link,
                    label="Select servers",
                ))
            row.add_item(ui.Button(
                url=SUPPORT_URL,
                style=discord.ButtonStyle.link,
                label="Support",
            ))
            return row

        if event == "subscription_started":
            container = ui.Container(
                ui.TextDisplay(
                    "### <a:GemStone_animated:1509243505845731389> Welcome to Moddy Max !\n"
                    "Your Moddy Max subscription is now active ! Your servers are in good hands.\n"
                    "Your support truly warms our hearts <3\n"
                    "\n"
                    "**One last step: pick your servers.**\n"
                    "Premium is not active on any server until you choose them — "
                    f"open [Select premium servers]({SELECT_URL}) and pick up to **5** "
                    "of the servers you want it on.\n"
                ),
                ui.MediaGallery(
                    discord.MediaGalleryItem(
                        media="https://media.tenor.com/eaDPAe9OLSoAAAAM/cat-kissing.gif",
                    ),
                ),
                ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
                ui.TextDisplay(
                    "-# Premium features are yours everywhere on Discord as a personal app right away, "
                    "and on the 5 servers you select. Use </subscription:1459599678139011357> for details. "
                    "Need help? Contact our [support](https://moddy.app/support).\n"
                ),
                accent_colour=ACCENT,
            )
            view = ui.LayoutView()
            view.add_item(container)
            view.add_item(_action_row(include_select=True))

        elif event == "subscription_renewed":
            from utils.emojis import PREMIUM
            container = ui.Container(
                ui.TextDisplay(
                    f"### {PREMIUM} Subscription renewed\n"
                    "Your **Moddy Max** subscription has been successfully renewed. Thanks for your continued support !\n"
                ),
                ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
                ui.TextDisplay(
                    "-# Your premium access continues uninterrupted. Use </subscription:1459599678139011357> for details.\n"
                ),
                accent_colour=ACCENT,
            )
            view = ui.LayoutView()
            view.add_item(container)
            view.add_item(_action_row(include_select=False))

        elif event == "subscription_cancelled":
            from utils.emojis import PREMIUM
            container = ui.Container(
                ui.TextDisplay(
                    f"### {PREMIUM} Subscription cancelled\n"
                    "Your **Moddy Max** subscription has been cancelled.\n"
                    "You'll keep access until the end of your current billing period.\n"
                ),
                ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
                ui.TextDisplay(
                    "-# You can reactivate your subscription at any time from the dashboard. Need help? Contact our [support](https://moddy.app/support).\n"
                ),
                accent_colour=ACCENT,
            )
            view = ui.LayoutView()
            view.add_item(container)
            view.add_item(_action_row(include_select=False))

        elif event == "subscription_updated":
            from utils.emojis import PREMIUM
            container = ui.Container(
                ui.TextDisplay(
                    f"### {PREMIUM} Subscription updated\n"
                    "Your **Moddy Max** subscription has been updated.\n"
                ),
                ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
                ui.TextDisplay(
                    "-# Use </subscription:1459599678139011357> to see your current subscription details.\n"
                ),
                accent_colour=ACCENT,
            )
            view = ui.LayoutView()
            view.add_item(container)
            view.add_item(_action_row(include_select=False))

        elif event == "subscription_upgraded":
            from utils.emojis import PREMIUM
            container = ui.Container(
                ui.TextDisplay(
                    f"### {PREMIUM} Subscription upgraded\n"
                    "Your subscription has been upgraded to **Moddy Max** ! Enjoy your new premium features.\n"
                ),
                ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
                ui.TextDisplay(
                    "-# Use </subscription:1459599678139011357> to see your current subscription details.\n"
                ),
                accent_colour=ACCENT,
            )
            view = ui.LayoutView()
            view.add_item(container)
            view.add_item(_action_row(include_select=True))

        elif event == "subscription_downgraded":
            from utils.emojis import PREMIUM
            container = ui.Container(
                ui.TextDisplay(
                    f"### {PREMIUM} Subscription changed\n"
                    "Your **Moddy Max** subscription plan has been changed.\n"
                ),
                ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
                ui.TextDisplay(
                    "-# Use </subscription:1459599678139011357> to see your current subscription details. Need help? Contact our [support](https://moddy.app/support).\n"
                ),
                accent_colour=ACCENT,
            )
            view = ui.LayoutView()
            view.add_item(container)
            view.add_item(_action_row(include_select=False))

        elif event == "payment_late":
            from utils.emojis import PREMIUM
            container = ui.Container(
                ui.TextDisplay(
                    f"### {PREMIUM} Payment issue\n"
                    "There was a problem renewing your **Moddy Max** subscription.\n"
                    "Please update your payment information to maintain access.\n"
                ),
                ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
                ui.TextDisplay(
                    "-# Update your billing details from the dashboard. Need help? Contact our [support](https://moddy.app/support).\n"
                ),
                accent_colour=ACCENT,
            )
            view = ui.LayoutView()
            view.add_item(container)
            view.add_item(_action_row(include_select=False))

        else:
            return

        try:
            await user.send(view=view)
            logger.info(f"[SubDM] Sent {event} DM to user {user_id}")
        except discord.Forbidden:
            logger.info(f"[SubDM] Cannot DM user {user_id} (DMs closed)")
        except Exception as e:
            logger.error(f"[SubDM] Error sending DM to user {user_id}: {e}")

    async def _handle_blacklist_event(self, data: dict):
        """Handle events from the moddy:blacklist:updates channel.

        The backend creates/revokes **global** cases (Moddy-team sanctions:
        ``warn`` / ``restrict`` / ``ban``) directly in DB. The bot resolves the
        level through :mod:`utils.global_sanctions`, which caches it in memory,
        so it must be told to drop the entry whenever the backend mutates it.

        Payload: ``{"type": "refresh", "user_id": ...}`` or
        ``{"type": "refresh", "guild_id": ...}``. Sending neither clears the
        whole cache.
        """
        if data.get("type") != "refresh":
            return

        subjects = (
            (global_sanctions.SUBJECT_USER, data.get("user_id")),
            (global_sanctions.SUBJECT_GUILD, data.get("guild_id")),
        )
        targeted = False
        for subject_type, raw in subjects:
            if raw is None:
                continue
            try:
                subject_id = int(raw)
            except (ValueError, TypeError):
                logger.warning(f"[GlobalSanctionPubSub] Invalid {subject_type} id: {raw}")
                continue
            global_sanctions.invalidate(self, subject_type, subject_id)
            targeted = True
            logger.info(f"[GlobalSanctionPubSub] Cache invalidated for {subject_type} {subject_id}")

        if not targeted:
            global_sanctions.invalidate(self)
            logger.info("[GlobalSanctionPubSub] Full global sanction cache invalidated")

    async def _handle_altguard_verdict(self, data: dict):
        """Handle a message from the ``altguard:verdict`` channel.

        The AltGuard service publishes one verdict per finished verification.
        All the logic (validation, idempotency, role changes, logging) lives in
        the AltGuard cog — this only routes.
        """
        cog = self.get_cog("AltGuard")
        if not cog:
            logger.warning("[AltGuard] Verdict received but the AltGuard cog is not loaded")
            return
        await cog.handle_verdict(data)

    async def _handle_bot_event(self, data: dict):
        """Route Pub/Sub events from the backend."""
        event_type = data.get("type")
        guild_id = data.get("guild_id")

        if event_type in ("config_updated", "module_updated", "module_disabled", "logging_updated"):
            try:
                guild_id = int(guild_id) if guild_id else 0
            except (TypeError, ValueError):
                guild_id = 0
            await self._handle_module_config_push(event_type, guild_id, data)

        elif event_type == "settings_updated":
            # The dashboard wrote guilds.data.settings straight to the DB. The
            # server language is cached in-process, so drop the entry and
            # re-apply what the language is baked into (panels).
            await self._handle_settings_push(guild_id)

        elif event_type in ("premium_activated", "premium_deactivated"):
            # Guild premium is cached in Redis (utils.subscription.is_guild_premium);
            # drop the entry so the next gate check sees the new state.
            if guild_id:
                from utils.subscription import invalidate_guild_cache
                await invalidate_guild_cache(self, int(guild_id))
            logger.info(f"[PubSub] {event_type} for guild {guild_id}")

        elif event_type == "payment_failed":
            user_id = data.get("user_id")
            logger.warning(f"[PubSub] Payment failed for user {user_id}")

        elif event_type == "stripe_action_result":
            self.stripe_admin.handle_reply(data)

        else:
            logger.debug(f"[PubSub] Unknown event type: {event_type}")

    async def _handle_settings_push(self, guild_id) -> None:
        """Apply server settings the dashboard wrote straight to the database.

        Only the language lives there today (``guilds.data.settings.language``,
        see utils/guild_language.py). Dropping the cached value is what makes
        the next message read the new one; re-posting the panels is what makes
        the messages *already sitting in Discord* speak it.
        """
        from utils.guild_language import invalidate_guild_language

        try:
            guild_id = int(guild_id) if guild_id else 0
        except (TypeError, ValueError):
            guild_id = 0
        if not guild_id:
            logger.warning("[PubSub] settings_updated without a guild id — ignored")
            return

        invalidate_guild_language(guild_id)
        logger.info(f"[PubSub] Server settings invalidated for guild {guild_id}")

        if self.module_manager:
            try:
                await self.module_manager.apply_language_change(guild_id)
            except Exception as e:
                logger.error(f"[PubSub] Could not re-apply the panels of guild "
                             f"{guild_id} after a settings push: {e}", exc_info=True)

    async def _handle_module_config_push(self, event_type: str, guild_id: int, data: dict):
        """Apply a module configuration the backend/dashboard wrote to the DB.

        Two shapes are accepted on `moddy:bot`, and the difference matters:

        - **with** a `module_id`: the module is reloaded *and* asked to re-apply
          the visible half of its configuration (`on_external_config_change`) —
          for AltGuard, re-posting the verification panel, closing every channel
          to the unverified role and resyncing membership with the service. The
          recap is published back on `moddy:dashboard` so the dashboard can tell
          the admin whether the panel actually went out.
        - **without** a `module_id` (the historical payload): only the guild's
          module cache is dropped, so the next read picks the new values up. No
          Discord-side effect is applied — nothing here knows what changed.

        The `module_id` form is the one to use for anything with a panel; see
        docs/ALTGUARD_INTEGRATION.md.
        """
        import json
        from modules.module_manager import EXTERNAL_DELETED, EXTERNAL_UPDATED

        if not self.module_manager or not guild_id:
            logger.warning(
                f"[PubSub] Ignoring {event_type}: no module manager or no guild id"
            )
            return

        module_id = data.get("module_id")

        if not module_id:
            try:
                await self.module_manager.unload_guild_modules(guild_id)
                logger.info(
                    f"[PubSub] Module cache invalidated for guild {guild_id} ({event_type})"
                )
            except Exception as e:
                logger.error(f"[PubSub] Error reloading modules for guild {guild_id}: {e}")
            return

        # `deleted` is explicit; `module_disabled` means the same thing for a
        # module whose config was dropped, and reload_module re-checks the stored
        # config anyway (an empty one is always treated as a deletion).
        action = data.get("action")
        if action not in (EXTERNAL_UPDATED, EXTERNAL_DELETED):
            action = EXTERNAL_DELETED if event_type == "module_disabled" else EXTERNAL_UPDATED

        try:
            result = await self.module_manager.reload_module(
                guild_id, str(module_id), action=action,
            )
        except Exception as e:
            logger.error(
                f"[PubSub] Error applying pushed {module_id} config for guild "
                f"{guild_id}: {e}", exc_info=True,
            )
            result = {"ok": False, "error": "internal_error"}

        logger.info(
            f"[PubSub] Pushed config applied for {module_id} in guild {guild_id}: {result}"
        )

        if self.redis:
            try:
                await self.redis.publish("moddy:dashboard", json.dumps({
                    "type": "module_config_applied",
                    "request_id": data.get("request_id"),
                    "guild_id": guild_id,
                    "module_id": module_id,
                    **result,
                }))
            except Exception as e:
                logger.error(f"[PubSub] Could not publish module config result: {e}")

    async def _consume_task_stream(self):
        """Consume Redis Stream moddy:tasks (critical guaranteed tasks from backend).

        Every entry is HMAC-verified before it runs (see docs/TASK_SIGNATURE.md):
        anyone with write access to Redis could otherwise inject a task and have
        the bot execute it with its own permissions. A rejected entry is skipped
        and logged, never retried — otherwise an attacker fills the stream with
        invalid entries and blocks the consumer.
        """
        from config import TASK_STREAM_ALLOW_UNSIGNED, TASK_STREAM_SECRET
        from utils.task_signature import TaskRejected, verify_task

        TASK_STREAM = "moddy:tasks"
        LAST_ID_KEY = "moddy:tasks:last_id"

        while True:
            try:
                last_id = await self.redis.get(LAST_ID_KEY) or "0"
                while True:
                    messages = await self.redis.xread(
                        {TASK_STREAM: last_id},
                        block=5000,
                        count=10,
                    )
                    if not messages:
                        continue
                    for _stream, entries in messages:
                        for entry_id, fields in entries:
                            try:
                                await verify_task(
                                    fields,
                                    TASK_STREAM_SECRET,
                                    self.redis,
                                    allow_unsigned=TASK_STREAM_ALLOW_UNSIGNED,
                                )
                            except TaskRejected as e:
                                logger.warning(
                                    f"[Stream] Task {entry_id} rejected "
                                    f"({e.code}{': ' + e.detail if e.detail else ''}) "
                                    f"— type={fields.get('type')!r} "
                                    f"guild_id={fields.get('guild_id')!r}"
                                )
                            except Exception as e:
                                logger.error(
                                    f"[Stream] Could not verify task {entry_id}: {e}"
                                )
                            else:
                                try:
                                    await self._process_task(fields)
                                except Exception as e:
                                    logger.error(
                                        f"[Stream] Error processing task {entry_id}: {e}"
                                    )
                            # The resume point always advances: a rejected or
                            # failing entry must not be replayed forever.
                            last_id = entry_id
                            await self.redis.set(LAST_ID_KEY, last_id)
            except Exception as e:
                logger.error(f"[Stream] Connection error: {e}")
                await asyncio.sleep(5)

    async def _process_task(self, fields: dict):
        """Process a task from the moddy:tasks stream.

        The entry is already authenticated by `_consume_task_stream` (HMAC,
        freshness and anti-replay — see utils/task_signature.py). This method
        must never be called with an unverified entry.
        """
        import json

        task_type = fields.get("type")
        guild_id = int(fields.get("guild_id", 0))
        payload = json.loads(fields.get("payload", "{}"))

        if task_type == "update_panel":
            # Reload a module's config and re-apply its Discord side (panel,
            # channel overwrites). Same work as the `moddy:bot` push, over the
            # stream instead: use this one when the change must survive the bot
            # being down at the moment it is made — Pub/Sub drops it, the stream
            # replays it from `moddy:tasks:last_id`.
            await self._handle_module_config_push("module_updated", guild_id, {
                **payload, "guild_id": guild_id,
            })

        elif task_type == "send_announcement":
            message_text = payload.get("message", "")
            guild_ids = payload.get("guild_ids")
            targets = self.guilds if not guild_ids else [self.get_guild(gid) for gid in guild_ids]
            for guild in targets:
                if guild and guild.system_channel:
                    try:
                        await guild.system_channel.send(message_text)
                    except Exception as e:
                        logger.warning(f"[Stream] Could not send announcement to {guild.id}: {e}")

        elif task_type in ("social_subscribe", "social_unsubscribe", "social_remove", "social_update"):
            # Social Notifications: the backend delegates subscription actions to
            # the bot so the subscribe/DB logic lives in a single place.
            await self._process_social_task(task_type, guild_id, payload)

        elif task_type == "bot_customization_update":
            # Bot Customization: the dashboard cannot call Discord's
            # "modify current member" endpoint itself (it is the bot's own
            # profile), so it delegates the change to the bot.
            await self._process_bot_customization_task(guild_id, payload)

        elif task_type in ("case_add_sanction", "case_revoke_sanction"):
            # Moderation cases: the backend delegates guild sanctions to the
            # bot so the Discord action (ban/timeout) and the case DB write
            # stay in one place, exactly like a manual /ban /mute /warn.
            await self._process_case_task(task_type, guild_id, payload)

        else:
            logger.warning(f"[Stream] Unknown task type: {task_type}")

    async def _process_social_task(self, task_type: str, guild_id: int, payload: dict):
        """Run a Social Notifications action requested by the backend and
        publish the result back on the `moddy:dashboard` Pub/Sub channel,
        correlated by the optional `request_id` from the payload."""
        import json
        action = task_type.replace("social_", "")  # subscribe|unsubscribe|remove|update
        cog = self.get_cog("SocialNotifications")
        if not cog:
            result = {"ok": False, "error": "module_unavailable"}
        else:
            try:
                result = await cog.handle_backend_task(action, guild_id, payload)
            except Exception as e:
                logger.error(f"[Stream] Social task '{task_type}' failed: {e}", exc_info=True)
                result = {"ok": False, "error": "internal_error"}

        if self.redis:
            try:
                await self.redis.publish("moddy:dashboard", json.dumps({
                    "type": f"{task_type}_result",
                    "request_id": payload.get("request_id"),
                    "guild_id": guild_id,
                    **result,
                }))
            except Exception as e:
                logger.error(f"[Stream] Could not publish social task result: {e}")

    async def _process_bot_customization_task(self, guild_id: int, payload: dict):
        """Apply a Bot Customization change requested by the dashboard and
        publish the result back on `moddy:dashboard`, correlated by the
        optional `request_id` from the payload."""
        import json
        result: dict
        if not guild_id or not self.get_guild(guild_id):
            result = {"ok": False, "error": "guild_not_found"}
        else:
            try:
                from modules.bot_customization import (
                    MODULE_ID, BotCustomizationModule, CustomizationError,
                )
                module = await self.module_manager.get_module_instance(guild_id, MODULE_ID)
                if module is None:
                    # No stored configuration yet — build a bare instance so a
                    # first-time dashboard change still works.
                    module = BotCustomizationModule(self, guild_id)
                    await module.load_config(
                        await self.module_manager.get_module_config(guild_id, MODULE_ID) or {}
                    )
                result = await module.handle_backend_task(payload)
            except CustomizationError as e:
                result = {"ok": False, "error": e.code, "detail": e.detail}
            except Exception as e:
                logger.error(f"[Stream] Bot customization task failed: {e}", exc_info=True)
                result = {"ok": False, "error": "internal_error"}

        if self.redis:
            try:
                await self.redis.publish("moddy:dashboard", json.dumps({
                    "type": "bot_customization_update_result",
                    "request_id": payload.get("request_id"),
                    "guild_id": guild_id,
                    **result,
                }))
            except Exception as e:
                logger.error(f"[Stream] Could not publish bot customization result: {e}")

    async def _process_case_task(self, task_type: str, guild_id: int, payload: dict):
        """Run a guild-case sanction action requested by the backend dashboard
        and publish the result back on `moddy:dashboard`, correlated by the
        optional `request_id` from the payload."""
        import json
        action = "add_sanction" if task_type == "case_add_sanction" else "revoke_sanction"
        cog = self.get_cog("ModerationCommands")
        if not cog:
            result = {"ok": False, "error": "module_unavailable"}
        else:
            try:
                result = await cog.handle_backend_task(action, guild_id, payload)
            except Exception as e:
                logger.error(f"[Stream] Case task '{task_type}' failed: {e}", exc_info=True)
                result = {"ok": False, "error": "bot_error"}

        if self.redis:
            try:
                await self.redis.publish("moddy:dashboard", json.dumps({
                    "type": f"{task_type}_result",
                    "request_id": payload.get("request_id"),
                    "guild_id": guild_id,
                    **result,
                }, default=str))
            except Exception as e:
                logger.error(f"[Stream] Could not publish case task result: {e}")

    async def setup_hook(self):
        """Called once on bot startup"""
        logger.info("Initial setup...")

        # Fetch bot version from GitHub
        await self.fetch_version()
        self.heartbeat.version = self.version or "0.0.0"

        # Configure error handler for slash commands
        self.tree.on_error = self.on_app_command_error

        # Connect the database
        if DATABASE_URL:
            await self.setup_database()

        # Initialize i18n system
        logger.info("Loading i18n system...")
        i18n.load_translations()
        logger.info(f"i18n loaded with {len(i18n.supported_locales)} languages")

        # Attach the command translator: Discord then shows every slash command
        # name/description in the user's own language. Translations are only
        # applied when the tree is synced, so this must run before sync_commands().
        logger.info("Loading slash command localizations...")
        self.command_translator = ModdyCommandTranslator()
        await self.tree.set_translator(self.command_translator)
        logger.info(
            f"Command localizations ready ({len(self.command_translator.supported_locales)} locales)"
        )

        # Initialize staff permissions system
        logger.info("Initializing staff permissions system...")
        setup_staff_permissions(self)
        logger.info("Staff permissions system ready")

        # Initialize staff logger
        logger.info("Initializing staff logger...")
        init_staff_logger(self)
        logger.info("Staff logger ready")

        # Initialize technical logger (webhook-based internal staff logs)
        logger.info("Initializing technical logger...")
        from utils.tech_logger import init_tech_logger
        init_tech_logger(self)
        # Wire DB write hooks so important writes are logged to the webhooks.
        if self.db:
            self.db.on_attribute_change = self.tech_logger.log_attribute_change
            self.db.on_data_change = self.tech_logger.log_data_change
        logger.info("Technical logger ready")

        # Initialize module manager
        logger.info("Initializing module manager...")
        self.module_manager = ModuleManager(self)
        self.module_manager.discover_modules()
        logger.info("Module manager ready")

        # Start internal API server
        logger.info("Starting internal API server...")
        self.start_internal_api_server()

        # Set start time for /status uptime metric
        import time as _time
        self._start_time = _time.time()

        # Connect to Redis
        if REDIS_URL:
            await self._setup_redis()
        else:
            logger.warning("[WARN] REDIS_URL not set - Redis features disabled")

        # Initialize API gateway (requires Redis + DB pool)
        logger.info("Starting API gateway...")
        try:
            await self.gateway.start(
                redis=self.redis,
                pool=self.db.pool if self.db else None,
                tech_logger=getattr(self, "tech_logger", None),
            )
            logger.info("API gateway ready")
        except Exception as e:
            logger.error(f"[FAIL] API gateway startup error: {e}")

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

        # Register persistent views (must run AFTER cogs are loaded so that
        # every view class is importable). See docs/PERSISTENT_VIEWS.md.
        from utils.persistent_views import register_all_persistent_views
        register_all_persistent_views(self)

        # Start background tasks
        self.status_update.start()
        self.case_expiry.start()
        self.enforcement_sweep.start()

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

    async def get_official_guild_ids(self) -> set:
        """Return the set of guild ids flagged with the OFFICIAL attribute.

        Staff slash commands (/dev, /team, ...) are synced to these guilds only.
        Toggle a guild's status with the ``official`` staff command.
        """
        if not self.db:
            return set()
        try:
            ids = await self.db.get_guilds_with_attribute("OFFICIAL")
            return set(ids)
        except Exception as e:
            logger.error(f"[FAIL] Could not fetch OFFICIAL guilds: {e}")
            return set()

    def register_module_commands(self, module_id: str, commands_list: list) -> None:
        """Publish these commands only in guilds where ``module_id`` is enabled.

        A cog calls this at load time with commands it deliberately never added
        to the global tree (declare them at module level, not as a Cog
        attribute, or discord.py registers them globally for you). They are then
        added to a guild's tree by :meth:`_register_guild_command_set` and
        removed again the moment the module is switched off, so a server that
        does not use a module never sees its commands at all.
        """
        self.module_slash_commands[module_id] = list(commands_list)
        logger.info(f"Module commands registered for '{module_id}': "
                    f"{[c.name for c in commands_list]}")

    async def get_enabled_module_ids(self, guild_id: int) -> Set[str]:
        """Modules currently enabled in a guild, restricted to those with commands.

        Only modules that actually own commands are looked at: the answer feeds
        the command tree and nothing else, so there is no reason to pay for the
        rest.
        """
        if not self.module_manager or not self.module_slash_commands:
            return set()

        enabled = set()
        for module_id in self.module_slash_commands:
            try:
                module = await self.module_manager.get_module_instance(guild_id, module_id)
            except Exception as e:
                logger.error(f"[FAIL] Could not read module {module_id} for guild "
                             f"{guild_id}: {e}")
                continue
            if module and module.enabled:
                enabled.add(module_id)
        return enabled

    def _register_guild_command_set(self, guild: discord.Guild, official_ids: set,
                                    module_ids: Optional[Set[str]] = None):
        """(Re)build the per-guild command tree: guild-only commands for every
        guild, the commands of the modules this guild has enabled, plus the
        staff slash groups for OFFICIAL guilds only."""
        self.tree.clear_commands(guild=guild)

        # Guild-only commands (e.g. /config) for every server with Moddy.
        for command in self._guild_only_commands:
            self.tree.add_command(command, guild=guild)

        # Commands owned by an enabled module (e.g. /ticket).
        for module_id in sorted(module_ids or ()):
            for command in self.module_slash_commands.get(module_id, []):
                self.tree.add_command(command, guild=guild)

        # Staff command groups only on OFFICIAL servers.
        if guild.id in official_ids:
            for group in (self.staff_slash_groups or []):
                self.tree.add_command(group, guild=guild)

    async def resync_module_commands(self, guild_id: int) -> bool:
        """Re-sync a guild's commands if its enabled-module set just changed.

        Called after any module configuration change (``/config``, the
        dashboard, a deletion). Returns True when a sync was actually sent —
        which only happens when the set of modules owning commands changed,
        because guild command syncs are rate-limited and a save that toggles a
        colour must not cost one.
        """
        guild = self.get_guild(guild_id)
        if guild is None:
            logger.warning(f"[WARN] Cannot re-sync module commands: guild "
                           f"{guild_id} is not in the cache")
            return False
        if not self.module_slash_commands:
            # Nothing ever called register_module_commands — almost always a
            # cog that failed to load. Silence here means a module works in
            # /config while its commands never appear, with nothing to link
            # the two, so say it out loud.
            logger.warning(
                f"[WARN] No module-gated commands are registered, so none can "
                f"be published in guild {guild_id}. Check the startup log for "
                f"a '[FAIL] Cog error' line."
            )
            return False

        module_ids = await self.get_enabled_module_ids(guild_id)
        if self._guild_module_commands.get(guild_id) == frozenset(module_ids):
            logger.debug(f"Module commands unchanged for guild {guild_id} "
                         f"({sorted(module_ids) or 'none'}) — no sync sent")
            return False

        try:
            official_ids = await self.get_official_guild_ids()
            self._register_guild_command_set(guild, official_ids, module_ids)
            await self.tree.sync(guild=guild)
            self._guild_module_commands[guild_id] = frozenset(module_ids)
            logger.info(f"Module commands re-synced for {guild.name} ({guild_id}): "
                        f"{sorted(module_ids) or 'none'}")
            return True
        except discord.Forbidden:
            logger.warning(f"[WARN] Cannot sync commands for guild {guild_id} "
                           f"(missing applications.commands scope)")
        except Exception as e:
            logger.error(f"[FAIL] Error re-syncing module commands for guild "
                         f"{guild_id}: {e}")
        return False

    async def sync_all_guild_commands(self):
        """
        Synchronise les commandes guild-only pour TOUS les serveurs, et les
        commandes staff (/dev, /team, ...) pour les serveurs OFFICIELS uniquement.
        Appelé dans on_ready() quand self.guilds est disponible.

        IMPORTANT: Ne PAS copier les commandes globales avec copy_global_to()
        car cela ferait que Discord ignore les commandes globales pour ce serveur.
        Les commandes globales sont déjà synchronisées globalement et disponibles partout.
        """
        try:
            official_ids = await self.get_official_guild_ids()

            # One greppable line saying which modules can publish commands at
            # all. Empty when a cog failed to load, which otherwise shows up
            # only as "the command never appeared".
            if self.module_slash_commands:
                logger.info(
                    "Module-gated commands available: "
                    + ", ".join(
                        f"{mid} -> {[c.name for c in cmds]}"
                        for mid, cmds in sorted(self.module_slash_commands.items())
                    )
                )
            else:
                logger.info("No module-gated commands registered")

            # Synchroniser les commandes guild-only dans chaque serveur
            guild_count = 0
            for guild in self.guilds:
                try:
                    # IMPORTANT: Clear d'abord toutes les commandes de ce serveur
                    # puis ajoute guild-only (+ commandes des modules activés,
                    # + staff groups si serveur officiel).
                    module_ids = await self.get_enabled_module_ids(guild.id)
                    self._register_guild_command_set(guild, official_ids, module_ids)

                    # Sync les commandes de ce serveur (ou sync vide si rien)
                    await self.tree.sync(guild=guild)
                    self._guild_module_commands[guild.id] = frozenset(module_ids)

                    guild_count += 1
                    logger.info(f"Guild commands synced for {guild.name} "
                                f"({guild.id}) — modules: "
                                f"{sorted(module_ids) or 'none'}")
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
            # Clear puis ajoute guild-only (+ commandes des modules activés,
            # + staff groups si serveur officiel).
            official_ids = await self.get_official_guild_ids()
            module_ids = await self.get_enabled_module_ids(guild.id)
            self._register_guild_command_set(guild, official_ids, module_ids)

            # Synchroniser les commandes pour ce serveur (ou sync vide si rien)
            await self.tree.sync(guild=guild)
            self._guild_module_commands[guild.id] = frozenset(module_ids)

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

            # Global sanctions live in the cases system now. Convert whatever
            # is left of the legacy BLACKLISTED attribute into a global ban
            # case, then drop the attribute (idempotent, no-op once done).
            try:
                await self.db.migrate_legacy_blacklist_attributes()
            except Exception as e:
                logger.error(f"[FAIL] Legacy blacklist migration error: {e}")

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
                        # Linked roles: only when something actually changed —
                        # republishing every boot would be pure noise.
                        from services.staff_events import notify_staff_change, EVENT_RANKED
                        await notify_staff_change(self, dev_id, event=EVENT_RANKED, roles=roles)
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

        # Start the Health Monitor heartbeat. Only from here on: an event
        # loop with a dead gateway connection has nothing worth reporting.
        self.heartbeat.start()
        self.betterstack_heartbeat.start()

    async def _is_bot_healthy(self) -> bool:
        """Whether the bot is healthy enough to ping the Better Stack
        heartbeat as a success — reuses the same down/degraded/ok verdict as
        the Moddy Health Monitor checks so the two never disagree."""
        checks = await self._build_heartbeat_checks()
        return checks["status"] == "ok"

    async def _build_heartbeat_checks(self) -> dict:
        """Build the ``checks``/``status`` the heartbeat reports for this bot.

        An event loop that is alive but whose gateway connection is dead must
        never report ``ok`` — that is the exact failure mode a dead man's
        switch exists to catch. ``bot.latency`` is ``nan`` until the gateway
        has answered at least once.
        """
        ready = bool(self.is_ready())
        latency_ms = round(self.latency * 1000) if math.isfinite(self.latency) else None

        shards = getattr(self, "shards", None) or {}
        if shards:
            total = len(shards)
            connected = len([s for s in shards.values() if not s.is_closed()])
        else:
            # Not sharded: one virtual shard, up iff the gateway is ready.
            total = 1
            connected = int(ready)

        if not ready:
            status = "down"
        elif connected < total:
            status = "degraded"
        else:
            status = "ok"

        return {
            "status": status,
            "checks": {
                "is_ready": {"ok": ready},
                "discord_gateway": {"ok": ready, "latency_ms": latency_ms},
                "shards": {"ok": connected == total, "connected": connected, "total": total},
            },
            "meta": {
                "shards": f"{connected}/{total}",
                "guilds": len(self.guilds),
            },
        }

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

        # 5. Redis connection
        if self.redis:
            try:
                await self.redis.ping()
                results.append(("Redis", True, "Connected"))
            except Exception as e:
                results.append(("Redis", False, str(e)))
        else:
            results.append(("Redis", False, "Not configured"))

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

        # 7. Technical log: bot startup health report (webhook)
        if getattr(self, "tech_logger", None):
            import time as _time
            boot_seconds = None
            if self._start_time:
                boot_seconds = _time.time() - self._start_time
            await self.tech_logger.log_startup(
                results,
                version=self.version,
                latency_ms=round(self.latency * 1000) if self.latency else None,
                guild_count=len(self.guilds),
                user_count=len(self.users),
                boot_seconds=boot_seconds,
            )

    async def apply_name_style(self, guild_id: int) -> bool:
        """Apply Moddy's branded display name font/effect/color to its own member profile in a guild"""
        route = discord.http.Route(
            "PATCH", "/guilds/{guild_id}/members/@me", guild_id=guild_id
        )
        try:
            await self.http.request(route, json={
                "display_name_font_id": NAME_STYLE_FONT_ID,
                "display_name_effect_id": NAME_STYLE_EFFECT_ID,
                "display_name_colors": NAME_STYLE_COLORS,
            })
            return True
        except discord.Forbidden:
            logger.warning(f"[WARN] name_style: missing Change Nickname permission in {guild_id}")
        except discord.HTTPException as e:
            logger.warning(f"[WARN] name_style: failed on {guild_id} — {e}")
        return False

    async def _resolve_inviter(self, guild: discord.Guild) -> Optional[discord.User]:
        """Who actually added Moddy to this server.

        Read from the audit log, which is the only place Discord exposes it —
        best-effort: the entry may not be written yet, and Moddy may not have
        View Audit Log on a brand-new server. Returns ``None`` when unknown.
        """
        try:
            async for entry in guild.audit_logs(
                limit=5, action=discord.AuditLogAction.bot_add
            ):
                target_id = getattr(entry.target, "id", None)
                if target_id == self.user.id:
                    return entry.user
        except (discord.Forbidden, discord.HTTPException, AttributeError):
            pass
        return None

    async def _check_join_allowed(self, guild: discord.Guild) -> Optional[dict]:
        """Decide whether Moddy may stay in a server it was just added to.

        Resolves the three levels the policy needs and hands them to
        ``global_sanctions.decide_join_refusal``, which holds the rule itself.

        Returns ``None`` when the join is fine, otherwise a dict describing the
        refusal: ``{"reason", "level", "guild_suspended", "notify_user"}``.
        """
        guild_level = await global_sanctions.get_guild_level(self, guild.id)
        owner_level = await global_sanctions.get_user_level(self, guild.owner_id)

        # Who actually clicked "Add to server" — unknown on a server whose
        # audit log Moddy cannot read, in which case only the owner is checked.
        inviter = await self._resolve_inviter(guild)
        if inviter is not None and inviter.id == guild.owner_id:
            inviter = None
        inviter_level = (
            await global_sanctions.get_user_level(self, inviter.id)
            if inviter is not None else None
        )

        reason = global_sanctions.decide_join_refusal(
            guild_level=guild_level,
            owner_level=owner_level,
            inviter_level=inviter_level,
        )
        if reason is None:
            return None

        level, notify_user = {
            "guild": (guild_level, guild.owner),
            "owner": (owner_level, guild.owner),
            "inviter": (inviter_level, inviter),
        }[reason]

        return {
            "reason": reason,
            "level": level,
            "guild_suspended": reason == "guild",
            "notify_user": notify_user,
        }

    async def _refuse_guild_join(self, guild: discord.Guild, refusal: dict) -> None:
        """Leave a server Moddy may not stay in, and tell the right person why."""
        reason = refusal["reason"]
        level = refusal["level"]
        logger.warning(
            f"[WARN] Add attempt blocked — {reason} is `{level.value}` "
            f"(guild {guild.id}, owner {guild.owner_id})"
        )

        # Tell whoever caused the refusal, if their DMs allow it.
        target = refusal.get("notify_user")
        if target is not None:
            try:
                from utils.global_sanction_views import build_guild_join_refusal
                await target.send(view=build_guild_join_refusal(
                    level=level,
                    guild_name=guild.name,
                    guild_id=guild.id,
                    guild_suspended=refusal["guild_suspended"],
                ))
            except Exception:
                pass

        await guild.leave()

        if log_cog := self.get_cog("LoggingSystem"):
            try:
                await log_cog.log_critical(
                    title="Join Blocked - Global Sanction",
                    description=(
                        f"**Server:** {guild.name} (`{guild.id}`)\n"
                        f"**Owner:** {guild.owner} (`{guild.owner_id}`)\n"
                        f"**Members:** {guild.member_count}\n"
                        f"**Sanctioned:** {reason} (`{level.value}`)\n"
                        f"**Action:** Bot left automatically"
                    ),
                    ping_dev=False
                )
            except Exception:
                pass

        # Technical log (security feed)
        if getattr(self, "tech_logger", None):
            try:
                await self.tech_logger.log_security(
                    "Join Blocked — Global Sanction",
                    [
                        f"**Guild** `{guild.name}` `{guild.id}`",
                        f"**Owner** `{guild.owner}` `{guild.owner_id}`",
                        f"**Members** `{guild.member_count or 0}`",
                        f"**Sanctioned** `{reason}` `{level.value}`",
                        f"**Action** `bot left automatically`",
                    ],
                )
            except Exception:
                pass

    async def on_guild_join(self, guild: discord.Guild):
        """When the bot joins a server"""
        logger.info(f"New server: {guild.name} ({guild.id})")

        # Refuse the join when a global sanction says Moddy has no business
        # being here (see _check_join_allowed).
        if self.db:
            try:
                refusal = await self._check_join_allowed(guild)
                if refusal is not None:
                    await self._refuse_guild_join(guild, refusal)
                    return

                # Nothing blocking — continue normally.
                # Create the server entry in the guilds table
                await self.db.get_guild(guild.id)  # This creates the entry if it doesn't exist

            except Exception as e:
                logger.error(f"DB Error (guild_join): {e}")

        # Invalidate backend Redis cache (so dashboard reflects new guild list)
        if self.redis:
            try:
                await self.redis.delete("moddy:bot_guilds")
            except Exception as e:
                logger.warning(f"[WARN] Could not invalidate Redis cache on guild join: {e}")

        # Technical log: bot added to a server
        if getattr(self, "tech_logger", None):
            await self.tech_logger.log_guild_join(guild)

        # Synchronize commands for this new guild
        # This ensures guild-only commands (/config) are available in this server
        try:
            await self.sync_guild_commands(guild)
            logger.info(f"Commands synchronized for new guild {guild.name} ({guild.id})")
        except Exception as e:
            logger.error(f"[FAIL] Error syncing commands for new guild {guild.id}: {e}")

        # Apply Moddy's branded name style (font/effect/color) to its own profile
        try:
            await self.apply_name_style(guild.id)
        except Exception as e:
            logger.error(f"[FAIL] Error applying name style for new guild {guild.id}: {e}")

        # Welcome the person who just installed Moddy (a DM to them, not a
        # card in a channel they may never read) — utils/install_welcome.py.
        try:
            from utils.install_welcome import send_install_welcome
            await send_install_welcome(self, guild)
        except Exception as e:
            logger.error(f"[FAIL] Error sending the install welcome for guild {guild.id}: {e}")

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

        # Technical log: bot removed from a server
        if getattr(self, "tech_logger", None):
            await self.tech_logger.log_guild_remove(guild)

        # Invalidate backend Redis cache
        if self.redis:
            try:
                await self.redis.delete(
                    "moddy:bot_guilds",
                    f"discord:guild:{guild.id}:info",
                    f"discord:guild:{guild.id}:channels",
                    f"discord:guild:{guild.id}:roles",
                )
            except Exception as e:
                logger.warning(f"[WARN] Could not invalidate Redis cache on guild remove: {e}")

        # Clear commands for this guild to remove guild-only commands
        # This ensures /config is no longer accessible in this server
        try:
            self.tree.clear_commands(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info(f"Commands cleared for guild {guild.name} ({guild.id})")
        except discord.Forbidden:
            # Bot was kicked — no longer has access to sync commands, this is expected
            logger.debug(f"[SKIP] Cannot clear commands for guild {guild.id}: bot no longer has access (kicked)")
        except Exception as e:
            logger.error(f"[FAIL] Error clearing commands for guild {guild.id}: {e}")

    async def _global_sanction_check(self, interaction: discord.Interaction) -> bool:
        """
        Check global pour toutes les app commands (slash commands).
        Appelé automatiquement par discord.py AVANT l'exécution de toute app command.
        Retourne False ou lève une exception pour bloquer l'exécution.

        Une suspension globale (case ``global`` + sanction ``ban``) sur
        l'utilisateur **ou** sur le serveur coupe tout accès à Moddy — sauf
        les commandes d'information listées dans
        ``global_sanctions.SUSPENDED_ALLOWED_COMMANDS`` (``/mycases``,
        ``/moddy``…) : un suspendu doit pouvoir lire ses cases, comprendre la
        sanction et faire appel.
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
            command_name = interaction.command.qualified_name if interaction.command else None
            if global_sanctions.is_command_allowed_when_suspended(command_name):
                # Informational commands stay reachable under a suspension.
                user_suspended = guild_suspended = False
            else:
                user_suspended = await global_sanctions.is_suspended(
                    self, user_id=interaction.user.id)
                guild_suspended = (
                    not user_suspended
                    and interaction.guild_id is not None
                    and await global_sanctions.is_suspended(self, guild_id=interaction.guild_id)
                )

            if user_suspended or guild_suspended:
                # Components V2 panel explaining the suspension.
                view = create_suspension_message(
                    str(interaction.locale) if interaction.locale else 'en-US',
                    guild=guild_suspended,
                )

                # Répond à l'interaction
                try:
                    await interaction.response.send_message(
                        view=view,
                        ephemeral=True
                    )
                except Exception as e:
                    logger.error(f"Error sending suspension message: {e}")

                # Log l'interaction bloquée
                if log_cog := self.get_cog("LoggingSystem"):
                    try:
                        await log_cog.log_critical(
                            title="🚫 SLASH COMMAND BLOQUÉE (SUSPENSION GLOBALE)",
                            description=(
                                f"**Utilisateur:** {interaction.user.mention} (`{interaction.user.id}`)\n"
                                f"**Commande:** {interaction.command.name if interaction.command else 'N/A'}\n"
                                f"**Serveur:** {interaction.guild.name if interaction.guild else 'DM'}\n"
                                f"**Suspendu:** {'serveur' if guild_suspended else 'utilisateur'}\n"
                                f"**Action:** ✋ BLOQUÉE AVANT EXÉCUTION (tree.interaction_check)"
                            ),
                            ping_dev=False
                        )
                    except Exception as e:
                        logger.error(f"Error logging suspension: {e}")

                # Retourne False pour bloquer l'exécution
                return False

        except Exception as e:
            logger.error(f"Error checking global sanctions in _global_sanction_check: {e}")

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

        return True  # Autorise si pas suspendu ou en cas d'erreur

    async def _check_suspension_and_respond(self, interaction: discord.Interaction) -> bool:
        """
        Vérifie si l'utilisateur (ou son serveur) est suspendu globalement et
        répond si c'est le cas.
        Retourne True si l'interaction est bloquée, False sinon.

        Les composants d'appel (``moddy:apl:*``), le navigateur ``/mycases`` et
        le panneau ``/moddy`` restent cliquables : sans eux, un suspendu ne
        pourrait pas contester sa sanction.
        """
        if not self.db or interaction.user.bot:
            return False

        try:
            custom_id = (interaction.data or {}).get("custom_id") if hasattr(interaction, "data") else None
            if global_sanctions.is_component_allowed_when_suspended(custom_id):
                return False

            user_suspended = await global_sanctions.is_suspended(
                self, user_id=interaction.user.id)
            guild_suspended = (
                not user_suspended
                and interaction.guild_id is not None
                and await global_sanctions.is_suspended(self, guild_id=interaction.guild_id)
            )

            if user_suspended or guild_suspended:
                # Components V2 panel explaining the suspension.
                view = create_suspension_message(
                    str(interaction.locale) if interaction.locale else 'en-US',
                    guild=guild_suspended,
                )

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
                    logger.error(f"Error sending suspension message: {e}")

                # Log l'interaction bloquée
                if log_cog := self.get_cog("LoggingSystem"):
                    try:
                        interaction_type = interaction.type.name
                        if interaction.type == discord.InteractionType.application_command:
                            identifier = f"Commande: {interaction.command.name if interaction.command else 'N/A'}"
                        else:
                            identifier = f"Custom ID: {interaction.data.get('custom_id', 'N/A') if hasattr(interaction, 'data') else 'N/A'}"

                        await log_cog.log_critical(
                            title="🚫 INTERACTION BLOQUÉE (SUSPENSION GLOBALE)",
                            description=(
                                f"**Utilisateur:** {interaction.user.mention} (`{interaction.user.id}`)\n"
                                f"**Type:** {interaction_type}\n"
                                f"**{identifier}**\n"
                                f"**Serveur:** {interaction.guild.name if interaction.guild else 'DM'}\n"
                                f"**Suspendu:** {'serveur' if guild_suspended else 'utilisateur'}\n"
                                f"**Action:** ✋ BLOQUÉE AVANT TRAITEMENT"
                            ),
                            ping_dev=False
                        )
                    except Exception as e:
                        logger.error(f"Error logging suspension: {e}")

                return True  # Suspendu

        except Exception as e:
            logger.error(f"Error checking global sanctions: {e}")

        return False  # Pas suspendu

    async def on_interaction(self, interaction: discord.Interaction):
        """
        INTERCEPTION pour les composants (boutons, selects, modals).
        Les slash commands sont gérées par _global_sanction_check via tree.interaction_check.
        """
        # Les app commands sont déjà gérées par _global_sanction_check
        if interaction.type == discord.InteractionType.application_command:
            return

        # Pour les composants (boutons, selects, modals), vérifie la suspension
        if await self._check_suspension_and_respond(interaction):
            # Le sujet est suspendu, le message a été envoyé
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

    @tasks.loop(minutes=2)
    async def case_expiry(self):
        """Expire temporary moderation sanctions whose deadline has passed.

        Flips each due sanction to ``expired``, logs the timeline event and
        recomputes the parent case status (see db/repositories/moderation.py),
        then hands the expired rows to ``self.expirations`` so the Discord
        action is reversed and the subject is notified.
        """
        if not self.db:
            return
        try:
            expired = await self.db.expire_due_sanctions()
        except Exception as e:
            logger.error(f"Error expiring moderation sanctions: {e}", exc_info=True)
            return

        # Reverse the Discord side of each expired sanction (a temporary ban has
        # to be lifted explicitly; a timeout is auto-cleared by Discord) and DM
        # the subject that it is over — with an invite back when it was a ban.
        # See services/expiration_notifier.py.
        await self.expirations.process(expired)

    @case_expiry.before_loop
    async def before_case_expiry(self):
        await self.wait_until_ready()

    @tasks.loop(minutes=5)
    async def enforcement_sweep(self):
        """Run the deferred consequences of global sanctions whose grace period
        has elapsed without an appeal.

        The subject was given 48h in their notice DM to appeal; past that,
        Moddy leaves the suspended servers and the backend is told to cancel
        the subscription and purge what it must (see
        ``services/global_sanction_service.py``).
        """
        if not self.db:
            return
        try:
            executed = await self.global_sanctions.run_due()
            if executed:
                logger.info(f"[Enforcement] Executed {executed} global sanction group(s)")
        except Exception as e:
            logger.error(f"Error sweeping global sanction enforcements: {e}", exc_info=True)

    @enforcement_sweep.before_loop
    async def before_enforcement_sweep(self):
        await self.wait_until_ready()

    async def close(self):
        """Cleanly closing the bot"""
        logger.info("Shutting down...")

        # Technical log: bot shutting down (best-effort, before sessions close)
        if getattr(self, "tech_logger", None):
            try:
                await self.tech_logger.log_shutdown()
                await self.tech_logger.close()
            except Exception as e:
                logger.error(f"[FAIL] Error sending shutdown log: {e}")

        # Stop tasks BEFORE closing
        if self.status_update.is_running():
            self.status_update.cancel()
        if self.case_expiry.is_running():
            self.case_expiry.cancel()
        if self.enforcement_sweep.is_running():
            self.enforcement_sweep.cancel()

        # Wait a bit for tasks to finish
        await asyncio.sleep(0.1)

        # Stop API gateway (flushes log buffer)
        await self.gateway.stop()

        # Stop the Health Monitor heartbeat
        await self.heartbeat.stop()
        await self.betterstack_heartbeat.stop()

        # Close the AltGuard HTTP session
        try:
            await self.altguard.close()
        except Exception as e:
            logger.error(f"[FAIL] Error closing AltGuard client: {e}")

        # Close Redis connection
        if self.redis:
            try:
                await self.redis.aclose()
                logger.info("Redis closed")
            except Exception as e:
                logger.error(f"[FAIL] Error closing Redis: {e}")

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
