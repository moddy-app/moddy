"""
Shared invite creation.

Several flows need to hand someone a working invite to a guild Moddy is in:
an appeal reviewer investigating a case, a member whose temporary ban just
expired… They all need the same thing — the first channel where Moddy may
actually create an invite — so the lookup lives here instead of being
re-implemented per feature.
"""

from __future__ import annotations

import logging
from typing import Optional

import discord

logger = logging.getLogger('moddy.invites')

# Defaults for a reviewer-style, single-use invite.
DEFAULT_MAX_AGE = 86400   # 24 h
DEFAULT_MAX_USES = 1


async def create_guild_invite(
    guild: Optional[discord.Guild],
    *,
    reason: str,
    max_age: int = DEFAULT_MAX_AGE,
    max_uses: int = DEFAULT_MAX_USES,
) -> Optional[str]:
    """Return an invite URL for ``guild``, or ``None`` when none can be made.

    Tries the rules channel first, then the system channel, then any text
    channel — the first one where Moddy holds *Create Invite* wins. Never
    raises: a guild where the bot cannot invite simply yields ``None``.
    """
    if guild is None:
        return None
    me = guild.me
    if me is None:
        return None

    candidates = [guild.rules_channel, guild.system_channel]
    candidates += list(guild.text_channels)
    seen: set[int] = set()
    for channel in candidates:
        if channel is None or channel.id in seen:
            continue
        seen.add(channel.id)
        try:
            if not channel.permissions_for(me).create_instant_invite:
                continue
            invite = await channel.create_invite(
                max_age=max_age, max_uses=max_uses, unique=True, reason=reason,
            )
            return invite.url
        except (discord.Forbidden, discord.HTTPException):
            continue
    return None
