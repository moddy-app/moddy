"""
Bump prospects — the Discord wiring of ``services/bump_prospect_service.py``.

Feeds the service's scan queue and follows up open cards:

* at startup, every server without a row yet (once per process);
* when Moddy joins a server;
* when a bump directory or a reminder bot joins or leaves a server — the only
  events that can change a server's verdict;
* every ``FOLLOW_UP_MINUTES``, open cards are settled when the server enabled
  Moddy's Bump Reminder (``converted``) or removed Moddy (``left``).

``/dev bumpscan`` queues every server by hand. See docs/features/BUMP_PROSPECTS.md.
"""

import logging

import discord
from discord.ext import commands, tasks

from bumpreminder.prospects import is_watched
from db.repositories.bump_prospects import STATUS_LEFT
from services.bump_prospect_service import FOLLOW_UP_MINUTES

logger = logging.getLogger("moddy.cogs.bump_prospects")


class BumpProspects(commands.Cog):
    """Spots servers that bump by hand and puts them in front of the team."""

    def __init__(self, bot):
        self.bot = bot
        self._startup_done = False

    @property
    def service(self):
        return getattr(self.bot, "bump_prospects", None)

    async def cog_load(self):
        if self.service is None or not self.service.enabled:
            logger.info("Bump prospects disabled (no channel configured)")
            return
        self.service.start()
        self.follow_up.start()

    async def cog_unload(self):
        self.follow_up.cancel()
        if self.service is not None:
            self.service.stop()

    @commands.Cog.listener()
    async def on_ready(self):
        # on_ready fires again on every reconnect; the startup pass runs once.
        if self._startup_done or self.service is None or not self.service.enabled:
            return
        self._startup_done = True
        queued = await self.service.enqueue_unknown()
        logger.info("Bump prospects: %s servers queued for a first look", queued)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        if self.service is not None and self.service.enabled:
            self.service.enqueue([guild.id], priority=True)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        if self.service is not None and self.service.enabled:
            await self.service.settle(guild.id, STATUS_LEFT)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        self._watched_change(member)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        self._watched_change(member)

    def _watched_change(self, member: discord.Member) -> None:
        if not member.bot or not is_watched(member.id):
            return
        if self.service is not None and self.service.enabled:
            self.service.enqueue([member.guild.id], priority=True)

    @tasks.loop(minutes=FOLLOW_UP_MINUTES)
    async def follow_up(self):
        try:
            await self.service.follow_up()
        except Exception:  # noqa: BLE001 — the loop must survive a bad pass
            logger.exception("Bump prospect follow-up failed")

    @follow_up.before_loop
    async def _before_follow_up(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(BumpProspects(bot))
