"""Shared helpers for the ``/mod altguard`` sub-group."""

from __future__ import annotations

from typing import Optional, Tuple

from staff.framework import design, parse_guild_id, parse_user_id
from utils.i18n import t
from utils.members import get_or_fetch_member

GROUP = "altguard"
GROUP_DESCRIPTION = "AltGuard verification (anti multi-account)"

# Fine-grained permission node: AltGuard commands expose verification internals
# (scores, matched signals), so they are gated beyond the base Moderator role.
PERMISSION = "altguard_manage"


async def resolve_target(ctx, *, user_option: str = "user",
                         guild_option: str = "guild") -> Tuple[Optional[object], Optional[object], Optional[object]]:
    """Resolve ``(guild, member, error_view)`` from the command options.

    Moddy staff act from their own server, so the guild is always given
    explicitly by id and the member is fetched from it rather than from the
    interaction.
    """
    user_id = parse_user_id(str(ctx.opt(user_option) or ""))
    guild_id = parse_guild_id(str(ctx.opt(guild_option) or ""))

    if not user_id or not guild_id:
        return None, None, design.error(
            t("staff.mod.altguard.invalid_title", locale=ctx.locale),
            t("staff.mod.altguard.invalid", locale=ctx.locale),
        )

    guild = ctx.bot.get_guild(guild_id)
    if guild is None:
        return None, None, design.error(
            t("staff.mod.altguard.no_guild_title", locale=ctx.locale),
            t("staff.mod.altguard.no_guild", locale=ctx.locale, id=f"`{guild_id}`"),
        )

    member = await get_or_fetch_member(guild, user_id)
    if member is None:
        try:
            member = await guild.fetch_member(user_id)
        except Exception:
            member = None

    if member is None:
        return guild, None, design.error(
            t("staff.mod.altguard.no_member_title", locale=ctx.locale),
            t("staff.mod.altguard.no_member", locale=ctx.locale, id=f"`{user_id}`"),
        )

    return guild, member, None


async def resolve_module(ctx, guild_id: int):
    """The guild's AltGuard module, or ``(None, error_view)`` when unconfigured."""
    module = await ctx.bot.module_manager.get_module_instance(guild_id, "altguard")
    if not module or not module.enabled:
        return None, design.error(
            t("staff.mod.altguard.not_configured_title", locale=ctx.locale),
            t("staff.mod.altguard.not_configured", locale=ctx.locale, id=f"`{guild_id}`"),
        )
    return module, None
