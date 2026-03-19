"""
Cog Manager - Hot-disable/enable cogs at runtime.
When a cog is disabled, its commands show an unavailability message.
"""

import discord
from discord.ext import commands
from discord import app_commands
import logging
from typing import Set

logger = logging.getLogger('moddy.cog_manager')


class CogManager(commands.Cog):
    """Manages hot-disable/enable of cogs at runtime"""

    def __init__(self, bot):
        self.bot = bot
        # Set of disabled cog names (e.g. "Invite", "Reminder")
        self.disabled_cogs: Set[str] = set()
        # Store original checks for disabled cogs
        self._original_checks = {}

    def is_cog_disabled(self, cog_name: str) -> bool:
        return cog_name in self.disabled_cogs

    async def disable_cog(self, cog_name: str) -> tuple[bool, str]:
        """Disable a cog. Returns (success, message)."""
        cog = self.bot.get_cog(cog_name)
        if not cog:
            return False, f"Cog '{cog_name}' not found."

        if cog_name in self.disabled_cogs:
            return False, f"Cog '{cog_name}' is already disabled."

        if cog_name in ("ErrorTracker", "CogManager", "BlacklistCheck"):
            return False, f"Cog '{cog_name}' cannot be disabled (critical system)."

        self.disabled_cogs.add(cog_name)
        logger.info(f"Cog disabled: {cog_name}")
        return True, f"Cog '{cog_name}' has been disabled."

    async def enable_cog(self, cog_name: str) -> tuple[bool, str]:
        """Re-enable a cog. Returns (success, message)."""
        if cog_name not in self.disabled_cogs:
            return False, f"Cog '{cog_name}' is not disabled."

        self.disabled_cogs.discard(cog_name)
        logger.info(f"Cog re-enabled: {cog_name}")
        return True, f"Cog '{cog_name}' has been re-enabled."

    def get_disabled_cogs(self) -> list[str]:
        return sorted(self.disabled_cogs)


async def setup(bot):
    await bot.add_cog(CogManager(bot))
