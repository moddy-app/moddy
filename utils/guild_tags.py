"""Server tags ("guild tags"): does a server have one, and is a member wearing it.

Discord exposes a server's tag to bots in only two places, neither of which
discord.py models on ``discord.Guild``:

* the ``profile`` object (``{"tag": ..., "badge": ...}``) of the guild payload
  received over the gateway (``GUILD_CREATE`` / ``GUILD_UPDATE``);
* the ``primary_guild`` of every user wearing it
  (``identity_guild_id`` + ``identity_enabled`` + ``tag``).

There is no bot-usable REST route (``GET /guilds/{id}/profile`` is user-only),
so the gateway hooks in ``cogs/server_tag_events.py`` feed :func:`remember`
from both, and this module keeps the result in memory. When neither has been
seen yet, the ``GUILD_TAGS`` feature ("can set a tag") is the best available
signal that the server may have one.
"""

from typing import Any, Dict, Optional

import discord

# guild_id -> tag text ("" = the server explicitly has no tag).
_TAGS: Dict[int, str] = {}

GUILD_TAGS_FEATURE = "GUILD_TAGS"


def remember(guild_id: int, tag: Optional[str]) -> None:
    """Store what a payload said about a server's tag."""
    _TAGS[int(guild_id)] = (tag or "").strip()


def forget(guild_id: int) -> None:
    _TAGS.pop(int(guild_id), None)


def remember_from_guild_payload(data: Dict[str, Any]) -> None:
    """``GUILD_CREATE`` / ``GUILD_UPDATE``: read ``profile.tag`` when sent.

    Absent ``profile`` key = the payload says nothing, keep what we know.
    """
    if not isinstance(data, dict) or "id" not in data or "profile" not in data:
        return
    profile = data.get("profile") or {}
    remember(data["id"], profile.get("tag") if isinstance(profile, dict) else None)


def remember_from_user_payload(guild_id: int, user: Dict[str, Any]) -> None:
    """A member payload: someone wearing THIS server's tag tells us its text."""
    primary = (user or {}).get("primary_guild") or {}
    if not isinstance(primary, dict):
        return
    if str(primary.get("identity_guild_id")) != str(guild_id):
        return
    tag = primary.get("tag")
    if tag:
        remember(guild_id, tag)


def known_tag(guild_id: int) -> Optional[str]:
    """The server's tag text if a payload told us, else None."""
    tag = _TAGS.get(int(guild_id))
    return tag or None


def guild_has_tag(guild: Optional[discord.Guild]) -> bool:
    """True when the server has (or, lacking proof, can have) a tag."""
    if guild is None:
        return False
    if guild.id in _TAGS:
        return bool(_TAGS[guild.id])
    return GUILD_TAGS_FEATURE in (guild.features or [])


def wears_tag(user: Any, guild_id: int) -> bool:
    """True when ``user`` currently displays the tag of ``guild_id``."""
    primary = getattr(user, "primary_guild", None)
    if primary is None:
        return False
    return primary.id == guild_id and primary.identity_enabled is True
