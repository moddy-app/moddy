"""
Invite tracker — which invite did a joining member use?

Discord never says. The only way to know is to compare each invite's ``uses``
counter before and after the join: exactly one invite whose counter went up is
the one that was used. Anything else (vanity URL, single-use or expired invite
that Discord already deleted, two members joining at the same instant) is
ambiguous, and an ambiguous join is attributed to nobody rather than guessed.

The result feeds the optional ``inviter_id`` of ``POST /altguard/token``. It is
a public Discord id, never shown to the member and never used to sanction
anyone on the bot side. Tracking is best-effort: it needs ``manage_guild`` and
silently does nothing without it -- it must never block the gate.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, Optional

import discord

logger = logging.getLogger('moddy.services.altguard_invites')


class InviteTracker:
    """In-memory ``{guild_id: {invite_code: uses}}`` baseline per tracked guild."""

    def __init__(self) -> None:
        self._uses: Dict[int, Dict[str, int]] = {}
        self._locks: Dict[int, asyncio.Lock] = {}

    # ------------------------------------------------------------------ #
    # Plumbing
    # ------------------------------------------------------------------ #

    def _lock(self, guild_id: int) -> asyncio.Lock:
        lock = self._locks.get(guild_id)
        if lock is None:
            lock = self._locks[guild_id] = asyncio.Lock()
        return lock

    def is_warm(self, guild_id: int) -> bool:
        return guild_id in self._uses

    def forget(self, guild_id: int) -> None:
        self._uses.pop(guild_id, None)

    @staticmethod
    def _can_read(guild: discord.Guild) -> bool:
        me = getattr(guild, 'me', None)
        return bool(me and me.guild_permissions.manage_guild)

    async def _fetch(self, guild: discord.Guild) -> Optional[list]:
        """The guild's invites, or None when they cannot be read."""
        if not self._can_read(guild):
            return None
        try:
            invites = await guild.invites()
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.debug(f"[AltGuard] Cannot list invites in guild {guild.id}: {e}")
            return None
        except Exception as e:
            logger.debug(f"[AltGuard] Unexpected invite listing error in guild {guild.id}: {e}")
            return None
        return list(invites)

    @staticmethod
    def _uses_of(invites) -> Dict[str, int]:
        return {invite.code: invite.uses or 0 for invite in invites}

    # ------------------------------------------------------------------ #
    # Baseline
    # ------------------------------------------------------------------ #

    async def warm(self, guild: discord.Guild, *, force: bool = False) -> bool:
        """Store the baseline for a guild. Returns True when it is now tracked.

        Without ``manage_guild`` (or when Discord refuses) the guild is dropped
        from the cache: a stale baseline would attribute wrongly later.
        """
        if self.is_warm(guild.id) and not force:
            return True
        async with self._lock(guild.id):
            invites = await self._fetch(guild)
            if invites is None:
                self._uses.pop(guild.id, None)
                return False
            self._uses[guild.id] = self._uses_of(invites)
            return True

    def on_invite_create(self, invite: discord.Invite) -> None:
        guild = invite.guild
        if guild is None or guild.id not in self._uses:
            return
        self._uses[guild.id][invite.code] = invite.uses or 0

    def on_invite_delete(self, invite: discord.Invite) -> None:
        guild = invite.guild
        if guild is None or guild.id not in self._uses:
            return
        self._uses[guild.id].pop(invite.code, None)

    # ------------------------------------------------------------------ #
    # Attribution
    # ------------------------------------------------------------------ #

    async def resolve_inviter(self, member: discord.Member) -> Optional[int]:
        """Discord id of the creator of the invite ``member`` just used, or None.

        Serialized per guild so simultaneous joins are compared one after the
        other. The cache is always refreshed, attribution or not -- otherwise
        the next join would be compared against a stale baseline.
        """
        guild = member.guild
        try:
            async with self._lock(guild.id):
                before = self._uses.get(guild.id)
                invites = await self._fetch(guild)
                if invites is None:
                    self._uses.pop(guild.id, None)
                    return None
                after = self._uses_of(invites)
                self._uses[guild.id] = after

                if before is None:
                    # Cold cache: every invite with uses > 0 would look "new".
                    return None

                increased = [code for code, uses in after.items()
                             if uses > before.get(code, 0)]
                if len(increased) != 1:
                    return None

                used = next(inv for inv in invites if inv.code == increased[0])
                inviter = used.inviter.id if used.inviter else None
                if inviter is None or inviter == member.id:
                    return None
                return inviter
        except Exception as e:
            logger.debug(f"[AltGuard] Inviter resolution failed in guild {guild.id}: {e}")
            return None
