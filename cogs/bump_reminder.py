"""
Bump Reminder — the Discord wiring.

Two halves, both of which have to live outside the module: a `modules/*.py` is
instantiated *per guild* and so cannot own a listener or a loop.

**The listener.** It is deliberately its own, and not a block inside
``cogs/module_events.py``: that cog drops bot-authored messages before any
module sees them (``if message.author.bot: return``), which is exactly the class
of message this feature reads. Relaxing that shared guard would change what four
other modules receive, so this cog watches on its own instead — the same
arrangement ``cogs/logs.py`` already uses.

The listener runs on **every message the bot can see**, so its first test is a
dict lookup over the seven known directories. Everything else — the guild's
module, the channel, the detection itself — sits behind that.

**The sweeper.** One query every thirty seconds, against a partial index, no
matter how many servers Moddy is in. Reminders live as rows, never as timers, so
the bot holds nothing in memory and a restart costs nothing: whatever came due
while it was down is simply due now, and goes out with a note saying it is late.

See docs/BUMP_REMINDER.md.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import discord
from discord.ext import commands, tasks

from bumpreminder import bot_by_app_id, bot_by_key
from notifications.models import NotificationContent, NotificationSource
from utils.bump_views import (
    LATE_AFTER,
    NO_MENTIONS,
    build_reminder_card,
    build_thanks_card,
    reminder_mentions,
)
from utils.emojis import ROCKET_LAUNCH
from utils.i18n import t

logger = logging.getLogger('moddy.cogs.bump_reminder')

MODULE_ID = "bump_reminder"

# How many due reminders one pass may take. Well above what any realistic
# thirty-second window produces; it exists so a backlog after a long outage
# drains over several passes instead of one enormous burst of API calls.
SWEEP_BATCH = 50


def _thanks_content(spec, guild_name: str) -> NotificationContent:
    """Uniform payload behind a thank-you card.

    Placeholders stay unresolved: every server bumping the same directory shares
    one stored body, and each notification stays reproducible from its variables.
    """
    return NotificationContent(
        title=guild_name,
        body="{user} bumped {server} on {directory}. Next bump {timestamp}.",
        icon=ROCKET_LAUNCH,
        template_id=f"bump_reminder.thanks.{spec.key}",
    )


def _reminder_content(spec, guild_name: str) -> NotificationContent:
    return NotificationContent(
        title=guild_name,
        body="{server} can be bumped on {directory} again.",
        icon=ROCKET_LAUNCH,
        template_id=f"bump_reminder.reminder.{spec.key}",
    )


class BumpReminder(commands.Cog):
    """Watches for successful bumps and posts the reminders they earn."""

    def __init__(self, bot):
        self.bot = bot
        self.sweep_bump_reminders.start()

    def cog_unload(self):
        self.sweep_bump_reminders.cancel()

    # --------------------------------------------------------------- listener
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # The hot path. Everything below the first two lines runs only for a
        # message posted by one of the seven directories.
        author = message.author
        if not author.bot or message.guild is None:
            return

        spec = bot_by_app_id(author.id)
        if spec is None or not self.bot.module_manager:
            return

        try:
            module = await self.bot.module_manager.get_module_instance(
                message.guild.id, MODULE_ID)
            if not module or not module.enabled:
                return

            found = await module.on_message(message, spec)
            if found is None:
                return

            await self._on_bump(message, module, found['hit'], found['entry'])
        except Exception as e:
            logger.error(
                f"Error handling bump message in guild {message.guild.id}: {e}",
                exc_info=True)

    async def _on_bump(self, message: discord.Message, module,
                       hit, entry: Dict[str, Any]) -> None:
        """Thank the bumper and arm the reminder."""
        guild = message.guild
        from utils.guild_language import guild_locale
        locale = await guild_locale(self.bot, guild)

        view = build_thanks_card(
            hit.bot, hit.bumper_id, hit.due_at,
            locale=locale, ping_mode=entry['ping_mode'],
        )

        # The thank-you is a courtesy; the reminder is the promise. So a failed
        # card is logged and stepped over — the row still gets armed below.
        result = await self.bot.notifications.send_channel(
            message.channel,
            content=_thanks_content(hit.bot, guild.name),
            source=NotificationSource.service_guild(MODULE_ID, guild.id),
            guild_id=guild.id,
            variables={
                "user": f"<@{hit.bumper_id}>" if hit.bumper_id else "—",
                "server": guild.name,
                "directory": hit.bot.name,
                "timestamp": f"<t:{int(hit.due_at.timestamp())}:R>",
            },
            view=view,
            allowed_mentions=NO_MENTIONS,
            locale=locale,
            attribution=False,
        )
        if not result.delivered:
            logger.warning(
                f"Bump thanks not posted in guild {guild.id} channel "
                f"{message.channel.id}: {result.error}")

        sent = result.message
        await self.bot.db.record_bump(
            guild.id, hit.bot.key,
            channel_id=entry['channel_id'],
            due_at=hit.due_at,
            bumper_id=hit.bumper_id,
            thanks_channel_id=message.channel.id,
            thanks_message_id=getattr(sent, "id", None),
        )
        logger.info(
            f"Bump recorded: guild {guild.id}, {hit.bot.key}, "
            f"by {hit.bumper_id}, due {hit.due_at.isoformat()}"
            f"{' (stated by the directory)' if hit.stated_by_bot else ''}"
        )

    # ---------------------------------------------------------------- sweeper
    @tasks.loop(seconds=30)
    async def sweep_bump_reminders(self):
        if not self.bot.db or not self.bot.db.pool:
            return
        try:
            for state in await self.bot.db.claim_due_bumps(SWEEP_BATCH):
                try:
                    await self._fire(state)
                except Exception as e:
                    logger.error(
                        f"Error firing bump reminder for guild {state['guild_id']}: {e}",
                        exc_info=True)
        except Exception as e:
            logger.error(f"Error sweeping bump reminders: {e}", exc_info=True)

    @sweep_bump_reminders.before_loop
    async def before_sweep(self):
        # Nothing to replay by hand: a reminder missed while the bot was down is
        # simply a row whose due_at is in the past, which the first pass claims.
        await self.bot.wait_until_ready()

    async def _fire(self, state: Dict[str, Any]) -> None:
        """Post one directory's reminder into every channel that asked for it."""
        guild = self.bot.get_guild(state['guild_id'])
        spec = bot_by_key(state['bot_key'])
        if guild is None or spec is None:
            return

        module = await self.bot.module_manager.get_module_instance(guild.id, MODULE_ID)
        if not module or not module.enabled:
            return

        # Re-read the config rather than trust the row: the reminder may have
        # been deleted, paused or re-pointed during the hours it was pending.
        entries = module.entries_for_bot(spec.key)
        if not entries:
            return

        from utils.guild_language import guild_locale
        locale = await guild_locale(self.bot, guild)

        now = datetime.now(timezone.utc)
        late_by = int((now - state['due_at']).total_seconds())
        bumped_at = state.get('bumped_at')
        elapsed = int((state['due_at'] - bumped_at).total_seconds()) if bumped_at else None

        bumper = None
        if state.get('bumper_id'):
            bumper = guild.get_member(state['bumper_id'])

        for entry in entries:
            await self._post(guild, spec, entry, state, locale,
                             bumper=bumper, elapsed=elapsed, late_by=late_by)

    async def _post(self, guild: discord.Guild, spec, entry: Dict[str, Any],
                    state: Dict[str, Any], locale: str, *,
                    bumper: Optional[discord.Member],
                    elapsed: Optional[int], late_by: int) -> None:
        channel = guild.get_channel(entry['channel_id'])
        if not isinstance(channel, discord.TextChannel):
            logger.warning(
                f"Bump reminder channel {entry['channel_id']} gone (guild {guild.id})")
            return

        perms = channel.permissions_for(guild.me)
        if not perms.view_channel or not perms.send_messages:
            logger.warning(
                f"Cannot post bump reminder in guild {guild.id} channel {channel.id} "
                "— missing permissions")
            return

        # Three ways the last bumper gets mentioned, and only these three.
        ping_mode = entry['ping_mode']
        mention_bumper = bool(bumper) and (
            ping_mode == "auto" or (ping_mode == "button" and state.get('opt_in'))
        )

        roles, allowed = reminder_mentions(
            guild, entry['role_ids'], bumper, mention_bumper)

        view = build_reminder_card(
            spec, locale=locale,
            role_ids=[role.id for role in roles],
            bumper_id=state.get('bumper_id'),
            mention_bumper=mention_bumper,
            elapsed=elapsed,
            late_by=late_by,
        )

        result = await self.bot.notifications.send_channel(
            channel,
            content=_reminder_content(spec, guild.name),
            source=NotificationSource.service_guild(MODULE_ID, guild.id),
            guild_id=guild.id,
            variables={"server": guild.name, "directory": spec.name},
            view=view,
            allowed_mentions=allowed,
            locale=locale,
            attribution=False,
        )
        if result.delivered:
            logger.info(
                f"Bump reminder sent: guild {guild.id}, {spec.key}, channel {channel.id}"
                f"{f' (late by {late_by}s)' if late_by >= LATE_AFTER else ''}"
            )
        else:
            logger.warning(
                f"Bump reminder not posted in guild {guild.id} channel "
                f"{channel.id}: {result.error}")


async def setup(bot):
    await bot.add_cog(BumpReminder(bot))
