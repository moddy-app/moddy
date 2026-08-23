"""Voice: who joined, left, moved between channels, or was disconnected.

Discord folds joins, leaves and switches into a single ``voice_state_update``
whose only reliable signal is ``before.channel`` vs ``after.channel`` — the
same event also fires for mute/deafen/stream changes, which this module
deliberately ignores.

Moves and forced disconnects have **no** gateway event at all: only the audit
log knows a moderator dragged someone out of a channel, so those two arrive
from the audit-log listener instead of from a voice state.
"""

from __future__ import annotations

from typing import Optional

import discord

from serverlogs.renderer import fmt_channel, fmt_number


# --------------------------------------------------------------------------- #
# Gateway: joins, leaves, switches
# --------------------------------------------------------------------------- #

async def on_voice_state_update(service, member: discord.Member,
                                before: discord.VoiceState,
                                after: discord.VoiceState) -> None:
    guild = member.guild
    old, new = before.channel, after.channel
    if old == new:
        # Mute, deafen, stream, camera… — not a movement, nothing to log.
        return

    if old is None and new is not None:
        entry = await service.open(guild, "voice_user_join", channel=new,
                                   subject=member)
        if entry is not None:
            entry.line("channel", fmt_channel(new))
            entry.line("members", fmt_number(len(new.members)))
            await service.submit(guild, entry)
        await _log_channel_full(service, guild, member, new)
        return

    if new is None and old is not None:
        entry = await service.open(guild, "voice_user_leave", channel=old,
                                   subject=member)
        if entry is not None:
            entry.line("channel", fmt_channel(old))
            entry.line("members", fmt_number(len(old.members)))
            await service.submit(guild, entry)
        return

    # Both sides set and different: the user hopped channels on their own.
    entry = await service.open(guild, "voice_user_switch", channel=new,
                               subject=member)
    if entry is None:
        return
    entry.line("before_channel", fmt_channel(old))
    entry.line("after_channel", fmt_channel(new))
    await service.submit(guild, entry)
    await _log_channel_full(service, guild, member, new)


async def _log_channel_full(service, guild, member: discord.Member,
                            channel: Optional[discord.abc.GuildChannel]) -> None:
    """Emit once, for the join that made the channel reach its user limit."""
    limit = getattr(channel, "user_limit", 0) or 0
    if not limit or len(channel.members) < limit:
        return

    entry = await service.open(guild, "voice_channel_full", channel=channel,
                               subject=member)
    if entry is None:
        return
    entry.line("channel", fmt_channel(channel))
    entry.line("user_limit", fmt_number(limit))
    entry.line("members", fmt_number(len(channel.members)))
    await service.submit(guild, entry)


# --------------------------------------------------------------------------- #
# Audit log: moves and forced disconnects
# --------------------------------------------------------------------------- #

async def on_voice_move(service, guild: discord.Guild,
                        audit_entry: discord.AuditLogEntry) -> None:
    """``member_move`` — a moderator dragged users into another channel.

    Discord reports the operation as a whole (a channel and a count), not one
    entry per user, so the log describes the batch rather than the members.
    """
    channel = getattr(audit_entry.extra, "channel", None)
    entry = await service.open(guild, "voice_user_move", channel=channel,
                               actor=audit_entry.user)
    if entry is None:
        return
    entry.line("channel", fmt_channel(channel))
    entry.line("count", fmt_number(_count(audit_entry)))
    entry.actor(audit_entry.user, audit_entry.reason)
    await service.submit(guild, entry)


async def on_voice_kick(service, guild: discord.Guild,
                        audit_entry: discord.AuditLogEntry) -> None:
    """``member_disconnect`` — users forcibly removed from voice."""
    target = audit_entry.target
    subject = target if isinstance(target, (discord.Member, discord.User)) else None
    channel = getattr(audit_entry.extra, "channel", None)

    entry = await service.open(guild, "voice_user_kick", channel=channel,
                               subject=subject, actor=audit_entry.user)
    if entry is None:
        return
    if channel is not None:
        entry.line("channel", fmt_channel(channel))
    entry.line("count", fmt_number(_count(audit_entry)))
    entry.actor(audit_entry.user, audit_entry.reason)
    await service.submit(guild, entry)


def _count(audit_entry: discord.AuditLogEntry) -> Optional[int]:
    """Discord sometimes sends audit-log counts as strings."""
    raw = getattr(getattr(audit_entry, "extra", None), "count", None)
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None
