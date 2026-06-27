"""
Case sync — auto-records guild sanctions as cases from Discord events.

When a ban / kick / timeout happens on a server Moddy is in, a moderation case
is opened automatically through :class:`CaseService` — even when the action did
not go through Moddy. We listen to the guild audit log so the real moderator
and reason are captured.

Lifts (unban, timeout cleared) revoke the matching active sanction, which lets
the case auto-close once nothing is active.

This is intentionally source-driven: everything funnels through the ``guild``
case source, so extending behaviour later means touching the source registry,
not this cog.
"""

import logging
import time

import discord
from discord.ext import commands

from utils.moderation_cases import IssuerType

logger = logging.getLogger('moddy.case_sync')


class CaseSync(commands.Cog):
    """Mirror external guild moderation actions into the case system."""

    def __init__(self, bot):
        self.bot = bot

    def _is_moddy_initiated(self, guild_id: int, user_id: int, action: str) -> bool:
        """Return True if Moddy itself initiated this sanction (prevents double case recording)."""
        store = getattr(self.bot, "_moddy_initiated_sanctions", {})
        key = (guild_id, user_id, action)
        ts = store.get(key)
        if ts is not None and (time.time() - ts) < 10:
            store.pop(key, None)
            return True
        return False

    def _issuer(self, moderator: discord.abc.User):
        """Map the audit-log actor to (issuer_type, issuer_id)."""
        if moderator is None:
            return IssuerType.SYSTEM.value, None
        return IssuerType.DISCORD_USER.value, moderator.id

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry: discord.AuditLogEntry):
        """Single funnel for external moderation actions (needs View Audit Log)."""
        if not getattr(self.bot, "cases", None):
            return
        guild = entry.guild
        if guild is None:
            return

        action = entry.action
        target = entry.target
        target_id = getattr(target, "id", None)
        if target_id is None:
            return
        # Never open a case about a bot account.
        if isinstance(target, (discord.User, discord.Member)) and target.bot:
            return

        issuer_type, issuer_id = self._issuer(entry.user)
        reason = entry.reason or "External moderation action"

        try:
            A = discord.AuditLogAction
            if action == A.ban:
                if self._is_moddy_initiated(guild.id, target_id, "ban"):
                    return
                await self.bot.cases.record_sanction(
                    "guild", subject_id=target_id, action="ban", reason=reason,
                    issuer_type=issuer_type, issuer_id=issuer_id, scope_id=guild.id,
                )
            elif action == A.unban:
                await self.bot.cases.revoke_sanction(
                    "guild", subject_id=target_id, action="ban", scope_id=guild.id,
                    by_type=issuer_type, by_id=issuer_id,
                )
            elif action == A.kick:
                await self.bot.cases.record_sanction(
                    "guild", subject_id=target_id, action="kick", reason=reason,
                    issuer_type=issuer_type, issuer_id=issuer_id, scope_id=guild.id,
                )
            elif action == A.member_update:
                await self._handle_timeout(entry, guild, target_id, reason, issuer_type, issuer_id)
        except Exception as e:
            logger.error(f"case_sync: failed to record {action} in guild {guild.id}: {e}", exc_info=True)

    async def _handle_timeout(self, entry, guild, target_id, reason, issuer_type, issuer_id):
        """A member_update entry may set or clear a communication timeout (mute)."""
        before = getattr(entry.before, "timed_out_until", None)
        after = getattr(entry.after, "timed_out_until", None)
        # Nothing timeout-related changed.
        if before is None and after is None:
            return

        now = discord.utils.utcnow()
        if after is not None and after > now:
            # Timeout set / extended -> active mute with an expiry.
            if self._is_moddy_initiated(guild.id, target_id, "mute"):
                return
            await self.bot.cases.record_sanction(
                "guild", subject_id=target_id, action="mute", reason=reason,
                issuer_type=issuer_type, issuer_id=issuer_id, scope_id=guild.id,
                expires_at=after,
            )
        elif before is not None and (after is None or after <= now):
            # Timeout cleared -> revoke the active mute.
            await self.bot.cases.revoke_sanction(
                "guild", subject_id=target_id, action="mute", scope_id=guild.id,
                by_type=issuer_type, by_id=issuer_id,
            )


async def setup(bot):
    await bot.add_cog(CaseSync(bot))
