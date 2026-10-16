"""
Automod member profiles — the periodic hold sweep.

A profile "hold" (a member timed out until they fix their avatar / banner)
outlives a Discord timeout: the longest one is 28 days. This cog keeps it in
force. A modules/*.py instance is per guild and cannot own a loop, hence the cog
(same arrangement as ``cogs/bump_reminder.py``).

Every hour, one query finds the holds whose timeout ends within the renewal
window AND the holds not re-screened for ``RECHECK_AFTER`` (a banner removal
raises no gateway event, so every hold is re-checked on a cadence), least
recently checked first so a stale hold never starves the batch; each is re-screened through the module (cached verdicts, so no
new SafeSearch call for an unchanged image): still flagged -> the timeout is
renewed, fixed -> lifted and announced. Holds of a server that switched the
feature off are dropped (the timeout simply lapses).

The listeners for joins and profile changes are in ``cogs/module_events.py``.
See docs/features/AUTOMOD.md "Profiles".
"""

import logging
from datetime import datetime, timedelta, timezone

from discord.ext import commands, tasks

from automod import constants as ac

logger = logging.getLogger('moddy.cogs.automod_profiles')

MODULE_ID = "automod_ai"

#: Holds handled per pass (a backlog drains over several passes).
SWEEP_BATCH = 100
#: A hold whose member never came back is forgotten once its timeout is this old.
ABANDONED_AFTER = timedelta(days=60)
#: Every hold is re-screened at least this often, expiry or not.
RECHECK_AFTER = timedelta(hours=6)


class AutomodProfiles(commands.Cog):
    """Renews or lifts the member-profile holds."""

    def __init__(self, bot):
        self.bot = bot
        self.sweep_profile_holds.start()

    def cog_unload(self):
        self.sweep_profile_holds.cancel()

    @tasks.loop(hours=1)
    async def sweep_profile_holds(self):
        if not self.bot.db or not self.bot.db.pool or not self.bot.module_manager:
            return
        now = datetime.now(timezone.utc)
        try:
            due = await self.bot.db.list_profile_holds_due(
                now + timedelta(hours=ac.PROFILE_HOLD_RENEW_WINDOW_HOURS), SWEEP_BATCH,
                recheck_before=now - RECHECK_AFTER)
        except Exception as e:
            logger.error(f"Error listing profile holds: {e}", exc_info=True)
            return
        for hold in due:
            try:
                await self._sweep_one(hold, now)
            except Exception as e:
                logger.error(
                    f"Error sweeping profile hold {hold['guild_id']}/{hold['user_id']}: {e}",
                    exc_info=True)

    async def _sweep_one(self, hold: dict, now: datetime) -> None:
        try:
            await self._sweep_hold(hold, now)
        finally:
            # Even a failing / absent hold rotates to the back of the queue.
            try:
                await self.bot.db.touch_profile_hold(
                    hold['guild_id'], hold['user_id'], hold['kind'])
            except Exception as e:  # noqa: BLE001
                logger.debug(f"Could not stamp profile hold: {e}")

    async def _sweep_hold(self, hold: dict, now: datetime) -> None:
        guild = self.bot.get_guild(hold['guild_id'])
        if guild is None:
            await self.bot.db.delete_profile_hold(
                hold['guild_id'], hold['user_id'], hold['kind'])
            return
        module = await self.bot.module_manager.get_module_instance(guild.id, MODULE_ID)
        if module is None or not hasattr(module, "sweep_hold"):
            return
        result = await module.sweep_hold(hold)
        if result == "absent":
            until = hold.get('timeout_until')
            if until is not None and now - until > ABANDONED_AFTER:
                await self.bot.db.delete_profile_hold(
                    hold['guild_id'], hold['user_id'], hold['kind'])

    @sweep_profile_holds.before_loop
    async def before_sweep(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(AutomodProfiles(bot))
