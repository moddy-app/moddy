"""Channels: creation, deletion, pins and every tracked setting.

The twenty-one channel events are declared as a diff table (see
:mod:`serverlogs.listeners._diff`): Discord sends one ``channel_update`` for
all of them, and each rule turns into its own log so a server can follow
renames without drowning in permission changes.
"""

from __future__ import annotations

from typing import Any, List, Optional

import discord

from serverlogs.listeners._diff import DiffRule, default_render, diff_sets, emit_diffs
from serverlogs.renderer import (
    escape, fmt_channel, fmt_duration, fmt_permissions, fmt_role, fmt_time,
    fmt_user,
)


# --------------------------------------------------------------------------- #
# Value renderers
# --------------------------------------------------------------------------- #

def _render_category(value: Any, locale: str) -> str:
    from utils.i18n import t
    if value is None:
        return t("modules.logs.values.none", locale=locale)
    return fmt_channel(value)


def _render_duration(value: Any, locale: str) -> str:
    return fmt_duration(int(value) if value is not None else None, locale)


def _render_minutes(value: Any, locale: str) -> str:
    return fmt_duration(int(value) * 60 if value is not None else None, locale)


def _render_bitrate(value: Any, locale: str) -> str:
    if value is None:
        return default_render(value, locale)
    return f"`{int(value) // 1000} kbps`"


def _render_user_limit(value: Any, locale: str) -> str:
    from utils.i18n import t
    if not value:
        return t("modules.logs.values.unlimited", locale=locale)
    return f"`{value}`"


def _render_region(value: Any, locale: str) -> str:
    from utils.i18n import t
    if value is None:
        return t("modules.logs.values.automatic", locale=locale)
    return f"`{escape(str(value))}`"


def _render_enum(value: Any, locale: str) -> str:
    if value is None:
        return default_render(value, locale)
    return f"`{escape(getattr(value, 'name', str(value)))}`"


def _render_emoji(value: Any, locale: str) -> str:
    if value is None:
        return default_render(value, locale)
    return str(value)


def _is_voice(channel: Any) -> bool:
    return isinstance(channel, (discord.VoiceChannel, discord.StageChannel))


def _is_forum(channel: Any) -> bool:
    return isinstance(channel, discord.ForumChannel)


#: One line per tracked setting — this table *is* the channel log catalogue.
CHANNEL_RULES = (
    DiffRule("channel_name_update", "name"),
    DiffRule("channel_topic_update", "topic", as_block=True),
    DiffRule("channel_nsfw_update", "nsfw"),
    DiffRule("channel_parent_update", "category", render=_render_category),
    DiffRule("channel_bitrate_update", "bitrate", render=_render_bitrate,
             applies=_is_voice),
    DiffRule("channel_user_limit_update", "user_limit", render=_render_user_limit,
             applies=lambda c: isinstance(c, discord.VoiceChannel)),
    DiffRule("channel_slowmode_update", "slowmode_delay", render=_render_duration),
    DiffRule("channel_rtc_region_update", "rtc_region", render=_render_region,
             applies=_is_voice),
    DiffRule("channel_video_quality_update", "video_quality_mode", render=_render_enum,
             applies=lambda c: isinstance(c, discord.VoiceChannel)),
    DiffRule("channel_default_archive_duration_update", "default_auto_archive_duration",
             render=_render_minutes),
    DiffRule("channel_default_thread_slowmode_update", "default_thread_slowmode_delay",
             render=_render_duration),
    DiffRule("channel_default_reaction_emoji_update", "default_reaction_emoji",
             render=_render_emoji, applies=_is_forum),
    DiffRule("channel_default_sort_order_update", "default_sort_order",
             render=_render_enum, applies=_is_forum),
    DiffRule("channel_forum_layout_update", "default_layout", render=_render_enum,
             applies=_is_forum),
)


# --------------------------------------------------------------------------- #
# Listeners
# --------------------------------------------------------------------------- #

async def on_channel_create(service, channel: discord.abc.GuildChannel) -> None:
    guild = channel.guild
    who, reason = await service.executor(
        guild, discord.AuditLogAction.channel_create, channel.id)

    entry = await service.open(guild, "channel_create", channel=channel, actor=who)
    if entry is None:
        return
    _header(entry, channel)
    if getattr(channel, "category", None):
        entry.line("category", fmt_channel(channel.category))
    entry.actor(who, reason)
    await service.submit(guild, entry)


async def on_channel_delete(service, channel: discord.abc.GuildChannel) -> None:
    guild = channel.guild
    who, reason = await service.executor(
        guild, discord.AuditLogAction.channel_delete, channel.id)

    entry = await service.open(guild, "channel_delete", channel=channel, actor=who)
    if entry is None:
        return
    _header(entry, channel)
    if getattr(channel, "category", None):
        entry.line("category", fmt_channel(channel.category))
    entry.line("channel_created", fmt_time(channel.created_at))
    entry.actor(who, reason)
    await service.submit(guild, entry)


async def on_channel_update(service, before: discord.abc.GuildChannel,
                            after: discord.abc.GuildChannel) -> None:
    guild = after.guild

    await emit_diffs(
        service, guild, CHANNEL_RULES, before, after,
        header=lambda entry: _header(entry, after),
        audit_action=discord.AuditLogAction.channel_update,
        audit_target_id=after.id,
    )

    if type(before) is not type(after):
        await _log_type_change(service, guild, before, after)
    if before.overwrites != after.overwrites:
        await _log_permission_change(service, guild, before, after)
    if _is_forum(after) and getattr(before, "available_tags", None) != getattr(after, "available_tags", None):
        await _log_forum_tags(service, guild, before, after)


async def on_channel_pins_update(service, channel: discord.abc.GuildChannel,
                                 last_pin) -> None:
    guild = getattr(channel, "guild", None)
    if guild is None:
        return
    entry = await service.open(guild, "channel_pins_update", channel=channel)
    if entry is None:
        return
    _header(entry, channel)
    entry.line("last_pin", fmt_time(last_pin))

    who = reason = None
    for action in (discord.AuditLogAction.message_pin,
                   discord.AuditLogAction.message_unpin):
        who, reason = await service.executor(guild, action, wait=1.0)
        if who is not None:
            break
    entry.actor(who, reason)
    await service.submit(guild, entry)


async def on_voice_status_update(service, guild: discord.Guild,
                                 audit_entry: discord.AuditLogEntry) -> None:
    """Voice channel "status" text.

    Discord has no gateway event for it, and discord.py has no enum member
    either — the cog matches the raw audit-log action id and forwards here.
    """
    channel = guild.get_channel(getattr(audit_entry, "target_id", None) or 0)
    entry = await service.open(guild, "channel_voice_status_update",
                               channel=channel, actor=audit_entry.user)
    if entry is None:
        return
    if channel is not None:
        _header(entry, channel)
    status = getattr(getattr(audit_entry, "extra", None), "status", None)
    entry.line("status", escape(status) if status else None)
    entry.actor(audit_entry.user, audit_entry.reason)
    await service.submit(guild, entry)


# --------------------------------------------------------------------------- #
# Special cases
# --------------------------------------------------------------------------- #

async def _log_type_change(service, guild, before, after) -> None:
    who, reason = await service.executor(
        guild, discord.AuditLogAction.channel_update, after.id, wait=1.5)
    entry = await service.open(guild, "channel_type_update", channel=after, actor=who)
    if entry is None:
        return
    _header(entry, after)
    entry.line("before", f"`{before.type.name}`")
    entry.line("after", f"`{after.type.name}`")
    entry.actor(who, reason)
    await service.submit(guild, entry)


async def _log_permission_change(service, guild, before, after) -> None:
    who = reason = None
    for action in (discord.AuditLogAction.overwrite_update,
                   discord.AuditLogAction.overwrite_create,
                   discord.AuditLogAction.overwrite_delete):
        who, reason = await service.executor(guild, action, after.id, wait=1.0)
        if who is not None:
            break

    entry = await service.open(guild, "channel_permissions_update", channel=after,
                               actor=who)
    if entry is None:
        return
    _header(entry, after)

    for target in set(before.overwrites) | set(after.overwrites):
        old = before.overwrites.get(target)
        new = after.overwrites.get(target)
        if old == new:
            continue
        label = fmt_role(target) if isinstance(target, discord.Role) else fmt_user(target)
        allowed, denied, reset = _overwrite_delta(old, new)
        details: List[str] = []
        if allowed:
            details.append(f"✚ {fmt_permissions(allowed, entry.locale)}")
        if denied:
            details.append(f"✖ {fmt_permissions(denied, entry.locale)}")
        if reset:
            details.append(f"↺ {fmt_permissions(reset, entry.locale)}")
        entry.block("target", f"{label}\n" + "\n".join(details) if details else label)

    entry.actor(who, reason)
    await service.submit(guild, entry)


def _overwrite_delta(old: Optional[discord.PermissionOverwrite],
                     new: Optional[discord.PermissionOverwrite]):
    """``(allowed, denied, reset)`` permission names between two overwrites."""
    old_pairs = dict(old) if old is not None else {}
    new_pairs = dict(new) if new is not None else {}
    allowed, denied, reset = [], [], []
    for name in set(old_pairs) | set(new_pairs):
        before_value = old_pairs.get(name)
        after_value = new_pairs.get(name)
        if before_value == after_value:
            continue
        if after_value is True:
            allowed.append(name)
        elif after_value is False:
            denied.append(name)
        else:
            reset.append(name)
    return sorted(allowed), sorted(denied), sorted(reset)


async def _log_forum_tags(service, guild, before, after) -> None:
    added, removed = diff_sets(getattr(before, "available_tags", []),
                               getattr(after, "available_tags", []))
    who, reason = await service.executor(
        guild, discord.AuditLogAction.channel_update, after.id, wait=1.5)

    entry = await service.open(guild, "channel_forum_tags_update", channel=after,
                               actor=who)
    if entry is None:
        return
    _header(entry, after)
    if added:
        entry.line("added", ", ".join(f"`{escape(tag.name)}`" for tag in added))
    if removed:
        entry.line("removed", ", ".join(f"`{escape(tag.name)}`" for tag in removed))
    entry.actor(who, reason)
    await service.submit(guild, entry)


def _header(entry, channel) -> None:
    """Identity lines shared by every channel event."""
    entry.line("channel", fmt_channel(channel))
    entry.line("id", f"`{channel.id}`")
    channel_type = getattr(channel, "type", None)
    if channel_type is not None:
        entry.line("type", f"`{channel_type.name}`")
