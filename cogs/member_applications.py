"""
Member Applications — the Discord wiring.

Two ways an application reaches Moddy, on purpose:

**The gateway.** Discord dispatches ``GUILD_JOIN_REQUEST_CREATE`` /
``_UPDATE`` / ``_DELETE`` to users with Kick Members. discord.py knows none of
them and drops unknown events after a dict lookup, so this cog adds three
entries to the connection's parser table — the same table discord.py reads for
every event — and turns each payload into an ``on_join_request_event``
dispatch. Nothing else pays for it: no debug events, no raw socket listener.

Whether Discord actually sends these events to *bots* is not documented. So:

**The poll.** Every server with the module enabled is reconciled against
``GET /guilds/{id}/requests`` on a timer: new applications get their card,
cards whose application was decided or withdrawn elsewhere get closed. Until a
join request event has been seen, the poll is the primary path (every two
minutes); once the gateway has proven itself, it drops to a safety net (every
fifteen). Both paths feed the same idempotent service, so an application seen
twice still gets exactly one card.

A third loop purges applications past Discord's own 180-day retention.

See docs/MEMBER_APPLICATIONS.md.
"""

import logging
import time
from typing import Any, Dict

import discord
from discord.ext import commands, tasks

from modules.member_applications import MODULE_ID
from services.member_application_service import get_service, requests_from_gateway

logger = logging.getLogger("moddy.cogs.member_applications")

GATEWAY_EVENTS = {
    "GUILD_JOIN_REQUEST_CREATE": "create",
    "GUILD_JOIN_REQUEST_UPDATE": "update",
    "GUILD_JOIN_REQUEST_DELETE": "delete",
}

# Seconds between two reconciliations of the same server.
POLL_INTERVAL_PRIMARY = 120     # gateway events not (yet) seen
POLL_INTERVAL_FALLBACK = 900    # gateway events proven to arrive
# Servers reconciled per tick at most — spreads a large fleet over several
# ticks instead of firing every request at once.
POLL_BATCH = 25


class MemberApplications(commands.Cog):
    """Keeps review cards in step with Discord's join requests."""

    def __init__(self, bot):
        self.bot = bot
        self.gateway_seen = False
        self._last_sync: Dict[int, float] = {}
        self._installed_parsers: Dict[str, Any] = {}

    async def cog_load(self):
        self._install_parsers()
        self.sync_applications.start()
        self.purge_applications.start()

    async def cog_unload(self):
        self.sync_applications.cancel()
        self.purge_applications.cancel()
        self._remove_parsers()

    # ------------------------------------------------------------ gateway
    def _install_parsers(self) -> None:
        connection = getattr(self.bot, "_connection", None)
        parsers = getattr(connection, "parsers", None)
        if not isinstance(parsers, dict):
            logger.warning("No gateway parser table — join request events disabled, poll only")
            return
        for event, kind in GATEWAY_EVENTS.items():
            if event in parsers:
                # discord.py learned the event itself; its parser wins.
                continue
            handler = self._make_parser(kind)
            parsers[event] = handler
            self._installed_parsers[event] = handler

    def _remove_parsers(self) -> None:
        parsers = getattr(getattr(self.bot, "_connection", None), "parsers", None)
        if isinstance(parsers, dict):
            for event, handler in self._installed_parsers.items():
                if parsers.get(event) is handler:
                    del parsers[event]
        self._installed_parsers.clear()

    def _make_parser(self, kind: str):
        def parse(data: Dict[str, Any]) -> None:
            # Runs inside the gateway read loop: dispatch and return at once.
            self.bot.dispatch("join_request_event", kind, data)
        return parse

    @commands.Cog.listener()
    async def on_join_request_event(self, kind: str, data: Dict[str, Any]):
        if not self.gateway_seen:
            self.gateway_seen = True
            logger.info("Join request gateway events are delivered — poll relaxed "
                        f"to every {POLL_INTERVAL_FALLBACK}s")

        try:
            guild_id = int(data.get("guild_id"))
        except (TypeError, ValueError):
            return
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return

        service = get_service(self.bot)
        try:
            if kind == "delete":
                request_id = data.get("id")
                if str(request_id or "").isdigit():
                    await service.withdraw(guild, int(request_id))
                return
            for request in requests_from_gateway(kind, data):
                await service.ingest(guild, request)
        except Exception as e:
            logger.error(f"Error handling join request {kind} in guild {guild_id}: {e}",
                         exc_info=True)

    # ------------------------------------------------------------ poll
    @tasks.loop(seconds=30)
    async def sync_applications(self):
        manager = getattr(self.bot, "module_manager", None)
        if manager is None or not getattr(self.bot, "db", None) or not self.bot.db.pool:
            return

        interval = POLL_INTERVAL_FALLBACK if self.gateway_seen else POLL_INTERVAL_PRIMARY
        now = time.monotonic()
        due = []
        for guild_id, modules in list(manager.active_modules.items()):
            module = modules.get(MODULE_ID)
            if module is None or not module.enabled:
                continue
            if now - self._last_sync.get(guild_id, 0.0) >= interval:
                due.append(guild_id)

        service = get_service(self.bot)
        for guild_id in due[:POLL_BATCH]:
            self._last_sync[guild_id] = now
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue
            try:
                await service.sync_guild(guild)
            except discord.Forbidden:
                logger.debug(f"No Kick Members in guild {guild_id} — applications not synced")
            except discord.HTTPException as e:
                logger.warning(f"Join request sync failed in guild {guild_id}: "
                               f"{e.status} {e.text}")
            except Exception as e:
                logger.error(f"Error syncing applications in guild {guild_id}: {e}",
                             exc_info=True)

    @sync_applications.before_loop
    async def before_sync(self):
        await self.bot.wait_until_ready()

    @tasks.loop(hours=6)
    async def purge_applications(self):
        if not getattr(self.bot, "db", None) or not self.bot.db.pool:
            return
        try:
            purged = await self.bot.db.purge_member_applications()
            if purged:
                logger.info(f"Purged {purged} member application(s) past retention")
        except Exception as e:
            logger.error(f"Error purging member applications: {e}", exc_info=True)

    @purge_applications.before_loop
    async def before_purge(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(MemberApplications(bot))
