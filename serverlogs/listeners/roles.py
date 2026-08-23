"""Roles: creation, deletion and every tracked property."""

from __future__ import annotations

from typing import Any

import discord

from serverlogs.listeners._diff import DiffRule, emit_diffs
from serverlogs.renderer import (
    escape, fmt_number, fmt_permissions, fmt_role, fmt_time,
)


def _render_colour(value: Any, locale: str) -> str:
    from utils.i18n import t
    if value is None or (isinstance(value, discord.Colour) and value.value == 0):
        return t("modules.logs.values.default_colour", locale=locale)
    return f"`{value}`"


def _render_icon(value: Any, locale: str) -> str:
    from utils.i18n import t
    if value is None:
        return t("modules.logs.values.none", locale=locale)
    url = getattr(value, "url", None)
    if url:
        return f"[{t('modules.logs.values.open_link', locale=locale)}]({url})"
    return escape(str(value))


ROLE_RULES = (
    DiffRule("role_name_update", "name"),
    DiffRule("role_color_update", "colour", render=_render_colour),
    DiffRule("role_hoist_update", "hoist"),
    DiffRule("role_mentionable_update", "mentionable"),
    DiffRule("role_icon_update", "display_icon", render=_render_icon),
)


async def on_role_create(service, role: discord.Role) -> None:
    guild = role.guild
    who, reason = await service.executor(
        guild, discord.AuditLogAction.role_create, role.id)

    entry = await service.open(guild, "role_create", actor=who)
    if entry is None:
        return
    _header(entry, role)
    entry.line("colour", _render_colour(role.colour, entry.locale))
    entry.line("hoisted", role.hoist)
    entry.line("mentionable", role.mentionable)
    entry.actor(who, reason)
    await service.submit(guild, entry)


async def on_role_delete(service, role: discord.Role) -> None:
    guild = role.guild
    who, reason = await service.executor(
        guild, discord.AuditLogAction.role_delete, role.id)

    entry = await service.open(guild, "role_delete", actor=who)
    if entry is None:
        return
    _header(entry, role)
    entry.line("members", fmt_number(len(role.members)))
    entry.line("role_created", fmt_time(role.created_at))
    entry.actor(who, reason)
    await service.submit(guild, entry)


async def on_role_update(service, before: discord.Role, after: discord.Role) -> None:
    guild = after.guild

    await emit_diffs(
        service, guild, ROLE_RULES, before, after,
        header=lambda entry: _header(entry, after),
        audit_action=discord.AuditLogAction.role_update,
        audit_target_id=after.id,
        thumbnail=getattr(after.display_icon, "url", None)
        if not isinstance(after.display_icon, str) else None,
    )

    if before.permissions != after.permissions:
        await _log_permissions(service, guild, before, after)


async def _log_permissions(service, guild, before: discord.Role,
                           after: discord.Role) -> None:
    who, reason = await service.executor(
        guild, discord.AuditLogAction.role_update, after.id, wait=1.5)

    entry = await service.open(guild, "role_permissions_update", actor=who)
    if entry is None:
        return
    _header(entry, after)

    before_set = {name for name, value in before.permissions if value}
    after_set = {name for name, value in after.permissions if value}
    added = sorted(after_set - before_set)
    removed = sorted(before_set - after_set)
    if added:
        entry.line("added", fmt_permissions(added, entry.locale))
    if removed:
        entry.line("removed", fmt_permissions(removed, entry.locale))
    entry.actor(who, reason)
    await service.submit(guild, entry)


def _header(entry, role: discord.Role) -> None:
    entry.line("role", f"{escape(role.name)} ({fmt_role(role)})")
    entry.line("id", f"`{role.id}`")
