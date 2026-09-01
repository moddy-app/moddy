"""Gateway wiring for the `/team role` linking window.

The window itself lives in :mod:`services.team_link_session`; this cog only
hands it the two events it watches, and finishes any window a restart cut in
half.

- **`on_guild_role_update`** is how success is detected: Discord sets the
  ``guild_connections`` tag on a role the moment it carries a *Links*
  requirement, and sends a role update carrying it.
- **`on_audit_log_entry_create`** is how anything *else* the staffer does gets
  caught and undone. It needs `View Audit Log`; without it the window still
  works, it just loses its watchdog — so the absence is logged loudly once.
"""

import logging

import discord
from discord.ext import commands

from services import team_link_session as linking

logger = logging.getLogger("moddy.cogs.team_link_events")


class TeamLinkEvents(commands.Cog):
    """Feeds the linking window, and cleans up after a restart."""

    def __init__(self, bot):
        self.bot = bot
        self._recovered = False

    @commands.Cog.listener()
    async def on_ready(self):
        # A staffer left without their roles by a crash is the one outcome this
        # feature must never produce — so the sweep runs at every boot, once.
        if self._recovered:
            return
        self._recovered = True
        try:
            await linking.recover_sessions(self.bot)
        except Exception:  # noqa: BLE001 — never keep the bot from starting
            logger.error("Could not recover interrupted linking windows", exc_info=True)

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role):
        if linking.active_session(after.guild.id) is None:
            return
        try:
            await linking.handle_role_update(before, after)
        except Exception:  # noqa: BLE001
            logger.error("Linking window: role update handler failed", exc_info=True)

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry: discord.AuditLogEntry):
        if linking.active_session(getattr(entry.guild, "id", 0)) is None:
            return
        try:
            await linking.handle_audit_entry(entry)
        except Exception:  # noqa: BLE001
            logger.error("Linking window: audit entry handler failed", exc_info=True)


async def setup(bot):
    await bot.add_cog(TeamLinkEvents(bot))
