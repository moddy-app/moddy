"""Invites: created, revoked, and posted in a channel.

The first two mirror Discord's own gateway events. The third has nothing to
do with the invite API: ``invite_post`` is a *message* event — someone
dropped a ``discord.gg/…`` link in a channel — which is why servers use it to
catch advertising, and why it is opened against the message's channel and
author so the usual ignore lists apply.
"""

from __future__ import annotations

from typing import List, Optional

import discord

from serverlogs.renderer import (
    fmt_bool, fmt_channel, fmt_code, fmt_number, fmt_time, fmt_user,
)


def _invite_link(code: Optional[str]) -> str:
    """The shareable form of a code, as inline code so it stays unclickable."""
    if not code:
        return "—"
    return fmt_code(f"discord.gg/{code}")


def _guild_of(invite: discord.Invite) -> Optional[discord.Guild]:
    """The real guild behind an invite.

    ``Invite.guild`` may be a partial object outside of gateway events, so the
    channel's guild — always a full one when it exists — comes first.
    """
    guild = getattr(invite.channel, "guild", None)
    if isinstance(guild, discord.Guild):
        return guild
    return invite.guild if isinstance(invite.guild, discord.Guild) else None


# --------------------------------------------------------------------------- #
# Listeners
# --------------------------------------------------------------------------- #

async def on_invite_create(service, invite: discord.Invite) -> None:
    from utils.i18n import t

    guild = _guild_of(invite)
    if guild is None:
        return

    entry = await service.open(guild, "invite_create", channel=invite.channel,
                               actor=invite.inviter)
    if entry is None:
        return
    entry.line("invite", _invite_link(invite.code))
    entry.line("channel", fmt_channel(invite.channel))
    entry.line("inviter", fmt_user(invite.inviter))
    entry.line("expires", fmt_time(invite.expires_at))
    # max_uses == 0 means "no cap", which fmt_number would render as a bare 0.
    entry.line("max_uses",
               fmt_number(invite.max_uses) if invite.max_uses
               else t("modules.logs.values.unlimited", locale=entry.locale))
    entry.line("temporary", fmt_bool(bool(invite.temporary), entry.locale))
    entry.actor(invite.inviter)
    await service.submit(guild, entry)


async def on_invite_delete(service, invite: discord.Invite) -> None:
    guild = _guild_of(invite)
    if guild is None:
        return
    who, reason = await service.executor(
        guild, discord.AuditLogAction.invite_delete)

    entry = await service.open(guild, "invite_delete", channel=invite.channel,
                               actor=who)
    if entry is None:
        return
    entry.line("invite", _invite_link(invite.code))
    entry.line("channel", fmt_channel(invite.channel))
    entry.line("uses", fmt_number(invite.uses))
    entry.actor(who, reason)
    await service.submit(guild, entry)


async def on_invite_post(service, message: discord.Message,
                         codes: List[str]) -> None:
    """Someone posted one or more invite links in a channel."""
    guild = message.guild
    if guild is None or not codes:
        return

    entry = await service.open(guild, "invite_post", channel=message.channel,
                               actor=message.author)
    if entry is None:
        return
    entry.line("channel", fmt_channel(message.channel))
    entry.line("author", fmt_user(message.author))
    entry.line("message_id", f"`{message.id}`")
    entry.line("invites", ", ".join(_invite_link(code) for code in codes))
    entry.url(message.jump_url)
    entry.actor(message.author)
    await service.submit(guild, entry)
