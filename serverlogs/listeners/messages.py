"""Messages: deletions, bulk deletions, edits, publishes and command uses.

Two things make this category the noisiest one, and both are handled here
rather than in the renderer:

* **Bulk deletions** (a purge, an anti-raid sweep) produce one entry with a
  full ``.txt`` transcript attached instead of one embed per message.
* **Long messages** never get silently cut: anything that does not fit in an
  embed field is moved to a ``.txt`` attachment by
  :meth:`~serverlogs.renderer.LogEntry.block`.
"""

from __future__ import annotations

from typing import List, Optional, Sequence

import discord

from serverlogs.renderer import (
    build_transcript, escape, fmt_channel, fmt_number, fmt_time, fmt_user,
    truncate,
)

async def on_message_delete(service, message: discord.Message) -> None:
    guild = message.guild
    if guild is None:
        return

    entry = await service.open(guild, "message_delete", channel=message.channel,
                               subject=None, actor=message.author)
    if entry is None:
        return

    entry.line("channel", fmt_channel(message.channel))
    entry.line("message_id", f"`{message.id}`")
    entry.line("author", fmt_user(message.author))
    entry.line("message_created", fmt_time(message.created_at))
    entry.thumbnail(message.author.display_avatar.url if message.author else None)

    _add_content(entry, message)

    # Discord only files a MESSAGE_DELETE audit entry when someone deleted
    # *someone else's* message; a self-delete leaves no trace, which is the
    # normal case and simply means no footer.
    who, reason = await service.executor(
        guild, discord.AuditLogAction.message_delete,
        getattr(message.author, "id", None), wait=1.5)
    entry.actor(who, reason)

    await service.submit(guild, entry)


async def on_raw_message_delete(service, guild: discord.Guild,
                                payload: discord.RawMessageDeleteEvent) -> None:
    """Fallback for a message Discord no longer had in cache."""
    channel = guild.get_channel_or_thread(payload.channel_id)
    entry = await service.open(guild, "message_delete", channel=channel)
    if entry is None:
        return
    entry.line("channel", fmt_channel(channel) if channel else f"<#{payload.channel_id}>")
    entry.line("message_id", f"`{payload.message_id}`")
    entry.raw_line(_note(entry, "uncached_message"))
    await service.submit(guild, entry)


async def on_bulk_message_delete(service, messages: Sequence[discord.Message]) -> None:
    if not messages:
        return
    first = messages[0]
    guild = first.guild
    if guild is None:
        return

    entry = await service.open(guild, "message_bulk_delete", channel=first.channel)
    if entry is None:
        return

    ordered = sorted(messages, key=lambda m: m.created_at or first.created_at)
    authors = {m.author.id for m in ordered if m.author}

    entry.line("channel", fmt_channel(first.channel))
    entry.line("count", fmt_number(len(ordered)))
    entry.line("authors", fmt_number(len(authors)))
    entry.line("oldest_message", fmt_time(ordered[0].created_at))
    entry.line("newest_message", fmt_time(ordered[-1].created_at))
    entry.raw_line(_note(entry, "transcript_attached"))

    entry.attach_file(build_transcript(
        ordered,
        guild_name=guild.name,
        channel_name=getattr(first.channel, "name", str(first.channel.id)),
        header=_note(entry, "transcript_header"),
    ))

    who, reason = await service.executor(
        guild, discord.AuditLogAction.message_bulk_delete,
        getattr(first.channel, "id", None), wait=2.0)
    entry.actor(who, reason)

    await service.submit(guild, entry)


async def on_raw_bulk_message_delete(service, guild: discord.Guild,
                                     payload: discord.RawBulkMessageDeleteEvent) -> None:
    """Bulk deletion where none of the messages were cached."""
    if payload.cached_messages:
        return  # the cached path already logged it
    channel = guild.get_channel_or_thread(payload.channel_id)
    entry = await service.open(guild, "message_bulk_delete", channel=channel)
    if entry is None:
        return
    entry.line("channel", fmt_channel(channel) if channel else f"<#{payload.channel_id}>")
    entry.line("count", fmt_number(len(payload.message_ids)))
    entry.raw_line(_note(entry, "uncached_messages"))
    await service.submit(guild, entry)


async def on_message_edit(service, before: discord.Message,
                          after: discord.Message) -> None:
    guild = after.guild
    if guild is None or before.content == after.content:
        return

    entry = await service.open(guild, "message_edit", channel=after.channel,
                               actor=after.author)
    if entry is None:
        return

    entry.line("channel", fmt_channel(after.channel))
    entry.line("message_id", f"`{after.id}`")
    entry.line("author", fmt_user(after.author))
    entry.line("message_created", fmt_time(after.created_at))
    entry.thumbnail(after.author.display_avatar.url if after.author else None)
    entry.url(after.jump_url)

    entry.block("before", before.content or _note(entry, "empty_content"),
                filename=f"before-{after.id}.txt")
    entry.block("after", after.content or _note(entry, "empty_content"),
                filename=f"after-{after.id}.txt")

    await service.submit(guild, entry)


async def on_message_publish(service, message: discord.Message) -> None:
    """A message crossposted from an announcement channel."""
    guild = message.guild
    if guild is None:
        return
    entry = await service.open(guild, "message_publish", channel=message.channel,
                               actor=message.author)
    if entry is None:
        return
    entry.line("channel", fmt_channel(message.channel))
    entry.line("message_id", f"`{message.id}`")
    entry.line("author", fmt_user(message.author))
    entry.url(message.jump_url)
    _add_content(entry, message)
    entry.actor(message.author)
    await service.submit(guild, entry)


async def on_command_used(service, interaction: discord.Interaction) -> None:
    """An application command invoked in the server."""
    guild = interaction.guild
    if guild is None or interaction.command is None:
        return
    entry = await service.open(guild, "message_command_used",
                               channel=interaction.channel,
                               subject=interaction.user)
    if entry is None:
        return
    entry.line("channel", fmt_channel(interaction.channel))
    entry.line("command", f"`/{interaction.command.qualified_name}`")

    options = _format_options(interaction)
    if options:
        entry.block("options", options)
    entry.actor(interaction.user)
    await service.submit(guild, entry)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _add_content(entry, message: discord.Message) -> None:
    """Message body, attachments, stickers — the parts worth keeping."""
    content = message.content or ""
    if content:
        entry.block("message", content, filename=f"message-{message.id}.txt")
    elif not message.attachments and not message.stickers and not message.embeds:
        entry.raw_line(_note(entry, "empty_content"))

    if message.attachments:
        listing = "\n".join(
            f"• [{escape(a.filename)}]({a.url}) — `{_human_size(a.size)}`"
            for a in message.attachments[:10]
        )
        entry.block("attachments", truncate(listing, 1000))
        image = next((a for a in message.attachments
                      if (a.content_type or "").startswith("image/")), None)
        if image is not None:
            entry.image(image.url)

    if message.stickers:
        entry.line("stickers", ", ".join(f"`{escape(s.name)}`" for s in message.stickers))

    if message.reference and message.reference.message_id:
        entry.line("reply_to", f"`{message.reference.message_id}`")


def _format_options(interaction: discord.Interaction) -> Optional[str]:
    data = interaction.data or {}
    options = data.get("options") or []
    rendered: List[str] = []

    def walk(items, prefix=""):
        for option in items:
            name = f"{prefix}{option.get('name', '?')}"
            if option.get("options"):
                walk(option["options"], prefix=f"{name} ")
            elif "value" in option:
                rendered.append(f"• `{name}`: {escape(truncate(str(option['value']), 200))}")

    walk(options)
    return "\n".join(rendered) if rendered else None


def _human_size(size: Optional[int]) -> str:
    if not size:
        return "0 B"
    units = ("B", "KB", "MB", "GB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def _note(entry, key: str) -> str:
    from utils.i18n import t
    return t(f"modules.logs.values.{key}", locale=entry.locale)
