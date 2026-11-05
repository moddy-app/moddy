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


# Discord's CHANNEL_OBFUSCATED channel flag (docs: Channel Object -> Obfuscated
# Channels). From 2026-11-16, a channel the bot cannot view is dispatched with
# name "___hidden___" and a single fake @everyone overwrite denying
# VIEW_CHANNEL: its real overwrites are unknown. See
# docs/guides/DISCORD_PERMISSIONS.md section 5.
CHANNEL_OBFUSCATED_FLAG = 1 << 17

# Permissions that only make sense when the bot can send in the channel
# (docs: "Denying SEND_MESSAGES implicitly denies MENTION_EVERYONE,
# SEND_TTS_MESSAGES, ATTACH_FILES, and EMBED_LINKS").
_IMPLIED_BY_SEND = ("embed_links", "attach_files", "mention_everyone", "send_tts_messages")


def is_obfuscated(channel) -> bool:
    """True when Discord hid this channel's metadata from the bot.

    Detected through the ``CHANNEL_OBFUSCATED`` flag only, as the docs require
    ("Do not rely on inspecting name"). Never raises.
    """
    try:
        flags = getattr(channel, "flags", None)
        value = getattr(flags, "value", flags)
        return bool(int(value or 0) & CHANNEL_OBFUSCATED_FLAG)
    except (TypeError, ValueError):
        return False


def _is_thread(channel) -> bool:
    return isinstance(channel, discord.Thread)


def channel_keys(channel, keys: Sequence[str]) -> List[str]:
    """Normalize the permission keys an action needs in ``channel``.

    * ``view_channel`` always comes first: without it nothing else in the
      channel is possible (implicit deny, which discord.py does not apply).
    * Embeds / files / mentions imply ``send_messages``.
    * In a thread, ``send_messages`` is replaced by
      ``send_messages_in_threads`` ("The SEND_MESSAGES permission has no
      effect in threads").
    """
    out = list(merge_permissions(keys))
    if any(k in out for k in _IMPLIED_BY_SEND) and "send_messages" not in out \
            and "send_messages_in_threads" not in out:
        out.append("send_messages")
    if _is_thread(channel):
        out = ["send_messages_in_threads" if k == "send_messages" else k for k in out]
        out = merge_permissions(out)
    if "view_channel" in out:
        out.remove("view_channel")
    return ["view_channel"] + out


def channel_missing(channel, member, keys: Sequence[str]) -> List[str]:
    """Every permission of ``keys`` ``member`` lacks in ``channel`` (all at once).

    Follows Discord's own computation (``permissions_for``: roles, @everyone,
    role and member overwrites, timeout mask) plus the implicit rules above.
    Returns ``["view_channel", ...]`` for a channel the member cannot see.
    Never raises: an unusable channel/member reports ``view_channel``.
    """
    normalized = channel_keys(channel, keys)
    if member is None:
        return normalized
    try:
        perms = channel.permissions_for(member)
    except Exception:  # noqa: BLE001 - stale cache objects must not crash a check
        return normalized
    return [k for k in normalized if not getattr(perms, k, False)]


def can_act_in(channel, member, keys: Sequence[str]) -> bool:
    """Shorthand: ``member`` holds every key of ``keys`` in ``channel``."""
    return not channel_missing(channel, member, keys)


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
        missing = [k for k in channel_missing(channel, me, keys) if k not in skip]
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
    missing = channel_missing(channel, me, required)
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
