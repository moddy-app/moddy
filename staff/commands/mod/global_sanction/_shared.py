"""Shared helpers for the ``/mod global`` sub-group."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from staff.framework import design, parse_guild_id
from utils import emojis
from utils.global_sanctions import GlobalLevel
from utils.i18n import t

GROUP = "global"
GROUP_DESCRIPTION = "Moddy-team global sanctions (user + servers)"


def parse_guild_ids(raw: Optional[str]) -> List[int]:
    """Parse a ``guilds`` option into ids.

    Accepts anything a moderator would naturally type: ids separated by
    commas, spaces or newlines.
    """
    if not raw:
        return []
    out: List[int] = []
    for chunk in raw.replace(",", " ").split():
        guild_id = parse_guild_id(chunk)
        if guild_id and guild_id not in out:
            out.append(guild_id)
    return out


def targets_recap(bot, user, guild_ids: List[int]) -> str:
    """Human-readable recap of who a sanction is about to hit."""
    lines: List[str] = []
    if user is not None:
        lines.append(f"{emojis.USER} {user} (`{user.id}`)")
    for guild_id in guild_ids:
        guild = bot.get_guild(guild_id)
        name = guild.name if guild else "Unknown server"
        lines.append(f"{emojis.GROUPS} {name} (`{guild_id}`)")
    return "\n".join(lines) if lines else "—"


async def resolve_group(bot, raw: str) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    """Resolve a group from a group id **or** any case reference inside it.

    Staff should never have to care which of the two they have at hand: a case
    reference from a report and the group id from the apply panel both work.
    """
    raw = (raw or "").strip()
    if not raw:
        return None, []

    # A 6-char public reference — look up the case's group.
    if len(raw) <= 8:
        group_id = await bot.db.get_case_group_id(raw)
        if group_id is None:
            return None, []
        return str(group_id), await bot.db.list_group_cases(group_id)

    try:
        cases = await bot.db.list_group_cases(raw)
    except (ValueError, TypeError):
        return None, []
    return (raw, cases) if cases else (None, [])


def group_level(cases: List[Dict[str, Any]]) -> GlobalLevel:
    """Highest level currently active across a group's cases."""
    from utils.global_sanctions import level_from_actions

    actions = [
        str(s.get("action"))
        for case in cases
        for s in case.get("sanctions", [])
        if str(s.get("status")) == "active"
    ]
    level = level_from_actions(actions)
    if level is not GlobalLevel.NONE:
        return level
    # Everything was revoked/expired — still show what the group *was* about.
    return level_from_actions(
        str(s.get("action"))
        for case in cases for s in case.get("sanctions", [])
    )


def not_found(locale: str):
    """Standard 'no such group' panel."""
    return design.error(
        t("global_sanctions.staff.group_notfound_title", locale=locale),
        t("global_sanctions.staff.group_notfound", locale=locale),
    )
