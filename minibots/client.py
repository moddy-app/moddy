"""
One mini-bot: a deliberately lean ``commands.Bot``.

Everything a mini-bot does not need is switched off, because each client's
cache is paid once per bot:

- intents: ``guilds`` + ``guild_messages`` (staff commands by mention — the
  content of a message mentioning the bot is delivered without the privileged
  intent) + only what its spec declares; never members/presences;
- no message cache (``max_messages=None``), no member cache, no chunking;
- modules imported only when the bot starts, and loaded per guild lazily.

It exposes what the shared code reads on ``bot`` (``db``, ``redis``,
``module_manager``, ``tech_logger``, ``notifications``, ``stats``,
``is_developer``, ``launch_time``), so Moddy's modules, /config screens and
staff commands run unchanged. Built-ins (``/config``, ``/about``, welcome DM)
come from :mod:`minibots.builtin`, staff commands from :mod:`minibots.staff`.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import inspect
import logging
import sys
import traceback
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

import discord
from discord.ext import commands

from minibots.ownership import can_claim
from minibots.spec import MiniBotSpec

if TYPE_CHECKING:
    from minibots.resources import SharedResources
    from minibots.runtime import MiniBotRuntime

logger = logging.getLogger("moddy.minibots")

#: A prefix no message ever starts with: mini-bots have no prefix commands
#: (staff commands are routed by the staff cog, on the bot's mention).
_NO_PREFIX = "\x00minibot\x00"


def build_intents(spec: MiniBotSpec) -> discord.Intents:
    intents = discord.Intents.none()
    intents.guilds = True
    intents.guild_messages = True
    for name in spec.intents:
        setattr(intents, name, True)
    return intents


class MiniBotClient(commands.Bot):
    def __init__(self, spec: MiniBotSpec, resources: "SharedResources",
                 runtime: Optional["MiniBotRuntime"] = None):
        super().__init__(
            command_prefix=_NO_PREFIX,
            help_command=None,
            intents=build_intents(spec),
            max_messages=None,
            member_cache_flags=discord.MemberCacheFlags.none(),
            chunk_guilds_at_startup=False,
            enable_debug_events=False,
            activity=discord.CustomActivity(name=spec.activity) if spec.activity else None,
        )
        self.spec = spec
        self.app_id = spec.id
        self.runtime = runtime
        self.resources = resources
        self.launch_time = datetime.now(timezone.utc)
        # What the shared modules / screens / staff commands read on ``bot``.
        self.db = resources.db
        self.redis = resources.redis
        self.stats = None
        self.module_slash_commands: dict = {}
        tech = getattr(resources, "tech_logger", None)
        self.tech_logger = tech.for_app(self, spec.id, spec.name) if tech is not None else None
        self.staff_logger = None
        from notifications.service import NotificationService
        self.notifications = NotificationService(self)
        state = runtime.apps.get(spec.id) if runtime is not None else None
        self.emoji_map = state.emoji_map if state is not None else {}
        # Set once the emoji copy finished (minibots/emojis.py): a message sent
        # before would show Moddy's ids as text. The welcome DM waits for it.
        self.emojis_ready = asyncio.Event()

        from modules.module_manager import ModuleManager
        self.module_manager = ModuleManager(self, app=spec.id)
        self.module_manager.default_gate = self._default_gate
        self._first_ready_done = False

    def is_developer(self, user_id: int) -> bool:  # replaced by minibots.staff.setup
        return False

    @contextlib.contextmanager
    def emoji_context(self):
        """Send with this app's emoji ids and name from code running outside
        its task (control requests, periodic jobs)."""
        from minibots.branding import _brand, _standalone
        from minibots.emojis import _app_map
        token = _app_map.set(self.emoji_map)
        brand = _brand.set(self.spec.name)
        single = _standalone.set(self.spec.modules[0] if len(self.spec.modules) == 1 else None)
        try:
            yield
        finally:
            _standalone.reset(single)
            _brand.reset(brand)
            _app_map.reset(token)

    # ------------------------------------------------------------ lifecycle

    async def setup_hook(self) -> None:
        from utils.command_translator import ModdyCommandTranslator
        from minibots import builtin, staff

        self.tree.on_error = self._on_tree_error
        await self.tree.set_translator(ModdyCommandTranslator())
        self._register_modules()
        await builtin.setup(self)
        await staff.setup(self)
        if self.spec.commands_module:
            module = importlib.import_module(self.spec.commands_module)
            setup = getattr(module, "setup", None)
            if setup is not None:
                await setup(self)

    def _register_modules(self) -> None:
        """Import the spec's module files and register only whitelisted ids."""
        from modules.module_manager import ModuleBase
        wanted = set(self.spec.modules)
        for path in self.spec.module_paths:
            module = importlib.import_module(path)
            for _, obj in inspect.getmembers(module, inspect.isclass):
                if (issubclass(obj, ModuleBase) and obj is not ModuleBase
                        and obj.MODULE_ID in wanted):
                    self.module_manager.register_module(obj)
        missing = wanted - set(self.module_manager.registered_modules)
        if missing:
            logger.warning("Mini-bot %s: modules not found: %s", self.app_id, sorted(missing))

    async def on_ready(self) -> None:
        if self.db is not None:
            try:
                await self.db.reconcile_app_guilds(self.app_id, [g.id for g in self.guilds])
            except Exception as exc:  # noqa: BLE001
                logger.error("Mini-bot %s: presence reconcile failed: %s", self.app_id, exc)
        if not self._first_ready_done:
            self._first_ready_done = True
            from minibots import builtin, staff
            from minibots.emojis import sync_application_emojis
            await staff.learn_developers(self)
            asyncio.create_task(self._sync_emojis(sync_application_emojis))
            await builtin.sync_commands(self)
        if self.runtime is not None:
            await self.runtime.on_client_ready(self)

    async def _sync_emojis(self, sync) -> None:
        try:
            stats = await sync(self, self.emoji_map)
        finally:
            self.emojis_ready.set()
        if stats.get("uploaded") or stats.get("replaced") or stats.get("failed"):
            state = self.runtime.apps.get(self.app_id) if self.runtime else None
            if state is not None:
                await self.runtime._lifecycle(state, "emojis_synced", [
                    f"**Uploaded** `{stats['uploaded']}` • **Replaced** `{stats['replaced']}` "
                    f"• **Failed** `{stats['failed']}` "
                    f"• **Already there** `{stats['present']}`"])

    async def on_guild_join(self, guild: discord.Guild) -> None:
        from minibots import builtin
        if self.db is not None:
            try:
                await self.db.mark_app_present(self.app_id, guild.id, guild.member_count)
            except Exception as exc:  # noqa: BLE001
                logger.error("Mini-bot %s: presence write failed: %s", self.app_id, exc)
        if self.tech_logger is not None:
            await self.tech_logger.log_guild_join(guild)
        await builtin.send_welcome(self, guild)

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        if self.db is not None:
            try:
                await self.db.mark_app_absent(self.app_id, guild.id)
            except Exception as exc:  # noqa: BLE001
                logger.error("Mini-bot %s: presence write failed: %s", self.app_id, exc)
        await self.module_manager.unload_guild_modules(guild.id)
        if self.tech_logger is not None:
            await self.tech_logger.log_guild_remove(guild)

    async def _on_tree_error(self, interaction: discord.Interaction, error) -> None:
        tracker = self.get_cog("ErrorTracker")
        if tracker is not None and hasattr(tracker, "on_app_command_error"):
            await tracker.on_app_command_error(interaction, error)
        else:  # pragma: no cover - the tracker is always loaded
            logger.error("Mini-bot %s: app command error: %s", self.app_id, error)

    async def on_error(self, event_method: str, *args, **kwargs) -> None:
        exc_type, exc, tb = sys.exc_info()
        logger.error("Mini-bot %s: error in %s", self.app_id, event_method, exc_info=True)
        if self.tech_logger is None or exc is None:
            return
        frame = traceback.extract_tb(tb)[-1] if tb else None
        await self.tech_logger.log_error("MINIBOT", {
            "type": getattr(exc_type, "__name__", "?"),
            "message": str(exc),
            "file": frame.filename.rsplit("/", 1)[-1] if frame else "?",
            "line": frame.lineno if frame else "?",
            "command": event_method,
            "app": self.app_id,
            "traceback": "".join(traceback.format_exception(exc_type, exc, tb)),
        })

    # ------------------------------------------------------- module manager

    async def resync_module_commands(self, guild_id: int) -> bool:
        """Mini-bots publish global commands only: nothing per guild."""
        return False

    async def _default_gate(self, guild_id: int, module_class) -> bool:
        """Run a DEFAULT_ENABLED module's defaults only where nobody else does."""
        if self.db is None:
            return False
        apps = await self.db.apps_in_guild(guild_id)
        return can_claim(None, self.app_id, apps,
                         moddy_default_enabled=module_class.DEFAULT_ENABLED)
