"""
Welcome DM module — welcome messages sent in private to new members.

Rework (Components V2)
----------------------
The module used to store a single ``message_template`` plus ~8 optional
``discord.Embed`` keys. It now mirrors ``modules/welcome_channel.py`` and stores
a **list** of welcome DMs (max ``MAX_WELCOME_DMS`` per guild, all users
combined), each one rendering as a Components V2 container:

  - a fully customizable message (placeholders such as ``{user}``, ``{server}``…),
  - an accent colour (the coloured bar on the left of the container),
  - its own enabled/paused switch.

Configuration lives in ``guilds.data.modules.welcome_dm`` (JSONB) — see
docs/WELCOME_DM.md for the full schema and the backend/dashboard contract.

Legacy (v1) configurations are migrated on read by :func:`normalize_config`, so
a guild that configured the old message + embed version keeps its text.
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

logger = logging.getLogger('moddy.modules.welcome_dm')


# --------------------------------------------------------------------------- #
# Limits & constants
# --------------------------------------------------------------------------- #
# Hard cap on welcome DMs per guild — ALL users combined, not per user. Lower
# than the channel module's cap on purpose: every entry is one more private
# message pushed to the same person the second they join.
# >>> KEEP IN SYNC WITH THE BACKEND (dashboard enforces the same cap) <<<
MAX_WELCOME_DMS = 3

# Max length of a single welcome DM template.
MAX_MESSAGE_LENGTH = 1500

# Accent colour used when a message defines none (Discord blurple).
DEFAULT_ACCENT_COLOR = 0x5865F2

# Current config schema version stored in the DB.
CONFIG_VERSION = 2

# Placeholders understood inside a welcome DM template. Substitution is done
# with plain ``str.replace`` (never ``str.format``) so a user typing a stray
# ``{`` in their message can never raise.
PLACEHOLDERS: Tuple[str, ...] = (
    "{server}",         # server name
    "{user}",           # user mention
    "{display_name}",   # user display name (nickname / global name)
    "{username}",       # user account name
    "{member_count}",   # member count after the join
    "{timestamp}",      # join time, unix seconds — use as <t:{timestamp}:R>
)


def get_default_message(locale: str = 'en-US') -> str:
    """The pre-filled welcome DM, translated.

    Fetched WITHOUT kwargs so the literal ``{placeholder}`` braces survive
    (``i18n.t`` only runs ``str.format`` when kwargs are passed).
    """
    return t('modules.welcome_dm.default_message', locale=locale)


def new_message_id() -> str:
    """Stable, collision-resistant id for a welcome DM entry."""
    return f"wdm_{secrets.token_hex(4)}"


# --------------------------------------------------------------------------- #
# Config normalization (v1 -> v2)
# --------------------------------------------------------------------------- #
def normalize_config(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return the v2 config shape, migrating a legacy v1 config if needed.

    v1 stored a single ``message_template`` plus embed_* keys. Its text becomes
    a one-entry ``messages`` list so an existing guild never loses its welcome
    DM; the embed title/description are folded into the message text (heading +
    body) since the V2 container has no separate title field. Pure function: it
    does not write anything back — the migrated shape is persisted the next time
    the guild saves from ``/config``.
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
    template = (raw.get('message_template') or '').strip()
    embed_enabled = bool(raw.get('embed_enabled'))
    embed_title = (raw.get('embed_title') or '').strip() if embed_enabled else ''
    embed_desc = (raw.get('embed_description') or '').strip() if embed_enabled else ''

    parts: List[str] = []
    if embed_title:
        parts.append(f"### {embed_title}")
    if template:
        parts.append(template)
    if embed_desc and embed_desc != template:
        parts.append(embed_desc)
    text = "\n".join(parts).strip()

    if not text:
        return {'version': CONFIG_VERSION, 'messages': []}

    return {
        'version': CONFIG_VERSION,
        'messages': [_normalize_entry({
            'id': new_message_id(),
            'message': text[:MAX_MESSAGE_LENGTH],
            'accent_color': raw.get('embed_color') if embed_enabled else None,
            'enabled': True,
        })],
    }


def _normalize_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Fill in the defaults of a single welcome-DM entry."""
    accent = entry.get('accent_color')
    if not isinstance(accent, int):
        accent = None
    return {
        'id': entry.get('id') or new_message_id(),
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
    """Build the Components V2 DM for one welcome entry.

    The container holds **only** the guild's own text — no bot-authored extras —
    inside an accent-coloured container. Returns ``(view, allowed_mentions)``.
    """
    text = format_message(entry.get('message') or '', member, guild).strip()

    view = ui.LayoutView(timeout=None)
    container = ui.Container(accent_colour=discord.Colour(entry_accent_color(entry)))
    container.add_item(ui.TextDisplay(text or member.mention))
    view.add_item(container)

    # A DM cannot mass-ping, but a guild's text could still carry @everyone or
    # a role mention: keep the same constraint as the channel module.
    allowed = discord.AllowedMentions(everyone=False, roles=False, users=[member])
    return view, allowed


# --------------------------------------------------------------------------- #
# Module
# --------------------------------------------------------------------------- #
class WelcomeDmModule(ModuleBase):
    """Sends one or more welcome DMs when a member joins the guild."""

    MODULE_ID = "welcome_dm"
    MODULE_NAME = "Welcome DM"
    MODULE_DESCRIPTION = "Welcome messages sent in private to new members"
    MODULE_EMOJI = WAVING_HAND
    MODULE_ORDER = 50

    def __init__(self, bot, guild_id: int):
        super().__init__(bot, guild_id)
        self.messages: List[Dict[str, Any]] = []

    async def load_config(self, config_data: Dict[str, Any]) -> bool:
        try:
            normalized = normalize_config(config_data)
            self.config = normalized
            self.messages = normalized['messages']
            # Active as soon as at least one message is enabled.
            self.enabled = any(m.get('enabled', True) for m in self.messages)
            return True
        except Exception as e:
            logger.error(f"Error loading welcome_dm config: {e}", exc_info=True)
            return False

    async def validate_config(self, config_data: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate the whole message list (count, ids, lengths, colours)."""
        locale = await self._locale()
        messages = normalize_config(config_data)['messages']

        if len(messages) > MAX_WELCOME_DMS:
            return False, t('modules.welcome_dm.errors.too_many',
                            locale=locale, max=MAX_WELCOME_DMS)

        seen_ids = set()
        for entry in messages:
            if entry['id'] in seen_ids:
                return False, t('modules.welcome_dm.errors.duplicate_id', locale=locale)
            seen_ids.add(entry['id'])

            message = (entry.get('message') or '').strip()
            if not message:
                return False, t('modules.welcome_dm.errors.empty_message', locale=locale)
            if len(message) > MAX_MESSAGE_LENGTH:
                return False, t('modules.welcome_dm.errors.message_too_long',
                                locale=locale, max=MAX_MESSAGE_LENGTH)

            accent = entry.get('accent_color')
            if accent is not None and (not isinstance(accent, int) or not 0 <= accent <= 0xFFFFFF):
                return False, t('modules.welcome_dm.errors.invalid_color', locale=locale)

        return True, None

    def get_default_config(self) -> Dict[str, Any]:
        return {'version': CONFIG_VERSION, 'messages': []}

    async def _locale(self) -> str:
        """Server language (/config -> Server settings), used for validation
        errors raised outside an interaction and for the DMs themselves."""
        from utils.guild_language import guild_locale
        return await guild_locale(self.bot, self.guild_id)

    async def on_member_join(self, member: discord.Member):
        """Send every enabled welcome DM to the joining member."""
        if not self.enabled:
            return

        guild = self.bot.get_guild(self.guild_id)
        if not guild:
            return

        for entry in self.messages:
            if not entry.get('enabled', True):
                continue
            try:
                view, allowed = build_welcome_view(entry, member, guild)
                await member.send(view=view, allowed_mentions=allowed)
                logger.info(
                    f"DM welcome sent to {member.id} (guild {self.guild_id}, "
                    f"message {entry['id']})"
                )
            except discord.Forbidden:
                # DMs are closed for this member: every other entry would fail
                # the same way, so stop here instead of hammering the API.
                logger.warning(
                    f"Cannot send DM welcome to {member.id} (guild {self.guild_id}) "
                    "— DMs disabled"
                )
                return
            except Exception as e:
                logger.error(f"Error sending DM welcome: {e}", exc_info=True)
