"""Bot permission helpers shared by /config and the module save paths.

Everything here is pure and synchronous (cache reads only, no network), so it
is safe to call before an interaction has been acknowledged.

Two levels are checked, and both are reported in ONE message so an admin never
fixes a permission only to discover the next missing one:

* guild level  - ``guild.me.guild_permissions`` against the module's
  ``ModuleBase.required_permissions()`` (baseline + module specific);
* channel level - ``channel.permissions_for(guild.me)`` in every channel the
  module is configured to use (``ModuleBase.channel_requirements(config)``),
  which also sees channel overwrites that deny the bot.
"""

from typing import Dict, Iterable, List, Mapping, Optional, Sequence

import discord

from utils.i18n import t

# What every module that posts a Components V2 message needs: see the channel,
# send in it, read its history (replies / fetch_message) and render custom
# emojis from Moddy's own server.
BASELINE_BOT_PERMISSIONS: List[str] = [
    "view_channel",
    "send_messages",
    "read_message_history",
    "use_external_emojis",
]


def merge_permissions(*groups: Iterable[str]) -> List[str]:
    """Concatenate permission-name lists, dropping duplicates, keeping order."""
    seen: Dict[str, None] = {}
    for group in groups:
        for key in group or ():
            seen.setdefault(key, None)
    return list(seen)


def missing_guild_permissions(guild: discord.Guild, required: Sequence[str]) -> List[str]:
    """Every key of ``required`` the bot lacks in ``guild`` (never short-circuits)."""
    me = guild.me
    mine = me.guild_permissions if me else discord.Permissions.none()
    return [key for key in required if not getattr(mine, key, False)]


def missing_channel_permissions(
    guild: discord.Guild,
    requirements: Mapping[int, Sequence[str]],
    *,
    skip: Sequence[str] = (),
) -> Dict[int, List[str]]:
    """``{channel_id: [missing keys]}`` for every channel that lacks something.

    Channels that no longer exist are ignored (the module reports those
    itself). ``skip`` lists permissions already reported at guild level, so the
    same permission is not shown twice.
    """
    me = guild.me
    if me is None:
        return {}
    result: Dict[int, List[str]] = {}
    for channel_id, keys in requirements.items():
        channel = guild.get_channel_or_thread(int(channel_id))
        if channel is None:
            continue
        perms = channel.permissions_for(me)
        missing = [k for k in keys if k not in skip and not getattr(perms, k, False)]
        if missing:
            result[int(channel_id)] = missing
    return result


def permission_name(key: str, locale: str) -> str:
    """Discord's own wording for a permission key."""
    return t(f"modules.logs.permissions.{key}", locale=locale)


def format_missing(
    guild_missing: Sequence[str],
    channel_missing: Mapping[int, Sequence[str]],
    locale: str,
) -> str:
    """One text block: guild-level bullets, then one section per channel."""
    lines: List[str] = [f"• {permission_name(k, locale)}" for k in guild_missing]
    for channel_id, keys in channel_missing.items():
        if lines:
            lines.append("")
        lines.append(t("modules.config.errors.missing_bot_perms.in_channel",
                       locale=locale, channel=f"<#{channel_id}>"))
        lines.extend(f"• {permission_name(k, locale)}" for k in keys)
    return "\n".join(lines)


def channel_validation_error(
    channel: discord.abc.GuildChannel,
    guild: discord.Guild,
    required: Sequence[str],
    locale: str,
) -> Optional[str]:
    """Save-time check of ONE channel: a single message listing ALL gaps.

    Returns ``None`` when the bot holds everything, otherwise a localized
    message naming the channel and every missing permission at once.
    """
    me = guild.me
    if me is None:
        return None
    perms = channel.permissions_for(me)
    missing = [k for k in required if not getattr(perms, k, False)]
    if not missing:
        return None
    return t("modules.config.errors.channel_missing_perms", locale=locale,
             channel=channel.mention,
             permissions=format_missing(missing, {}, locale))


def channel_map(*pairs) -> Dict[int, List[str]]:
    """Build ``{channel_id: [keys]}`` from ``(channel_id_or_None, keys)`` pairs.

    Falsy / non-numeric ids are skipped; the same channel listed twice has its
    permission lists merged.
    """
    result: Dict[int, List[str]] = {}
    for channel_id, keys in pairs:
        try:
            cid = int(channel_id)
        except (TypeError, ValueError):
            continue
        if not cid:
            continue
        result[cid] = merge_permissions(result.get(cid, []), keys)
    return result
