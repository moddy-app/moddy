"""
Bump Reminder suggestion — offer the module the moment a directory arrives.

A server that already has Moddy and then adds DISBOARD (or any directory of
:data:`bumpreminder.BUMP_BOTS`) is about to start bumping — and about to start
missing bump windows. That is the one moment the Bump Reminder is obviously
useful, so Moddy offers it right then instead of waiting for someone to find it
in ``/config``.

The offer goes to the **person who added the directory** (audit log, which needs
*View Audit Log*), the server owner otherwise — the same rule as the install
welcome (``utils/install_welcome.py``). It is a DM sent through the notification
system, carrying a channel picker: one pick activates a reminder for that
directory with its defaults. Roles, ping mode and delay stay in ``/config``.

Nothing is offered when:

* the server already has a reminder for that directory (enabled or paused);
* the module is paused server-wide — a deliberate "no" we do not argue with;
* the same directory was already offered on that server recently
  (``SUGGEST_COOLDOWN``) — a directory kicked and re-added, or a flapping
  integration, must not turn into a DM per join.

See docs/features/BUMP_REMINDER.md → "Offering the module".
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

import discord

from bumpreminder import BumpBot, bot_by_app_id

logger = logging.getLogger("moddy.services.bump_suggestion")

MODULE_ID = "bump_reminder"

#: How long one offer per (server, directory) holds. A month: long enough that
#: re-adding a directory never spams, short enough that a server which said
#: nothing then genuinely starts over gets asked again.
SUGGEST_COOLDOWN = 30 * 24 * 3600

#: Discord writes the ``bot_add`` audit entry a moment after the member join;
#: reading it immediately often misses it.
AUDIT_LOG_ATTEMPTS = 3
AUDIT_LOG_DELAY = 1.5

#: A select holds 25 options.
MAX_CHANNEL_OPTIONS = 25

_REDIS_KEY = "moddy:bump:suggested:{guild}:{bot}"


# --------------------------------------------------------------------------- #
# Pure decisions
# --------------------------------------------------------------------------- #
def should_suggest(module_config: Optional[Dict[str, Any]], bot_key: str) -> bool:
    """Whether a server with this stored config should be offered ``bot_key``.

    Pure: reads the raw stored config (``guilds.data.modules.bump_reminder``).
    """
    from modules.bump_reminder import normalize_config
    from modules.module_manager import STATE_KEY, STATE_PAUSED

    raw = module_config or {}
    if raw.get(STATE_KEY) == STATE_PAUSED:
        return False
    reminders = normalize_config(raw)["reminders"]
    return not any(entry["bot"] == bot_key for entry in reminders)


def rank_channels(channels: List[Any]) -> List[Any]:
    """Order candidate channels for the picker, best guess first.

    A channel whose name says "bump" is almost certainly the one, so those
    lead; the rest follow in the server's own order. Capped to what a select
    can hold.
    """
    def key(channel):
        named = "bump" in (getattr(channel, "name", "") or "").lower()
        return (0 if named else 1, getattr(channel, "position", 0), channel.id)

    return sorted(channels, key=key)[:MAX_CHANNEL_OPTIONS]


def candidate_channels(guild: discord.Guild) -> List[discord.TextChannel]:
    """Text channels where a reminder could actually live (Moddy can post)."""
    me = guild.me
    if me is None:
        return []
    found = []
    for channel in guild.text_channels:
        perms = channel.permissions_for(me)
        if perms.view_channel and perms.send_messages:
            found.append(channel)
    return rank_channels(found)


# --------------------------------------------------------------------------- #
# The service
# --------------------------------------------------------------------------- #
class BumpSuggestionService:
    """Sends at most one activation offer per (server, directory) per month."""

    def __init__(self, bot):
        self.bot = bot
        # Fallback when Redis is unavailable: per-process memory. A restart
        # forgets it, which at worst means one repeated offer.
        self._memory: Dict[str, float] = {}

    async def on_member_join(self, member: discord.Member) -> None:
        """Entry point from the gateway. Never raises."""
        if not member.bot:
            return
        spec = bot_by_app_id(member.id)
        if spec is None:
            return
        try:
            await self._maybe_suggest(member.guild, spec)
        except Exception as exc:  # noqa: BLE001 — an offer is never worth an error
            logger.error("Bump suggestion failed for guild %s (%s): %s",
                         member.guild.id, spec.key, exc, exc_info=True)

    async def _maybe_suggest(self, guild: discord.Guild, spec: BumpBot) -> None:
        manager = getattr(self.bot, "module_manager", None)
        if manager is None or MODULE_ID not in getattr(manager, "registered_modules", {}):
            return

        stored = await manager.get_module_config(guild.id, MODULE_ID)
        # Another app (Moddy or the Bump Reminder mini-bot) runs the module
        # here: it is not this one's to offer.
        if hasattr(manager, "owns") and not manager.owns(stored):
            return
        if not should_suggest(stored, spec.key):
            return

        if not await self._claim(guild.id, spec.key):
            return

        recipient = await self._resolve_adder(guild, spec)
        if recipient is None or getattr(recipient, "bot", False):
            return

        await self._send(guild, spec, recipient)

    async def _claim(self, guild_id: int, bot_key: str) -> bool:
        """Reserve the offer; False when it was already made recently."""
        key = _REDIS_KEY.format(guild=guild_id, bot=bot_key)
        redis = getattr(self.bot, "redis", None)
        if redis is not None:
            try:
                return bool(await redis.set(key, "1", nx=True, ex=SUGGEST_COOLDOWN))
            except Exception as exc:  # noqa: BLE001 — fall back to memory
                logger.debug("Redis unavailable for bump suggestion dedupe: %s", exc)

        now = time.monotonic()
        expires = self._memory.get(key)
        if expires is not None and expires > now:
            return False
        self._memory[key] = now + SUGGEST_COOLDOWN
        return True

    async def _resolve_adder(self, guild: discord.Guild,
                             spec: BumpBot) -> Optional[discord.abc.User]:
        """Who added the directory — the owner when the audit log says nothing."""
        me = guild.me
        if me is not None and me.guild_permissions.view_audit_log:
            for attempt in range(AUDIT_LOG_ATTEMPTS):
                try:
                    async for entry in guild.audit_logs(
                        limit=10, action=discord.AuditLogAction.bot_add
                    ):
                        if getattr(entry.target, "id", None) == spec.app_id and entry.user:
                            return entry.user
                except (discord.Forbidden, discord.HTTPException) as exc:
                    logger.debug("Audit log unreadable in guild %s: %s", guild.id, exc)
                    break
                if attempt + 1 < AUDIT_LOG_ATTEMPTS:
                    await asyncio.sleep(AUDIT_LOG_DELAY)

        if guild.owner is not None:
            return guild.owner
        if guild.owner_id:
            try:
                return await self.bot.fetch_user(guild.owner_id)
            except discord.HTTPException:
                return None
        return None

    async def _send(self, guild: discord.Guild, spec: BumpBot,
                    recipient: discord.abc.User) -> None:
        from notifications.models import NotificationSource
        from utils.bump_views import build_suggestion_card, suggestion_content
        from utils.guild_language import guild_locale

        locale = await guild_locale(self.bot, guild)
        channels = candidate_channels(guild)
        view = build_suggestion_card(spec, guild, channels, locale=locale)

        result = await self.bot.notifications.send_dm(
            recipient,
            content=suggestion_content(spec, locale),
            # Moddy offering one of its own modules, about a server event.
            source=NotificationSource.service_guild(MODULE_ID, guild.id),
            variables={"server": guild.name},
            view=view,
            locale=locale,
        )
        if result.delivered:
            logger.info("Bump reminder offered to %s for guild %s (%s)",
                        recipient.id, guild.id, spec.key)
        elif getattr(result, "forbidden", False):
            logger.info("Bump reminder offer not delivered to %s (closed DMs)",
                        recipient.id)
