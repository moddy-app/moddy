"""Bot permission helpers shared by /config and the module save paths.

Everything here is pure and synchronous (cache reads only, no network), so it
is safe to call before an interaction has been acknowledged.

Two levels are checked, and both are reported in ONE message so an admin never
fixes a permission only to discover the next missing one:

* guild level  - ``guild.me.guild_permissions`` for the GUILD_SCOPED
  permissions of the module (manage_roles, kick, ban, timeout...);
* channel level - ``channel.permissions_for(guild.me)`` in every channel the
  module is configured to use (``ModuleBase.channel_requirements(config)``),
  which also sees channel overwrites that deny the bot. The CHANNEL_SCOPED
  permissions (send, webhooks, reactions, pins, threads, slowmode...) are
  checked HERE, never at guild level: Discord checks them in the channel, so a
  grant on that channel alone is enough (see ``analyze_requirements``).
"""

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

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


# --------------------------------------------------------------------------- #
# Where does Discord check each permission?
#
# docs/guides/DISCORD_PERMISSIONS.md section 3 ("Per endpoint - what is
# checked, and WHERE"). The golden rule: never check stricter than Discord,
# never check in a different place than Discord.
# --------------------------------------------------------------------------- #

#: Checked IN THE CHANNEL (channel overwrites apply): a grant on the one
#: configured channel is enough, a server-wide grant is not when the channel
#: denies it. Create Message / Get Channel Message / Create Reaction / Pin
#: (PIN_MESSAGES) / Delete Message (MANAGE_MESSAGES) / webhooks
#: (MANAGE_WEBHOOKS) / Start Thread (CREATE_*_THREADS) / Modify Channel
#: (MANAGE_CHANNELS, slowmode) / Create Invite.
CHANNEL_SCOPED_PERMISSIONS = frozenset({
    "view_channel", "read_messages", "send_messages", "send_messages_in_threads",
    "read_message_history", "use_external_emojis", "external_emojis",
    "use_external_stickers", "external_stickers", "embed_links", "attach_files",
    "add_reactions", "mention_everyone", "send_tts_messages", "send_polls",
    "send_voice_messages", "manage_webhooks", "manage_messages", "pin_messages",
    "create_public_threads", "create_private_threads", "manage_threads",
    "manage_channels", "create_instant_invite", "connect", "speak", "move_members",
})

#: Checked AT GUILD LEVEL (no channel overwrite involved): Add/Remove Member
#: Role + Create/Modify Role (MANAGE_ROLES, also for overwrites on create
#: [observed]), Kick, Ban, Timeout, nicknames, Audit log, Modify Guild,
#: expressions. Anything not listed in either set is treated as guild level.
GUILD_SCOPED_PERMISSIONS = frozenset({
    "manage_roles", "kick_members", "ban_members", "moderate_members",
    "manage_nicknames", "change_nickname", "view_audit_log", "manage_guild",
    "manage_expressions", "manage_emojis", "manage_emojis_and_stickers",
    "create_expressions", "view_guild_insights", "administrator",
})

#: Name of a permission when the admin is editing a CHANNEL (docs: "'Manage
#: Permissions' refers to MANAGE_ROLES"): the channel screen has no "Manage
#: Roles" toggle.
_CHANNEL_CONTEXT_NAMES = {"manage_roles": "manage_permissions"}


def is_channel_scoped(key: str) -> bool:
    """True when Discord checks ``key`` in the channel, not at guild level."""
    return key in CHANNEL_SCOPED_PERMISSIONS


def split_by_scope(keys: Iterable[str]) -> Tuple[List[str], List[str]]:
    """``(guild_level_keys, channel_level_keys)``, order kept, duplicates dropped."""
    unique = merge_permissions(keys)
    return ([k for k in unique if k not in CHANNEL_SCOPED_PERMISSIONS],
            [k for k in unique if k in CHANNEL_SCOPED_PERMISSIONS])


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


def permission_name(key: str, locale: str, *, in_channel: bool = False) -> str:
    """Discord's own wording for a permission key.

    ``in_channel=True`` uses the channel-screen wording (MANAGE_ROLES is
    "Manage Permissions" there).
    """
    if in_channel:
        key = _CHANNEL_CONTEXT_NAMES.get(key, key)
    return t(f"modules.logs.permissions.{key}", locale=locale)


def bot_is_timed_out(guild) -> bool:
    """True when Moddy itself is timed out here (it then holds only
    VIEW_CHANNEL + READ_MESSAGE_HISTORY: DISCORD_PERMISSIONS.md section 1)."""
    me = getattr(guild, "me", None)
    check = getattr(me, "is_timed_out", None)
    try:
        return bool(check()) if callable(check) else False
    except Exception:  # noqa: BLE001 - stale member objects
        return False


def held_somewhere(guild, key: str) -> bool:
    """True when the bot holds ``key`` server-wide or in at least one channel
    it can see. A channel-scoped permission granted only on one channel is a
    valid setup (Discord checks it in the channel), so a module with no channel
    configured yet must not demand it server-wide."""
    me = getattr(guild, "me", None)
    if me is None:
        return False
    if getattr(me.guild_permissions, key, False):
        return True
    for channel in list(getattr(guild, "channels", None) or ()):
        if is_obfuscated(channel):
            continue
        try:
            perms = channel.permissions_for(me)
        except Exception:  # noqa: BLE001
            continue
        if perms.view_channel and getattr(perms, key, False):
            return True
    return False


def explicit_channel_deny(channel, member, key: str) -> bool:
    """True when an overwrite applying to ``member`` (@everyone, one of its
    roles, or the member) explicitly denies ``key`` in ``channel``.

    That is the case where a server-wide grant would be overridden: the admin
    must change the channel's own permissions. Meaningless (False) on an
    obfuscated channel, whose real overwrites are hidden.
    """
    if channel is None or member is None or is_obfuscated(channel):
        return False
    try:
        overwrites = channel.overwrites
        ids = {r.id for r in getattr(member, "roles", ())}
        ids.add(member.id)
        ids.add(channel.guild.id)
    except Exception:  # noqa: BLE001
        return False
    for target, overwrite in overwrites.items():
        if getattr(target, "id", None) not in ids:
            continue
        try:
            if getattr(overwrite.pair()[1], key, False):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def invite_permissions_for(keys: Iterable[str]) -> discord.Permissions:
    """Permissions for a re-invite link asking ONLY for ``keys`` (never
    Administrator, CLAUDE.md rule 12)."""
    perms = discord.Permissions.none()
    for key in keys:
        if key in ("administrator", "manage_permissions"):
            continue
        if hasattr(perms, key):
            setattr(perms, key, True)
    return perms


# --------------------------------------------------------------------------- #
# Whole-module analysis (what /config reports before opening a screen)
# --------------------------------------------------------------------------- #

@dataclass
class Gaps:
    """Everything the bot lacks for one module, computed at the right level."""

    timed_out: bool = False
    #: guild-level permissions (manage_roles, kick, ...) missing server-wide.
    guild: List[str] = field(default_factory=list)
    #: channel-scoped permissions the module needs but no configured channel
    #: covers, and the bot holds nowhere (module not configured yet).
    anywhere: List[str] = field(default_factory=list)
    #: ``{channel_id: [keys]}`` missing in a configured channel.
    channels: Dict[int, List[str]] = field(default_factory=dict)
    #: configured channels the bot cannot see (includes obfuscated ones).
    hidden: List[int] = field(default_factory=list)
    #: ``{channel_id: [keys]}`` where an overwrite explicitly denies the key.
    denied: Dict[int, List[str]] = field(default_factory=dict)
    #: configured channels that no longer exist.
    gone: List[int] = field(default_factory=list)

    @property
    def blocking(self) -> bool:
        """Gaps that make the module unusable whatever channel is picked."""
        return bool(self.timed_out or self.guild or self.anywhere)

    @property
    def any(self) -> bool:
        return bool(self.blocking or self.channels or self.gone)

    def invite_keys(self) -> List[str]:
        keys = merge_permissions(self.guild, self.anywhere, *self.channels.values())
        return [k for k in keys if k != "administrator"]


def analyze_requirements(
    guild,
    required: Sequence[str],
    channel_requirements: Optional[Mapping[int, Sequence[str]]] = None,
) -> Gaps:
    """Compute :class:`Gaps` for a module, checking each permission where
    Discord checks it.

    * guild-scoped keys of ``required`` -> guild level;
    * channel-scoped keys -> in each configured channel (``channel_requirements``
      says which keys per channel); a channel-scoped key no configured channel
      mentions is only reported when the bot holds it nowhere at all.
    Never raises; pure cache reads.
    """
    gaps = Gaps()
    me = getattr(guild, "me", None)
    if me is None:
        return gaps
    gaps.timed_out = bot_is_timed_out(guild)
    if gaps.timed_out:
        return gaps  # every other gap is a consequence of the timeout

    guild_keys, chan_keys = split_by_scope(required)
    gaps.guild = missing_guild_permissions(guild, guild_keys)

    covered: set = set()
    for raw_id, keys in (channel_requirements or {}).items():
        try:
            channel_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        channel = guild.get_channel_or_thread(channel_id)
        if channel is None:
            gaps.gone.append(channel_id)
            continue
        covered.update(keys)
        missing = channel_missing(channel, me, keys)
        if not missing:
            continue
        # View Channel comes first in ``missing``; the other bits are accurate
        # (permissions_for keeps them) so they are reported too: granting the
        # whole list leaves nothing to discover on the next attempt.
        if "view_channel" in missing:
            gaps.hidden.append(channel_id)
        gaps.channels[channel_id] = missing
        denied = [k for k in missing if k != "view_channel"
                  and explicit_channel_deny(channel, me, k)]
        if denied:
            gaps.denied[channel_id] = denied

    gaps.anywhere = [k for k in chan_keys
                     if k not in covered and not held_somewhere(guild, k)]
    return gaps


def role_assignment_problem(guild, role) -> Optional[str]:
    """Why Moddy cannot give ``role`` to members, or None when it can.

    ``"missing"`` (deleted), ``"everyone"``, ``"managed"`` (bot / integration /
    booster role: "Managed roles cannot be assigned"), ``"above"`` ("A bot can
    grant roles ... of a lower position than its own highest role").
    ``role`` is a role or an id.
    """
    if not hasattr(role, "managed"):
        try:
            role = guild.get_role(int(role))
        except (TypeError, ValueError):
            role = None
    if role is None:
        return "missing"
    if role.id == guild.id:
        return "everyone"
    if getattr(role, "managed", False):
        return "managed"
    me = getattr(guild, "me", None)
    top = getattr(me, "top_role", None)
    if top is not None and role >= top:
        return "above"
    return None


def role_assignment_error(guild, roles: Iterable, locale: str) -> Optional[str]:
    """Localized refusal naming every unusable role and the hierarchy fix
    (one message, all problems), or None when Moddy can assign them all."""
    by_problem: Dict[str, List[str]] = {}
    for role in dict.fromkeys(roles):
        problem = role_assignment_problem(guild, role)
        if problem:
            label = getattr(role, "mention", None) or f"<@&{role}>"
            by_problem.setdefault(problem, []).append(label)
    if not by_problem:
        return None
    me = getattr(guild, "me", None)
    top = getattr(me, "top_role", None)
    lines = [t(f"modules.config.errors.role_problem.{problem}", locale=locale,
               roles=", ".join(labels),
               bot_role=getattr(top, "mention", "Moddy"))
             for problem, labels in by_problem.items()]
    return "\n".join(lines)


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


def _channel_lines(mention: str, keys: Sequence[str], denied: Sequence[str],
                   locale: str) -> List[str]:
    """Bullets for the permissions missing in one channel + where to fix them."""
    tm = lambda key, **kw: t(f"modules.config.errors.missing_bot_perms.{key}",  # noqa: E731
                             locale=locale, **kw)
    lines = [f"• {permission_name(k, locale, in_channel=True)}" for k in keys]
    denied = [k for k in keys if k in denied and k != "view_channel"]
    others = [k for k in keys if k != "view_channel" and k not in denied]
    if "view_channel" in keys:
        lines.append("-# " + tm("hidden", channel=mention))
    if denied:
        lines.append("-# " + tm(
            "hint_denied", channel=mention,
            permissions=", ".join(permission_name(k, locale, in_channel=True) for k in denied)))
    if others:
        lines.append("-# " + tm("hint_channel", channel=mention))
    return lines


def format_gaps(gaps: Gaps, locale: str) -> str:
    """The message body: every gap, WHERE each one must be granted, and why a
    server-wide grant would not be enough when a channel overwrite denies it."""
    tm = lambda key, **kw: t(f"modules.config.errors.missing_bot_perms.{key}",  # noqa: E731
                             locale=locale, **kw)
    blocks: List[str] = []
    if gaps.timed_out:
        return tm("timed_out")
    if gaps.guild:
        blocks.append("\n".join([f"• {permission_name(k, locale)}" for k in gaps.guild]
                                + [f"-# {tm('hint_guild')}"]))
    if gaps.anywhere:
        blocks.append("\n".join([f"• {permission_name(k, locale)}" for k in gaps.anywhere]
                                + [f"-# {tm('hint_anywhere')}"]))
    for channel_id, keys in gaps.channels.items():
        mention = f"<#{channel_id}>"
        lines = [tm("in_channel", channel=mention)]
        lines += _channel_lines(mention, keys, gaps.denied.get(channel_id, ()), locale)
        blocks.append("\n".join(lines))
    for channel_id in gaps.gone:
        blocks.append(tm("gone", channel=f"<#{channel_id}>"))
    return "\n\n".join(blocks)


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
    mention = f"<#{channel.id}>"  # never channel.name: it may be "___hidden___"
    denied = [k for k in missing if k != "view_channel"
              and explicit_channel_deny(channel, me, k)]
    return t("modules.config.errors.channel_missing_perms", locale=locale,
             channel=mention,
             permissions="\n".join(_channel_lines(mention, missing, denied, locale)))


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
