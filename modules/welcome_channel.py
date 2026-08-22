"""
Welcome Channel module — welcome messages posted in public channels.

Rework (Components V2)
----------------------
The module used to configure a single channel and an optional ``discord.Embed``.
It now stores a **list** of welcome messages (max ``MAX_WELCOME_MESSAGES`` per
guild, all users combined), each one rendering as a Components V2 container:

  - a fully customizable message (placeholders such as ``{user}``, ``{server}``…),
  - an accent colour (the coloured bar on the left of the container),
  - its own target channel,
  - an individual enabled/paused switch.

Configuration lives in ``guilds.data.modules.welcome_channel`` (JSONB) — see
docs/WELCOME_MESSAGES.md for the full schema and the backend/dashboard contract.

Legacy (v1) configurations are migrated on read by :func:`normalize_config`, so
a guild that configured the old single-channel + embed version keeps its channel
and its message text.
"""

import logging
import secrets
import time
from typing import Any, Dict, List, Optional, Tuple

import discord
from discord import ui

from modules.module_manager import ModuleBase
from utils.emojis import WAVING_HAND
from utils.i18n import t

logger = logging.getLogger('moddy.modules.welcome_channel')


# --------------------------------------------------------------------------- #
# Limits & constants
# --------------------------------------------------------------------------- #
# Hard cap on welcome messages per guild — ALL users combined, not per user.
# >>> KEEP IN SYNC WITH THE BACKEND (dashboard enforces the same cap) <<<
MAX_WELCOME_MESSAGES = 5

# Max length of a single welcome message template.
MAX_MESSAGE_LENGTH = 1500

# Accent colour used when a message defines none (Discord blurple).
DEFAULT_ACCENT_COLOR = 0x5865F2

# Current config schema version stored in the DB.
CONFIG_VERSION = 2

# Placeholders understood inside a welcome message template. Substitution is
# done with plain ``str.replace`` (never ``str.format``) so a user typing a
# stray ``{`` in their message can never raise.
PLACEHOLDERS: Tuple[str, ...] = (
    "{server}",         # server name
    "{user}",           # user mention
    "{display_name}",   # user display name (nickname / global name)
    "{username}",       # user account name
    "{member_count}",   # member count after the join
    "{timestamp}",      # join time, unix seconds — use as <t:{timestamp}:R>
)

# Channel types a welcome message may target.
CHANNEL_TYPES = [discord.ChannelType.text, discord.ChannelType.news]


def get_default_message(locale: str = 'en-US') -> str:
    """The pre-filled welcome message, translated.

    Fetched WITHOUT kwargs so the literal ``{placeholder}`` braces survive
    (``i18n.t`` only runs ``str.format`` when kwargs are passed).
    """
    return t('modules.welcome_channel.default_message', locale=locale)


def new_message_id() -> str:
    """Stable, collision-resistant id for a welcome message entry."""
    return f"wm_{secrets.token_hex(4)}"


# --------------------------------------------------------------------------- #
# Config normalization (v1 -> v2)
# --------------------------------------------------------------------------- #
def normalize_config(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return the v2 config shape, migrating a legacy v1 config if needed.

    v1 stored a single ``channel_id`` + ``message_template`` (+ embed_* keys
    that the rework drops). It becomes a one-entry ``messages`` list so an
    existing guild never loses its welcome channel. Pure function: it does not
    write anything back — the migrated shape is persisted the next time the
    guild saves from ``/config``.
    """
    if not raw:
        return {'version': CONFIG_VERSION, 'messages': []}

    messages = raw.get('messages')
    if isinstance(messages, list):
        return {
            'version': CONFIG_VERSION,
            'messages': [_normalize_entry(e) for e in messages if isinstance(e, dict)],
        }

    # --- legacy v1 ---
    channel_id = raw.get('channel_id')
    if not channel_id:
        return {'version': CONFIG_VERSION, 'messages': []}

    return {
        'version': CONFIG_VERSION,
        'messages': [_normalize_entry({
            'id': new_message_id(),
            'channel_id': channel_id,
            'message': raw.get('message_template') or get_default_message(),
            'accent_color': raw.get('embed_color') or DEFAULT_ACCENT_COLOR,
            'enabled': True,
        })],
    }


def _normalize_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Fill in the defaults of a single welcome-message entry."""
    accent = entry.get('accent_color')
    if not isinstance(accent, int):
        accent = None
    return {
        'id': entry.get('id') or new_message_id(),
        'channel_id': entry.get('channel_id'),
        'message': entry.get('message') or '',
        'accent_color': accent,
        'enabled': bool(entry.get('enabled', True)),
        'created_by': entry.get('created_by'),
        'created_at': entry.get('created_at'),
    }


def entry_accent_color(entry: Dict[str, Any]) -> int:
    """Accent colour of an entry, falling back to the module default."""
    accent = entry.get('accent_color')
    return accent if isinstance(accent, int) else DEFAULT_ACCENT_COLOR


# --------------------------------------------------------------------------- #
# Rendering (Components V2)
# --------------------------------------------------------------------------- #
def format_message(template: str, member: discord.Member,
                   guild: discord.Guild) -> str:
    """Substitute the supported placeholders inside a message template."""
    joined_at = getattr(member, 'joined_at', None)
    timestamp = int(joined_at.timestamp()) if joined_at else int(time.time())

    replacements = {
        "{server}": guild.name,
        "{user}": member.mention,
        "{display_name}": member.display_name,
        "{username}": member.name,
        "{member_count}": str(guild.member_count or 0),
        "{timestamp}": str(timestamp),
    }
    for key, value in replacements.items():
        template = template.replace(key, str(value))
    return template


def build_welcome_view(entry: Dict[str, Any], member: discord.Member,
                       guild: discord.Guild) -> Tuple[ui.LayoutView, discord.AllowedMentions]:
    """Build the Components V2 message for one welcome entry.

    The container holds **only** the guild's own text — no bot-authored extras —
    inside an accent-coloured container. Returns ``(view, allowed_mentions)``.
    """
    text = format_message(entry.get('message') or '', member, guild).strip()

    view = ui.LayoutView(timeout=None)
    container = ui.Container(accent_colour=discord.Colour(entry_accent_color(entry)))
    container.add_item(ui.TextDisplay(text or member.mention))
    view.add_item(container)

    # Only the joining member may be pinged: never roles, never @everyone.
    allowed = discord.AllowedMentions(everyone=False, roles=False, users=[member])
    return view, allowed


# --------------------------------------------------------------------------- #
# Module
# --------------------------------------------------------------------------- #
class WelcomeChannelModule(ModuleBase):
    """Posts one or more welcome messages when a member joins the guild."""

    MODULE_ID = "welcome_channel"
    MODULE_NAME = "Welcome Channel"
    MODULE_DESCRIPTION = "Welcome messages posted in public channels"
    MODULE_EMOJI = WAVING_HAND
    MODULE_ORDER = 40

    def __init__(self, bot, guild_id: int):
        super().__init__(bot, guild_id)
        self.messages: List[Dict[str, Any]] = []

    async def load_config(self, config_data: Dict[str, Any]) -> bool:
        try:
            normalized = normalize_config(config_data)
            self.config = normalized
            self.messages = normalized['messages']
            # Active as soon as at least one message is enabled and targets a channel.
            self.enabled = any(
                m.get('enabled', True) and m.get('channel_id') for m in self.messages
            )
            return True
        except Exception as e:
            logger.error(f"Error loading welcome_channel config: {e}", exc_info=True)
            return False

    async def validate_config(self, config_data: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate the whole message list (channels, permissions, lengths)."""
        guild = self.bot.get_guild(self.guild_id)
        if not guild:
            return False, t('modules.welcome_channel.errors.guild_not_found',
                            locale=self._locale())

        locale = self._locale()
        messages = normalize_config(config_data)['messages']

        if len(messages) > MAX_WELCOME_MESSAGES:
            return False, t('modules.welcome_channel.errors.too_many',
                            locale=locale, max=MAX_WELCOME_MESSAGES)

        seen_ids = set()
        for entry in messages:
            if entry['id'] in seen_ids:
                return False, t('modules.welcome_channel.errors.duplicate_id', locale=locale)
            seen_ids.add(entry['id'])

            channel_id = entry.get('channel_id')
            if not channel_id:
                return False, t('modules.welcome_channel.errors.channel_required', locale=locale)

            channel = guild.get_channel(channel_id)
            if not channel:
                return False, t('modules.welcome_channel.errors.channel_not_found', locale=locale)
            # Announcement channels are TextChannel subclasses in discord.py,
            # so this covers both types offered by the channel picker.
            if not isinstance(channel, discord.TextChannel):
                return False, t('modules.welcome_channel.errors.channel_type', locale=locale)

            perms = channel.permissions_for(guild.me)
            if not perms.send_messages or not perms.view_channel:
                return False, t('modules.welcome_channel.errors.no_send_permission',
                                locale=locale, channel=channel.mention)

            message = (entry.get('message') or '').strip()
            if not message:
                return False, t('modules.welcome_channel.errors.empty_message', locale=locale)
            if len(message) > MAX_MESSAGE_LENGTH:
                return False, t('modules.welcome_channel.errors.message_too_long',
                                locale=locale, max=MAX_MESSAGE_LENGTH)

            accent = entry.get('accent_color')
            if accent is not None and (not isinstance(accent, int) or not 0 <= accent <= 0xFFFFFF):
                return False, t('modules.welcome_channel.errors.invalid_color', locale=locale)

        return True, None

    def get_default_config(self) -> Dict[str, Any]:
        return {'version': CONFIG_VERSION, 'messages': []}

    def _locale(self) -> str:
        """Guild locale, used for validation errors raised outside an interaction."""
        try:
            guild = self.bot.get_guild(self.guild_id)
            if guild and guild.preferred_locale:
                return str(guild.preferred_locale)
        except Exception:
            pass
        return 'en-US'

    async def on_member_join(self, member: discord.Member):
        """Post every enabled welcome message for the joining member."""
        if not self.enabled:
            return

        guild = self.bot.get_guild(self.guild_id)
        if not guild:
            return

        for entry in self.messages:
            if not entry.get('enabled', True) or not entry.get('channel_id'):
                continue
            try:
                channel = guild.get_channel(entry['channel_id'])
                if not isinstance(channel, discord.TextChannel):
                    logger.warning(
                        f"Welcome channel {entry['channel_id']} not found or not a text "
                        f"channel (guild {self.guild_id})"
                    )
                    continue

                view, allowed = build_welcome_view(entry, member, guild)
                await channel.send(view=view, allowed_mentions=allowed)
                logger.info(
                    f"Channel welcome sent for {member.id} in channel {entry['channel_id']} "
                    f"(guild {self.guild_id}, message {entry['id']})"
                )
            except discord.Forbidden:
                logger.warning(
                    f"Missing permissions to send channel welcome in guild {self.guild_id} "
                    f"(channel {entry.get('channel_id')})"
                )
            except Exception as e:
                logger.error(f"Error sending channel welcome: {e}", exc_info=True)
