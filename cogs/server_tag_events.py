"""
Server tag events — gateway wiring for the Auto Role "server tag" roles.

discord.py models ``User.primary_guild`` but not the server side of a tag, and
its ``on_member_update`` / ``on_user_update`` only fire for members already in
the cache (startup chunking is off, see ``CHUNK_GUILDS_AT_STARTUP``). So this
cog wraps four gateway parsers instead of listening to the high-level events:

* ``GUILD_CREATE`` / ``GUILD_UPDATE`` -> remember the server's tag
  (``profile.tag``) for the /config panel (``utils/guild_tags.py``);
* ``GUILD_MEMBER_ADD`` -> learn the tag from a member wearing it (the join
  roles themselves are applied by ``AutoRoleModule.apply_roles``);
* ``GUILD_MEMBER_UPDATE`` -> learn the tag, then re-sync the member's tag roles.

Each wrapper only reads the payload, then calls discord.py's own parser
unchanged, so the library behaves exactly as before.
"""

import asyncio
import logging
from typing import Any, Callable, Dict

import discord
from discord.ext import commands

from utils import guild_tags

logger = logging.getLogger('moddy.cogs.server_tag_events')

WRAPPED_EVENTS = ("GUILD_CREATE", "GUILD_UPDATE", "GUILD_MEMBER_ADD", "GUILD_MEMBER_UPDATE")


class ServerTagEvents(commands.Cog):
    """Keeps the Auto Role tag roles in step with who wears the server tag."""

    def __init__(self, bot):
        self.bot = bot
        # event -> (original parser, our wrapper)
        self._wrapped: Dict[str, tuple] = {}

    async def cog_load(self):
        self._install_parsers()

    async def cog_unload(self):
        self._remove_parsers()

    # ------------------------------------------------------------ gateway
    def _install_parsers(self) -> None:
        parsers = getattr(getattr(self.bot, "_connection", None), "parsers", None)
        if not isinstance(parsers, dict):
            logger.warning("No gateway parser table — server tag roles disabled")
            return
        for event in WRAPPED_EVENTS:
            original = parsers.get(event)
            if original is None:
                continue
            wrapper = self._make_parser(event, original)
            parsers[event] = wrapper
            self._wrapped[event] = (original, wrapper)

    def _remove_parsers(self) -> None:
        parsers = getattr(getattr(self.bot, "_connection", None), "parsers", None)
        if isinstance(parsers, dict):
            for event, (original, wrapper) in self._wrapped.items():
                if parsers.get(event) is wrapper:
                    parsers[event] = original
        self._wrapped.clear()

    def _make_parser(self, event: str, original: Callable[[Dict[str, Any]], Any]):
        def parse(data: Dict[str, Any]) -> Any:
            # Runs inside the gateway read loop: read, never raise, never wait.
            try:
                self._observe(event, data)
            except Exception as e:
                logger.debug(f"Server tag observe failed on {event}: {e}")
            result = original(data)
            if event == "GUILD_MEMBER_UPDATE":
                self._schedule_sync(data)
            return result
        return parse

    @staticmethod
    def _observe(event: str, data: Dict[str, Any]) -> None:
        if event in ("GUILD_CREATE", "GUILD_UPDATE"):
            guild_tags.remember_from_guild_payload(data)
        elif "guild_id" in data and isinstance(data.get("user"), dict):
            guild_tags.remember_from_user_payload(data["guild_id"], data["user"])

    def _schedule_sync(self, data: Dict[str, Any]) -> None:
        user = data.get("user")
        if not isinstance(user, dict) or user.get("bot") or "guild_id" not in data:
            return
        # No primary_guild key at all = the payload says nothing about the tag.
        if "primary_guild" not in user:
            return
        try:
            guild_id, user_id = int(data["guild_id"]), int(user["id"])
        except (KeyError, TypeError, ValueError):
            return
        manager = self.bot.module_manager
        loaded = getattr(manager, "active_modules", {}).get(guild_id) if manager else None
        if loaded is not None and 'auto_role' not in loaded:
            # The server's modules are loaded and Auto Role is not one of them.
            return
        asyncio.create_task(self._sync(guild_id, user_id))

    async def _sync(self, guild_id: int, user_id: int) -> None:
        if not self.bot.module_manager:
            return
        try:
            module = await self.bot.module_manager.get_module_instance(guild_id, 'auto_role')
            if not module or not module.enabled or not module.tag_roles:
                return
            guild = self.bot.get_guild(guild_id)
            # discord.py's parser just cached the member (MemberCacheFlags.joined).
            member = guild.get_member(user_id) if guild else None
            if member is None:
                return
            await module.sync_tag_roles(member)
        except Exception as e:
            logger.error(
                f"Error syncing tag roles for {user_id} in guild {guild_id}: {e}",
                exc_info=True
            )

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        guild_tags.forget(guild.id)


async def setup(bot):
    await bot.add_cog(ServerTagEvents(bot))
