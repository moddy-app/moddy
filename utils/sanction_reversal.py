"""
Undo the Discord side of a sanction (unban / clear a timeout).

Shared by the automod appeal flow (``services/appeal_service.py``) and the
Moddy team labeling queue (``services/automod_label_service.py``): both revoke
a case sanction and must then lift what Discord still enforces. A warn has no
Discord-side effect; a deleted message cannot be restored.
"""

from __future__ import annotations

import logging
from typing import Optional

import discord

from utils.members import get_or_fetch_member

logger = logging.getLogger("moddy.sanction_reversal")


async def reverse_discord_sanction(guild: Optional[discord.Guild], subject_id: int,
                                   action: Optional[str], *, reason: str) -> bool:
    """Lift ``action`` ("ban" / "mute") for ``subject_id``. Returns True when
    Discord accepted the reversal (False for nothing-to-do or a failure)."""
    if guild is None or not action:
        return False
    try:
        if action == "ban":
            await guild.unban(discord.Object(id=int(subject_id)), reason=reason[:512])
            return True
        if action == "mute":
            member = await get_or_fetch_member(guild, int(subject_id))
            if member is not None:
                await member.timeout(None, reason=reason[:512])
                return True
    except (discord.Forbidden, discord.HTTPException, discord.NotFound) as e:
        logger.warning("sanction reversal (%s) failed in guild %s: %s", action, guild.id, e)
    return False
