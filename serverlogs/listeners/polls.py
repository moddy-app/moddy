"""Polls: creation, deletion, final results and individual votes.

A poll lives inside a message, so every builder here starts from a
:class:`discord.Message` and bails out when ``message.poll`` is ``None`` —
Discord happily sends message events for messages that never carried a poll,
and a message can also lose its poll after the fact.

Votes arrive as raw payloads (``on_raw_poll_vote_add``/``_remove``), so the
voter is resolved from the guild cache when possible and falls back to a bare
id line when the member is not cached.
"""

from __future__ import annotations

from typing import List, Optional

import discord

from serverlogs.renderer import (
    escape, fmt_channel, fmt_number, fmt_time, fmt_user,
)

#: Answers listed inline before the block would overflow into a file.
MAX_LISTED_ANSWERS = 10


async def on_poll_create(service, message: discord.Message) -> None:
    await _log_poll(service, message, "poll_create")


async def on_poll_delete(service, message: discord.Message) -> None:
    await _log_poll(service, message, "poll_delete")


async def on_poll_finalize(service, message: discord.Message) -> None:
    """The poll closed: the vote counts are now exact rather than approximate."""
    await _log_poll(service, message, "poll_finalize", with_expiry=False)


async def _log_poll(service, message: discord.Message, event: str, *,
                    with_expiry: bool = True) -> None:
    """Shared body of the three message-level poll events."""
    guild = message.guild
    if guild is None or message.poll is None:
        return

    entry = await service.open(guild, event, channel=message.channel,
                               actor=message.author)
    if entry is None:
        return

    poll = message.poll
    entry.line("channel", fmt_channel(message.channel))
    entry.line("author", fmt_user(message.author))
    entry.line("message_id", f"`{message.id}`")
    entry.line("question", escape(poll.question))
    if with_expiry:
        entry.line("expires_at", fmt_time(poll.expires_at))
    entry.line("votes", fmt_number(poll.total_votes))
    entry.block("answers", _format_answers(poll))
    entry.url(message.jump_url)
    entry.thumbnail(message.author.display_avatar.url if message.author else None)
    await service.submit(guild, entry)


async def on_poll_vote(service, guild: discord.Guild,
                       payload: discord.RawPollVoteActionEvent,
                       added: bool) -> None:
    """One member adding or removing one vote on one answer."""
    channel = guild.get_channel_or_thread(payload.channel_id)
    member = guild.get_member(payload.user_id)
    event = "poll_votes_add" if added else "poll_votes_remove"

    entry = await service.open(guild, event, channel=channel, subject=member,
                               actor=member)
    if entry is None:
        return

    entry.line("channel",
               fmt_channel(channel) if channel else f"<#{payload.channel_id}>")
    entry.line("message_id", f"`{payload.message_id}`")
    entry.line("answer_id", fmt_number(payload.answer_id))
    if member is None:
        entry.line("user_id", f"`{payload.user_id}`")
    await service.submit(guild, entry)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _format_answers(poll: discord.Poll) -> Optional[str]:
    """The answers as a bulleted list, with their counts when they exist.

    Counts are only meaningful once Discord has sent results — before that
    they are absent, and a missing count reads better than a wrong ``0``.
    """
    lines: List[str] = []
    for answer in list(poll.answers)[:MAX_LISTED_ANSWERS]:
        label = escape(answer.text) or ""
        emoji = getattr(answer, "emoji", None)
        if emoji is not None:
            label = f"{emoji} {label}".strip()
        count = getattr(answer, "vote_count", None)
        if count is None:
            lines.append(f"• {label}")
        else:
            lines.append(f"• {label} — {fmt_number(count)}")
    return "\n".join(lines) if lines else None
