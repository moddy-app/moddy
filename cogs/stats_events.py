"""The listeners that feed the statistics system.

Every emitter lives here rather than being sprinkled across sixty files. A
statistic is not a feature: nobody reading ``cogs/tickets.py`` should have to
step over a counter, and gathering the emitters in one place makes "what do
we actually measure" answerable by reading a single file alongside
``stats/registry.py``.

The two exceptions are the ones that would be wrong here: the API gateway
counts its own calls where it already logs them (``gateway/logger.py``), and
anything a module knows and Discord does not has to be emitted by that
module.

The Discord side of it costs nothing measurable: each listener is a handful
of dictionary operations, and ``bot.stats.incr`` neither awaits nor raises.

See docs/STATS.md.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import discord
from discord.ext import commands

from moddy import Cog

logger = logging.getLogger('moddy.stats.events')


class StatsEvents(Cog):
    """Turns Discord events into counters and lifecycle rows."""

    def __init__(self, bot):
        self.bot = bot

    # ------------------------------------------------------------------ #
    # Commands
    # ------------------------------------------------------------------ #

    @Cog.listener()
    async def on_app_command_completion(
        self,
        interaction: discord.Interaction,
        command,
    ):
        kind = "context" if isinstance(command, discord.app_commands.ContextMenu) else "slash"
        self._count_command(interaction.guild_id, interaction.user.id,
                            getattr(command, "qualified_name", str(command)), kind)

    @Cog.listener()
    async def on_command_completion(self, ctx: commands.Context):
        self._count_command(ctx.guild.id if ctx.guild else None, ctx.author.id,
                            ctx.command.qualified_name if ctx.command else "unknown",
                            "prefix")

    def _count_command(self, guild_id: Optional[int], user_id: int,
                       name: str, kind: str) -> None:
        self.bot.stats.incr(
            "command.used", guild_id=guild_id,
            dims={"command": name, "kind": kind},
        )
        # Distinct people, both in this server and across all of them. The
        # ids are folded into a HyperLogLog at the next flush and never
        # stored (see StatsService.observe_unique).
        if guild_id:
            self.bot.stats.observe_unique("user.active", user_id, guild_id=guild_id)
        self.bot.stats.observe_unique("user.active", user_id)

    @Cog.listener()
    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: discord.app_commands.AppCommandError,
    ):
        command = interaction.command
        self.bot.stats.incr(
            "command.error", guild_id=interaction.guild_id,
            dims={
                "command": getattr(command, "qualified_name", "unknown"),
                "error": type(error).__name__,
            },
        )

    @Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: Exception):
        self.bot.stats.incr(
            "command.error", guild_id=ctx.guild.id if ctx.guild else None,
            dims={
                "command": ctx.command.qualified_name if ctx.command else "unknown",
                "error": type(error).__name__,
            },
        )

    # ------------------------------------------------------------------ #
    # Members
    # ------------------------------------------------------------------ #

    @Cog.listener()
    async def on_member_join(self, member: discord.Member):
        self.bot.stats.incr("member.join", guild_id=member.guild.id)

    @Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        self.bot.stats.incr("member.leave", guild_id=member.guild.id)

    # ------------------------------------------------------------------ #
    # The bot's own footprint
    # ------------------------------------------------------------------ #

    @Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        """One row in ``guild_events``, plus the acquisition confirmation.

        This is the only place that writes a row per occurrence without
        hesitation: arrivals happen a few thousand times a month at most, and
        they are what the growth curve and the retention cohorts are computed
        from. Aggregating them would destroy exactly the detail that makes
        them worth having.
        """
        db = getattr(self.bot, "db", None)
        if not db or not getattr(db, "pool", None):
            return
        source = None
        try:
            # The backend wrote the UTM at the OAuth2 callback; all the bot
            # can add is that the install actually landed. No row means a
            # direct invite — not an error (see docs/STATS.md).
            source = await db.confirm_install(guild.id)
        except Exception as exc:
            logger.debug("stats: could not confirm the install of %s: %s", guild.id, exc)
        try:
            await db.record_guild_event(
                guild.id, "join",
                member_count=guild.member_count,
                owner_id=guild.owner_id,
                guild_age=self._age(guild.created_at),
                source=source,
            )
        except Exception as exc:
            logger.warning("stats: could not record the join of %s: %s", guild.id, exc)
        self.bot.stats.incr("guild.join", dims={"source": source or "unknown"})

    @Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        db = getattr(self.bot, "db", None)
        if not db or not getattr(db, "pool", None):
            return
        source = None
        lifetime = None
        try:
            source = await db.install_source(guild.id)
            joined_at = await db.last_guild_join(guild.id)
            if joined_at:
                lifetime = datetime.now(timezone.utc) - joined_at
        except Exception as exc:
            logger.debug("stats: could not resolve the history of %s: %s", guild.id, exc)
        try:
            await db.record_guild_event(
                guild.id, "leave",
                member_count=guild.member_count,
                owner_id=guild.owner_id,
                guild_age=self._age(guild.created_at),
                lifetime=lifetime,
                source=source,
            )
        except Exception as exc:
            logger.warning("stats: could not record the leave of %s: %s", guild.id, exc)
        self.bot.stats.incr("guild.leave", dims={"source": source or "unknown"})

    @staticmethod
    def _age(created_at: Optional[datetime]):
        if not created_at:
            return None
        return datetime.now(timezone.utc) - created_at


async def setup(bot):
    await bot.add_cog(StatsEvents(bot))
