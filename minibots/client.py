"""
One mini-bot: a deliberately lean ``discord.Client``.

Everything a mini-bot does not need is switched off, because each client's
cache is paid once per bot:

- intents: ``guilds`` + only what its spec declares (never members/presences);
- no message cache (``max_messages=None``), no member cache, no chunking;
- modules imported only when the bot starts, and loaded per guild lazily (an
  instance exists only in servers where an event actually needed one).

It exposes the attributes the existing modules read on ``bot`` (``db``,
``redis``, ``module_manager``, ``tech_logger``, ``stats``), so a Moddy module
runs unchanged under a mini-bot.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import sys
import traceback
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands

from minibots.ownership import can_claim
from minibots.spec import MiniBotSpec

if TYPE_CHECKING:
    from minibots.resources import SharedResources
    from minibots.runtime import MiniBotRuntime

logger = logging.getLogger("moddy.minibots")


def build_intents(spec: MiniBotSpec) -> discord.Intents:
    intents = discord.Intents.none()
    intents.guilds = True
    for name in spec.intents:
        setattr(intents, name, True)
    return intents


class MiniBotClient(discord.Client):
    def __init__(self, spec: MiniBotSpec, resources: "SharedResources",
                 runtime: Optional["MiniBotRuntime"] = None):
        super().__init__(
            intents=build_intents(spec),
            max_messages=None,
            member_cache_flags=discord.MemberCacheFlags.none(),
            chunk_guilds_at_startup=False,
            enable_debug_events=False,
        )
        self.spec = spec
        self.app_id = spec.id
        self.runtime = runtime
        self.resources = resources
        # Attributes the shared modules expect on ``bot``.
        self.db = resources.db
        self.redis = resources.redis
        self.tech_logger = resources.tech_logger
        self.stats = None
        self.module_slash_commands: dict = {}
        self.tree = app_commands.CommandTree(self)

        from modules.module_manager import ModuleManager
        self.module_manager = ModuleManager(self, app=spec.id)
        self.module_manager.default_gate = self._default_gate
        self._presence_reconciled = False

    # ------------------------------------------------------------ lifecycle

    async def setup_hook(self) -> None:
        self._register_modules()
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
        if self.db is not None and not self._presence_reconciled:
            try:
                await self.db.reconcile_app_guilds(self.app_id, [g.id for g in self.guilds])
                self._presence_reconciled = True
            except Exception as exc:  # noqa: BLE001
                logger.error("Mini-bot %s: presence reconcile failed: %s", self.app_id, exc)
        if self.runtime is not None:
            await self.runtime.on_client_ready(self)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        if self.db is not None:
            try:
                await self.db.mark_app_present(self.app_id, guild.id, guild.member_count)
            except Exception as exc:  # noqa: BLE001
                logger.error("Mini-bot %s: presence write failed: %s", self.app_id, exc)
        if self.tech_logger is not None:
            await self.tech_logger.log_minibot_guild(self.app_id, guild, True, len(self.guilds))

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        if self.db is not None:
            try:
                await self.db.mark_app_absent(self.app_id, guild.id)
            except Exception as exc:  # noqa: BLE001
                logger.error("Mini-bot %s: presence write failed: %s", self.app_id, exc)
        await self.module_manager.unload_guild_modules(guild.id)
        if self.tech_logger is not None:
            await self.tech_logger.log_minibot_guild(self.app_id, guild, False, len(self.guilds))

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
