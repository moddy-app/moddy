"""Server assets: custom emojis, stickers and soundboard sounds.

Discord does not tell us *what* changed in these three families: it sends the
**whole collection** on ``on_guild_emojis_update`` and
``on_guild_stickers_update``. Each builder therefore reconstructs the delta
itself — :func:`~serverlogs.listeners._diff.diff_sets` isolates the additions
and the removals, and the entries present on both sides are compared property
by property so a rename never reads as "deleted then created".

Soundboard sounds do get proper per-object gateway events, so they follow the
usual declarative :class:`~serverlogs.listeners._diff.DiffRule` table.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import discord

from serverlogs.listeners._diff import DiffRule, default_render, diff_sets, emit_diffs
from serverlogs.renderer import escape, fmt_bool, fmt_roles, fmt_time


# --------------------------------------------------------------------------- #
# Value renderers
# --------------------------------------------------------------------------- #

def _render_emoji(value: Any, locale: str) -> str:
    """A soundboard emoji (renderable) or a sticker's related emoji (a name)."""
    if value in (None, ""):
        return default_render(None, locale)
    if isinstance(value, str):
        return f"`{escape(value)}`"
    return str(value)


def _render_volume(value: Any, locale: str) -> str:
    if value is None:
        return default_render(value, locale)
    return f"`{int(float(value) * 100)}%`"


def _render_link(asset: Any, locale: str) -> str:
    """A playable/viewable asset, rendered as a single "open" markdown link."""
    from utils.i18n import t
    url = getattr(asset, "url", None)
    if not url:
        return escape(getattr(asset, "name", "")) or default_render(None, locale)
    return f"[{t('modules.logs.values.open_link', locale=locale)}]({url})"


#: Tracked soundboard properties — one line here is one loggable event.
SOUND_RULES = (
    DiffRule("sound_name_update", "name"),
    DiffRule("sound_volume_update", "volume", render=_render_volume),
    DiffRule("sound_emoji_update", "emoji", render=_render_emoji),
)


# --------------------------------------------------------------------------- #
# Emojis
# --------------------------------------------------------------------------- #

async def on_emojis_update(service, guild: discord.Guild, before: list,
                           after: list) -> None:
    """Fan the whole-collection gateway event out into per-emoji logs."""
    added, removed = diff_sets(before, after)

    for emoji in added:
        await _log_emoji_created(service, guild, emoji)
    for emoji in removed:
        await _log_emoji_deleted(service, guild, emoji)

    for old, new in _pairs(before, after):
        if old.name != new.name:
            await _log_emoji_name(service, guild, old, new)
        if list(getattr(old, "roles", []) or []) != list(getattr(new, "roles", []) or []):
            await _log_emoji_roles(service, guild, old, new)


async def _log_emoji_created(service, guild: discord.Guild,
                             emoji: discord.Emoji) -> None:
    who, reason = await service.executor(
        guild, discord.AuditLogAction.emoji_create, emoji.id)

    entry = await service.open(guild, "emoji_create", actor=who)
    if entry is None:
        return
    _emoji_header(entry, emoji)
    if getattr(emoji, "roles", None):
        entry.line("roles", fmt_roles(emoji.roles))
    entry.actor(who, reason)
    await service.submit(guild, entry)


async def _log_emoji_deleted(service, guild: discord.Guild,
                             emoji: discord.Emoji) -> None:
    who, reason = await service.executor(
        guild, discord.AuditLogAction.emoji_delete, emoji.id)

    entry = await service.open(guild, "emoji_delete", actor=who)
    if entry is None:
        return
    _emoji_header(entry, emoji)
    entry.line("created", fmt_time(emoji.created_at))
    entry.actor(who, reason)
    await service.submit(guild, entry)


async def _log_emoji_name(service, guild: discord.Guild, old: discord.Emoji,
                          new: discord.Emoji) -> None:
    who, reason = await service.executor(
        guild, discord.AuditLogAction.emoji_update, new.id, wait=1.5)

    entry = await service.open(guild, "emoji_name_update", actor=who)
    if entry is None:
        return
    _emoji_header(entry, new)
    entry.line("before", escape(old.name))
    entry.line("after", escape(new.name))
    entry.actor(who, reason)
    await service.submit(guild, entry)


async def _log_emoji_roles(service, guild: discord.Guild, old: discord.Emoji,
                           new: discord.Emoji) -> None:
    who, reason = await service.executor(
        guild, discord.AuditLogAction.emoji_update, new.id, wait=1.5)

    entry = await service.open(guild, "emoji_roles_update", actor=who)
    if entry is None:
        return
    _emoji_header(entry, new)

    added, removed = diff_sets(getattr(old, "roles", []), getattr(new, "roles", []))
    if added:
        entry.line("added", fmt_roles(added))
    if removed:
        entry.line("removed", fmt_roles(removed))
    entry.actor(who, reason)
    await service.submit(guild, entry)


def _emoji_header(entry, emoji: discord.Emoji) -> None:
    """Identity lines shared by every emoji event."""
    entry.line("emoji", str(emoji))
    entry.line("name", escape(emoji.name))
    entry.line("id", f"`{emoji.id}`")
    entry.line("animated", fmt_bool(bool(emoji.animated), entry.locale))
    entry.thumbnail(getattr(emoji, "url", None))


# --------------------------------------------------------------------------- #
# Stickers
# --------------------------------------------------------------------------- #

async def on_stickers_update(service, guild: discord.Guild, before: list,
                             after: list) -> None:
    """Same whole-collection treatment as emojis, for guild stickers."""
    added, removed = diff_sets(before, after)

    for sticker in added:
        await _log_sticker_created(service, guild, sticker)
    for sticker in removed:
        await _log_sticker_deleted(service, guild, sticker)

    for old, new in _pairs(before, after):
        if old.name != new.name:
            await _log_sticker_change(service, guild, new, "sticker_name_update",
                                      old.name, new.name)
        if (old.description or "") != (new.description or ""):
            await _log_sticker_change(service, guild, new,
                                      "sticker_description_update",
                                      old.description, new.description,
                                      as_block=True)
        if (old.emoji or "") != (new.emoji or ""):
            await _log_sticker_change(service, guild, new,
                                      "sticker_related_emoji_update",
                                      old.emoji, new.emoji,
                                      render=_render_emoji)


async def _log_sticker_created(service, guild: discord.Guild,
                               sticker: discord.GuildSticker) -> None:
    who, reason = await service.executor(
        guild, discord.AuditLogAction.sticker_create, sticker.id)

    entry = await service.open(guild, "sticker_create", actor=who)
    if entry is None:
        return
    _sticker_header(entry, sticker)
    entry.block("description", escape(sticker.description))
    entry.actor(who, reason)
    await service.submit(guild, entry)


async def _log_sticker_deleted(service, guild: discord.Guild,
                               sticker: discord.GuildSticker) -> None:
    who, reason = await service.executor(
        guild, discord.AuditLogAction.sticker_delete, sticker.id)

    entry = await service.open(guild, "sticker_delete", actor=who)
    if entry is None:
        return
    _sticker_header(entry, sticker)
    entry.line("created", fmt_time(sticker.created_at))
    entry.actor(who, reason)
    await service.submit(guild, entry)


async def _log_sticker_change(service, guild: discord.Guild,
                              sticker: discord.GuildSticker, event: str,
                              old: Any, new: Any, *,
                              render=default_render,
                              as_block: bool = False) -> None:
    """One before/after log for a single sticker property."""
    who, reason = await service.executor(
        guild, discord.AuditLogAction.sticker_update, sticker.id, wait=1.5)

    entry = await service.open(guild, event, actor=who)
    if entry is None:
        return
    _sticker_header(entry, sticker)
    add = entry.block if as_block else entry.line
    add("before", render(old, entry.locale))
    add("after", render(new, entry.locale))
    entry.actor(who, reason)
    await service.submit(guild, entry)


def _sticker_header(entry, sticker: discord.GuildSticker) -> None:
    """Identity lines shared by every sticker event."""
    entry.line("sticker", _render_link(sticker, entry.locale))
    entry.line("name", escape(sticker.name))
    entry.line("id", f"`{sticker.id}`")
    entry.thumbnail(getattr(sticker, "url", None))


# --------------------------------------------------------------------------- #
# Soundboard
# --------------------------------------------------------------------------- #

async def on_soundboard_sound_create(service, sound) -> None:
    guild = sound.guild
    who, reason = await service.executor(
        guild, discord.AuditLogAction.soundboard_sound_create, sound.id)

    entry = await service.open(guild, "sound_upload", actor=who)
    if entry is None:
        return
    _sound_header(entry, sound)
    entry.line("volume", _render_volume(sound.volume, entry.locale))
    entry.line("emoji", _render_emoji(sound.emoji, entry.locale))
    entry.actor(who, reason)
    await service.submit(guild, entry)


async def on_soundboard_sound_delete(service, sound) -> None:
    guild = sound.guild
    who, reason = await service.executor(
        guild, discord.AuditLogAction.soundboard_sound_delete, sound.id)

    entry = await service.open(guild, "sound_delete", actor=who)
    if entry is None:
        return
    _sound_header(entry, sound)
    entry.line("created", fmt_time(sound.created_at))
    entry.actor(who, reason)
    await service.submit(guild, entry)


async def on_soundboard_sound_update(service, before, after) -> None:
    guild = after.guild
    await emit_diffs(
        service, guild, SOUND_RULES, before, after,
        header=lambda entry: _sound_header(entry, after),
        audit_action=discord.AuditLogAction.soundboard_sound_update,
        audit_target_id=after.id,
    )


def _sound_header(entry, sound) -> None:
    """Identity lines shared by every soundboard event."""
    entry.line("sound", _render_link(sound, entry.locale))
    entry.line("name", escape(sound.name))
    entry.line("id", f"`{sound.id}`")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _pairs(before: Optional[list], after: Optional[list]) -> List[tuple]:
    """``(old, new)`` for every entry present in both collections.

    Matching is done on the object id, which is what makes a rename readable
    as a rename instead of a delete followed by a create.
    """
    old_by_id: Dict[int, Any] = {item.id: item for item in (before or [])}
    matched: List[tuple] = []
    for item in after or []:
        old = old_by_id.get(item.id)
        if old is not None:
            matched.append((old, item))
    return matched
