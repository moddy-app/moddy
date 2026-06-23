"""Shared helpers for the /mod case sub-group."""

from __future__ import annotations

from typing import Optional, Tuple

from staff.framework import design, parse_user_id, parse_guild_id
from utils import emojis
from utils.i18n import t
from utils.moderation_cases import ModerationCase, CaseStatus, EntityType


async def resolve_entity(ctx) -> Tuple[Optional[EntityType], Optional[int], Optional[str], object]:
    """Resolve the case target from a context.

    Returns ``(entity_type, entity_id, entity_name, error_view)``. ``error_view``
    is a panel when the target couldn't be resolved (otherwise ``None``).
    """
    bot = ctx.bot
    guild_id = None
    user_id = None

    if ctx.is_slash:
        if ctx.opt("guild_id"):
            guild_id = parse_guild_id(ctx.opt("guild_id"))
        elif ctx.opt("user"):
            user_id = ctx.opt("user").id
    else:
        parts = (ctx.raw_args or "").split()
        if parts and parts[0].lower() == "guild":
            guild_id = parse_guild_id(parts[1]) if len(parts) > 1 else None
        elif parts:
            user_id = parse_user_id(parts[0])

    if guild_id:
        guild = bot.get_guild(guild_id)
        return EntityType.GUILD, guild_id, (guild.name if guild else f"Guild {guild_id}"), None
    if user_id:
        try:
            user = await bot.fetch_user(user_id)
            name = f"{user} ({user.id})"
        except Exception:
            name = f"User {user_id}"
        return EntityType.USER, user_id, name, None

    return None, None, None, design.error(
        t("staff.common.invalid_usage.title", locale=ctx.locale),
        t("staff.mod.case.usage_target", locale=ctx.locale),
    )


def validate_case_id(raw: str, locale: str):
    """Return ``(case_id, error_view)``."""
    cid = (raw or "").strip().upper()
    if len(cid) != 8:
        return None, design.error(
            t("staff.mod.case.invalid_id_title", locale=locale),
            t("staff.mod.case.invalid_id", locale=locale),
        )
    return cid, None


async def build_case_panel(ctx, case: ModerationCase):
    """Build a standardized panel describing a moderation case."""
    bot = ctx.bot
    locale = ctx.locale

    if case.entity_type == EntityType.USER:
        try:
            user = await bot.fetch_user(case.entity_id)
            target = f"{user.mention} (`{user.id}`)"
        except Exception:
            target = f"`{case.entity_id}`"
    else:
        guild = bot.get_guild(case.entity_id)
        target = f"{guild.name} (`{guild.id}`)" if guild else f"`{case.entity_id}`"

    status_dot = emojis.GREEN_STATUS if case.status == CaseStatus.OPEN else emojis.RED_STATUS
    fields = [
        {"name": t("staff.mod.case.type", locale=locale), "value": f"`{case.case_type.value.title()}`"},
        {"name": t("staff.mod.case.sanction", locale=locale), "value": f"{case.get_sanction_emoji()} {case.get_sanction_name()}"},
        {"name": t("staff.mod.case.status", locale=locale), "value": f"{status_dot} `{case.status.value.title()}`"},
        {"name": t("staff.mod.case.target", locale=locale), "value": target},
        {"name": t("staff.mod.case.reason", locale=locale), "value": case.reason[:600]},
        {"name": t("staff.mod.case.created_by", locale=locale), "value": f"<@{case.created_by}> • <t:{int(case.created_at.timestamp())}:R>"},
    ]
    if case.evidence:
        fields.append({"name": t("staff.mod.case.evidence", locale=locale), "value": case.evidence[:600]})
    if case.duration:
        fields.append({"name": t("staff.mod.case.duration", locale=locale), "value": f"`{case.duration / 3600:.1f}h`"})
    if case.status == CaseStatus.CLOSED:
        closed = []
        if case.closed_by:
            closed.append(f"<@{case.closed_by}>")
        if case.closed_at:
            closed.append(f"<t:{int(case.closed_at.timestamp())}:R>")
        if closed:
            fields.append({"name": t("staff.mod.case.closed", locale=locale), "value": " • ".join(closed)})
        if case.close_reason:
            fields.append({"name": t("staff.mod.case.close_reason", locale=locale), "value": case.close_reason[:400]})
    if case.staff_notes:
        fields.append({"name": t("staff.mod.case.notes", locale=locale),
                       "value": t("staff.mod.case.notes_count", locale=locale, count=len(case.staff_notes))})

    return design.panel(
        "info" if case.status == CaseStatus.OPEN else "neutral",
        t("staff.mod.case.case_title", locale=locale, id=case.case_id),
        "",
        fields=fields,
        emoji=emojis.BLACKLIST,
        accent="error",
    )
