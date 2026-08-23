"""Members: joins, leaves, kicks, bans, timeouts, profile and role changes.

Covers the ``server`` events about people (join / leave / kick / ban /
prune) and the whole ``users`` category (name, avatar, roles, timeout).
"""

from __future__ import annotations

from typing import List

import discord

from serverlogs.renderer import escape, fmt_number, fmt_roles, fmt_time


# --------------------------------------------------------------------------- #
# Arrivals and departures
# --------------------------------------------------------------------------- #

async def on_member_join(service, member: discord.Member) -> None:
    entry = await service.open(member.guild, "user_join", subject=member)
    if entry is None:
        return
    entry.line("account_created", fmt_time(member.created_at))
    entry.line("member_count", fmt_number(member.guild.member_count))
    if member.bot:
        who, reason = await service.executor(
            member.guild, discord.AuditLogAction.bot_add, member.id, wait=1.0)
        entry.actor(who, reason)
    await service.submit(member.guild, entry)


async def on_member_remove(service, member: discord.Member) -> None:
    """A departure is a leave, a kick or a ban — the audit log decides which.

    Discord sends the same GUILD_MEMBER_REMOVE for all three, so we give the
    audit log a moment to tell us which one it was before settling on
    "left on their own".
    """
    guild = member.guild

    kick = await service.audit.wait_for(
        guild.id,
        _member_action_matcher(discord.AuditLogAction.kick, member.id),
        timeout=2.0,
    )
    if kick is not None:
        entry = await service.open(guild, "user_kick", subject=member, actor=kick.user)
        if entry is not None:
            entry.line("joined_at", fmt_time(member.joined_at))
            entry.line("member_count", fmt_number(guild.member_count))
            entry.actor(kick.user, kick.reason)
            await service.submit(guild, entry)
        # The moderation category carries the same fact for mod teams.
        await _mirror_kick(service, guild, member, kick)
        return

    # A ban also removes the member; on_member_ban already logged it.
    ban = service.audit.find(
        guild.id, _member_action_matcher(discord.AuditLogAction.ban, member.id))
    if ban is not None:
        return

    entry = await service.open(guild, "user_leave", subject=member)
    if entry is None:
        return
    entry.line("joined_at", fmt_time(member.joined_at))
    entry.line("member_count", fmt_number(guild.member_count))
    entry.line("roles", fmt_roles([r for r in member.roles if not r.is_default()]))
    await service.submit(guild, entry)


async def _mirror_kick(service, guild, member, audit_entry) -> None:
    entry = await service.open(guild, "kick_add", subject=member,
                               actor=audit_entry.user)
    if entry is None:
        return
    entry.actor(audit_entry.user, audit_entry.reason)
    await service.submit(guild, entry)


async def on_member_ban(service, guild: discord.Guild,
                        user: discord.abc.User) -> None:
    # "ban_add" lives in both the server and the moderation categories; the
    # dispatcher fans the single entry out to whichever are configured.
    who, reason = await service.executor(guild, discord.AuditLogAction.ban, user.id)
    entry = await service.open(guild, "ban_add", subject=user, actor=who)
    if entry is None:
        return
    entry.line("account_created", fmt_time(user.created_at))
    entry.actor(who, reason)
    await service.submit(guild, entry)


async def on_member_unban(service, guild: discord.Guild,
                          user: discord.abc.User) -> None:
    who, reason = await service.executor(guild, discord.AuditLogAction.unban, user.id)
    entry = await service.open(guild, "ban_remove", subject=user, actor=who)
    if entry is None:
        return
    entry.actor(who, reason)
    await service.submit(guild, entry)


async def on_member_prune(service, guild: discord.Guild,
                          audit_entry: discord.AuditLogEntry) -> None:
    """Emitted from the audit-log listener: pruning has no gateway event."""
    entry = await service.open(guild, "member_prune", actor=audit_entry.user)
    if entry is None:
        return
    extra = audit_entry.extra or {}
    entry.line("members_removed", fmt_number(getattr(extra, "members_removed", None)))
    entry.line("inactive_days", fmt_number(getattr(extra, "delete_member_days", None)))
    entry.line("member_count", fmt_number(guild.member_count))
    entry.actor(audit_entry.user, audit_entry.reason)
    await service.submit(guild, entry)


# --------------------------------------------------------------------------- #
# Profile and roles
# --------------------------------------------------------------------------- #

async def on_member_update(service, before: discord.Member,
                           after: discord.Member) -> None:
    guild = after.guild

    if before.nick != after.nick or before.display_name != after.display_name:
        await _log_name_change(service, guild, before, after)

    if set(before.roles) != set(after.roles):
        await _log_role_change(service, guild, before, after)

    if before.timed_out_until != after.timed_out_until:
        await _log_timeout(service, guild, before, after)

    if before.display_avatar != after.display_avatar:
        entry = await service.open(guild, "user_avatar_update", subject=after)
        if entry is not None:
            entry.line("before", f"[{_link_label(entry)}]({before.display_avatar.url})")
            entry.image(after.display_avatar.url)
            await service.submit(guild, entry)


def _link_label(entry) -> str:
    from utils.i18n import t
    return t("modules.logs.values.open_link", locale=entry.locale)


async def _log_name_change(service, guild, before: discord.Member,
                           after: discord.Member) -> None:
    entry = await service.open(guild, "user_name_update", subject=after)
    if entry is None:
        return
    entry.line("before", escape(before.nick or before.name))
    entry.line("after", escape(after.nick or after.name))
    who, reason = await service.executor(
        guild, discord.AuditLogAction.member_update, after.id, wait=1.5)
    entry.actor(who, reason)
    await service.submit(guild, entry)


async def _log_role_change(service, guild, before: discord.Member,
                           after: discord.Member) -> None:
    added = [role for role in after.roles if role not in before.roles]
    removed = [role for role in before.roles if role not in after.roles]

    who, reason = await service.executor(
        guild, discord.AuditLogAction.member_role_update, after.id, wait=1.5)

    # One combined entry (what changed) plus the two focused ones, exactly
    # like the categories in the registry promise.
    combined = await service.open(guild, "user_roles_update", subject=after,
                                  actor=who)
    if combined is not None:
        if added:
            combined.line("roles_added", fmt_roles(added))
        if removed:
            combined.line("roles_removed", fmt_roles(removed))
        combined.actor(who, reason)
        await service.submit(guild, combined)

    if added:
        entry = await service.open(guild, "user_roles_add", subject=after, actor=who)
        if entry is not None:
            entry.line("roles_added", fmt_roles(added))
            entry.actor(who, reason)
            await service.submit(guild, entry)

    if removed:
        entry = await service.open(guild, "user_roles_remove", subject=after, actor=who)
        if entry is not None:
            entry.line("roles_removed", fmt_roles(removed))
            entry.actor(who, reason)
            await service.submit(guild, entry)


async def _log_timeout(service, guild, before: discord.Member,
                       after: discord.Member) -> None:
    timed_out = after.timed_out_until is not None
    event = "user_timed_out" if timed_out else "user_timeout_removed"
    who, reason = await service.executor(
        guild, discord.AuditLogAction.member_update, after.id, wait=1.5)

    entry = await service.open(guild, event, subject=after, actor=who)
    if entry is not None:
        if timed_out:
            entry.line("until", fmt_time(after.timed_out_until, "F"))
            entry.line("expires", fmt_time(after.timed_out_until))
        entry.actor(who, reason)
        await service.submit(guild, entry)

    # Mirror into the moderation category (mute add / remove).
    mirror = await service.open(guild, "mute_add" if timed_out else "mute_remove",
                                subject=after, actor=who)
    if mirror is not None:
        if timed_out:
            mirror.line("until", fmt_time(after.timed_out_until, "F"))
        mirror.actor(who, reason)
        await service.submit(guild, mirror)


async def on_user_update(service, guilds: List[discord.Guild],
                         before: discord.User, after: discord.User) -> None:
    """Global username/avatar changes, logged in every shared server."""
    for guild in guilds:
        if before.name != after.name or before.global_name != after.global_name:
            entry = await service.open(guild, "user_name_update", subject=after)
            if entry is not None:
                entry.line("before", escape(before.global_name or before.name))
                entry.line("after", escape(after.global_name or after.name))
                await service.submit(guild, entry)

        if before.display_avatar != after.display_avatar:
            entry = await service.open(guild, "user_avatar_update", subject=after)
            if entry is not None:
                entry.line("before", f"[{_link_label(entry)}]({before.display_avatar.url})")
                entry.image(after.display_avatar.url)
                await service.submit(guild, entry)


def _member_action_matcher(action: discord.AuditLogAction, member_id: int):
    def _check(entry: discord.AuditLogEntry) -> bool:
        if entry.action != action:
            return False
        target_id = getattr(entry.target, "id", None) or entry.target_id
        return target_id == member_id
    return _check
