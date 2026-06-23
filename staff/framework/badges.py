"""
Verification-badge helpers for staff command displays.

CLAUDE.md rule #7: whenever a command shows a username/display name (outside a
mention) it must show the verification badge via ``get_user_verification_badge``
and ``format_verification_badge``. These helpers wrap that for the staff system
so every command renders names identically: ``**{display}**{badge}`` plus an
optional ``-#`` affiliation notice for org-member badges.
"""

from __future__ import annotations

from typing import Optional, Tuple

import discord

from utils.emojis import (
    get_user_verification_badge, format_verification_badge,
    DEV_BADGE, MANAGER_BADGE, MOD_SUPERVISOR_BADGE, COMMUNICATION_SUPERVISOR_BADGE,
    SUPPORT_SUPERVISOR_BADGE, MODERATOR_BADGE, COMMUNICATION_BADGE, SUPPORTAGENT_BADGE,
)

# Staff role value -> badge emoji.
STAFF_ROLE_BADGES = {
    "Dev": DEV_BADGE,
    "Manager": MANAGER_BADGE,
    "Supervisor_Mod": MOD_SUPERVISOR_BADGE,
    "Supervisor_Com": COMMUNICATION_SUPERVISOR_BADGE,
    "Supervisor_Sup": SUPPORT_SUPERVISOR_BADGE,
    "Moderator": MODERATOR_BADGE,
    "Communication": COMMUNICATION_BADGE,
    "Support": SUPPORTAGENT_BADGE,
}


def role_badge(role_value: str) -> str:
    """Return the badge emoji for a staff role value (empty string if unknown)."""
    return STAFF_ROLE_BADGES.get(role_value, "")


async def fetch_verification(bot, user_id: int) -> Tuple[dict, dict]:
    """Return ``(moddy_attributes, verification_data)`` from the DB."""
    moddy_attributes, verification = {}, {}
    if bot.db:
        try:
            data = await bot.db.get_user(user_id)
            if data:
                moddy_attributes = data.get("attributes", {}) or {}
                verification = (data.get("data") or {}).get("verification") or {}
        except Exception:
            pass
    return moddy_attributes, verification


def _user_api_dict(user: discord.abc.User) -> dict:
    try:
        flags = user.public_flags.value
    except Exception:
        flags = 0
    return {
        "public_flags": flags,
        "username": user.name,
        "global_name": getattr(user, "global_name", None),
        "id": str(user.id),
        "bot": getattr(user, "bot", False),
    }


def render_name(user: discord.abc.User, moddy_attributes: dict, verification: dict,
                *, prefer_display: bool = True) -> Tuple[str, list, Optional[str]]:
    """Render ``**name**{badge}`` for a user.

    Returns ``(rendered_name, org_names, tier)``. ``org_names`` is non-empty for
    org-member badges so the caller can add an affiliation notice.
    """
    ud = _user_api_dict(user)
    badge, orgs, tier = get_user_verification_badge(ud, moddy_attributes, verification)
    link = format_verification_badge(badge)
    name = (getattr(user, "global_name", None) or user.name) if prefer_display else user.name
    return f"**{name}**{link}", orgs, tier


async def render_user(bot, user: discord.abc.User, *, prefer_display: bool = True) -> Tuple[str, list, Optional[str]]:
    """Convenience: fetch verification data and render the name in one call."""
    attributes, verification = await fetch_verification(bot, user.id)
    return render_name(user, attributes, verification, prefer_display=prefer_display)
