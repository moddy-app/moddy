"""
Sanction expiration handling — reverse the Discord action, then tell the subject.

``bot.case_expiry`` flips due sanctions to ``expired`` in the database every two
minutes; this service turns each of those rows into what the subject actually
experiences:

- **ban** (guild) — the Discord ban is lifted, then the member is DMed with a
  fresh **invite** to the server, because an unban they cannot act on is
  invisible to them.
- **mute** (guild) — Discord clears the timeout on its own, so there is nothing
  to reverse; the member is simply told the timeout is over.
- **warn** (guild) — nothing to reverse either; the member is told the warning
  no longer stands.
- **global** (platform scope) — no Discord side and its own notice flow
  (``services/global_sanction_service.py``); only the resolver cache is dropped.

DM failures are never fatal: a closed DM, a member who left, a guild without
invite permission — each degrades to "no notification" while the expiry itself
stands.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, Optional

import discord

from utils import global_sanctions
from utils.expiration_views import NOTIFIABLE_ACTIONS, build_expiration_dm_view
from utils.invites import create_guild_invite

logger = logging.getLogger('moddy.expiration_notifier')

# Reason written to the Discord audit log when a temporary ban runs out.
UNBAN_REASON = "[Moddy] temporary ban expired"

# The invite handed to an unbanned member: long enough to be found in a DM they
# may not read immediately, single-use so it cannot be passed around.
INVITE_MAX_AGE = 7 * 86400  # 7 days
INVITE_MAX_USES = 1


class ExpirationNotifier:
    """Applies the consequences of expired sanctions (``bot.expirations``)."""

    def __init__(self, bot):
        self.bot = bot

    # ------------------------------------------------------------------ entry
    async def process(self, expired: Iterable[Dict[str, Any]]) -> int:
        """Handle every expired sanction row. Returns the number of DMs sent."""
        sent = 0
        for row in expired or []:
            try:
                if await self.handle(row):
                    sent += 1
            except Exception as exc:  # never let one row break the sweep
                logger.error("Error handling expired sanction: %s", exc, exc_info=True)
        return sent

    async def handle(self, row: Dict[str, Any]) -> bool:
        """Handle one expired sanction. Returns whether the subject was DMed."""
        # A temporary global sanction has no Discord side to reverse — it only
        # has to stop applying (its own service owns the subject's notices).
        if row.get("case_type") == "global":
            subject_type = row.get("subject_type") or global_sanctions.SUBJECT_USER
            global_sanctions.invalidate(self.bot, subject_type, row.get("subject_id"))
            return False

        action = row.get("action")
        if row.get("case_type") != "guild" or action not in NOTIFIABLE_ACTIONS:
            return False
        if row.get("scope_type") != "discord_guild" or not row.get("scope_id"):
            return False
        # Only guild-scoped cases about a Discord user are notifiable.
        if (row.get("subject_type") or "discord_user") != "discord_user":
            return False

        guild = self.bot.get_guild(int(row["scope_id"]))
        if guild is None:
            return False  # Moddy left the server — nothing it can say or do
        try:
            subject_id = int(row["subject_id"])
        except (TypeError, ValueError):
            return False

        invite_url: Optional[str] = None
        if action == "ban":
            if not await self._lift_ban(guild, subject_id):
                # The ban could not be lifted (missing permission, already
                # gone): do not promise a way back that does not exist.
                return False
            invite_url = await create_guild_invite(
                guild, reason=UNBAN_REASON,
                max_age=INVITE_MAX_AGE, max_uses=INVITE_MAX_USES,
            )

        return await self._notify(guild, subject_id, row, invite_url)

    # --------------------------------------------------------------- helpers
    async def _lift_ban(self, guild: discord.Guild, subject_id: int) -> bool:
        """Unban the subject. ``True`` when they are no longer banned."""
        me = guild.me
        if me is None or not me.guild_permissions.ban_members:
            return False
        try:
            await guild.unban(discord.Object(id=subject_id), reason=UNBAN_REASON)
            return True
        except discord.NotFound:
            # Already unbanned (manually, or by another Moddy path): the
            # sanction is still over, so the member should still hear about it.
            return True
        except (discord.Forbidden, discord.HTTPException) as exc:
            logger.warning(
                "Could not lift expired ban of %s in guild %s: %s",
                subject_id, guild.id, exc,
            )
            return False

    async def _notify(self, guild: discord.Guild, subject_id: int,
                      row: Dict[str, Any], invite_url: Optional[str]) -> bool:
        """DM the subject that their sanction expired. Never raises."""
        try:
            user = self.bot.get_user(subject_id) or await self.bot.fetch_user(subject_id)
        except discord.HTTPException:
            return False
        if user is None:
            return False

        view = build_expiration_dm_view(
            locale=await self.guild_locale(guild),
            action=row.get("action"),
            guild_name=guild.name,
            guild_id=guild.id,
            reference=row.get("reference"),
            expired_at=row.get("expires_at"),
            invite_url=invite_url,
        )
        try:
            await user.send(view=view)
            return True
        except discord.Forbidden:
            return False  # DMs closed — the case timeline still records it
        except discord.HTTPException as exc:
            logger.warning("Expiration DM to %s failed: %s", subject_id, exc)
            return False

    async def guild_locale(self, guild: Optional[discord.Guild]) -> str:
        """The locale the guild's sanction DMs are written in.

        The server language, set once in ``/config`` -> Server settings.
        """
        from utils.guild_language import guild_locale
        return await guild_locale(self.bot, guild)
