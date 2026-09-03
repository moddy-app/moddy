"""
Bump Reminder module — never miss a bump window again.

Server directories (DISBOARD, DiscordL, French.gg…) let a server climb back to
the top of their listing with a ``/bump`` command, reusable every one to four
hours. The window is trivial to miss, and a missed window is visibility lost.

This module watches the configured channel, recognises that a bump actually
**went through** (never merely that the command was run — a cooldown reply looks
almost identical), thanks whoever did it, and calls the channel back the moment
the command becomes available again.

Configuration lives in ``guilds.data.modules.bump_reminder`` (JSONB): one entry
per watched channel, capped **per directory** (one on a free server, three on a
premium one). The live half — what is pending right now — lives in the
``bump_reminders`` table, one row per *directory*, because a cooldown belongs to
the server and not to a channel: one bump arms every reminder that server has
for that directory. Nothing is held in memory, so a restart loses nothing.

The pieces:

- ``bumpreminder/``            — the pure detection core (registry + markers)
- ``cogs/bump_reminder.py``    — the listener and the sweeper loop
- ``utils/bump_views.py``      — the thank-you and reminder cards
- ``modules/configs/bump_reminder_config.py`` — the /config panel

See docs/BUMP_REMINDER.md.
"""

import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import discord

from bumpreminder import MAX_INTERVAL, MIN_INTERVAL, bot_by_key, detect
from modules.module_manager import ModuleBase
from utils.emojis import ROCKET_LAUNCH
from utils.i18n import t

logger = logging.getLogger('moddy.modules.bump_reminder')


# --------------------------------------------------------------------------- #
# Limits & constants
# --------------------------------------------------------------------------- #
CONFIG_VERSION = 1

# Reminders **per directory**. One is all a normal server needs: a bump puts the
# whole server on cooldown, so a second entry for the same listing only makes
# sense to call a *second channel* back — which is a large-server need, hence
# premium. The cap is per directory rather than a global total so that a free
# server can still cover every listing it bumps on.
FREE_REMINDERS_PER_BOT = 1
PREMIUM_REMINDERS_PER_BOT = 3

MAX_ROLE_MENTIONS = 5

# How the last bumper gets mentioned in the reminder.
#   auto   — always
#   button — only if they asked, via the button on the thank-you card
#   never  — the configured roles and nothing else
PING_MODES: Tuple[str, ...] = ("auto", "button", "never")
DEFAULT_PING_MODE = "button"

CHANNEL_TYPES = [discord.ChannelType.text, discord.ChannelType.news]


# --------------------------------------------------------------------------- #
# Config normalization
# --------------------------------------------------------------------------- #
def new_reminder_id() -> str:
    """Stable, collision-resistant id for one reminder entry."""
    return f"br_{secrets.token_hex(4)}"


def normalize_config(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return the stored shape with every key filled in.

    Pure: it never writes back. Entries naming a directory Moddy no longer
    supports are dropped here rather than crashing something downstream — a
    retired listing must not brick a server's whole panel.
    """
    if not raw:
        return {'version': CONFIG_VERSION, 'reminders': []}

    reminders = raw.get('reminders')
    if not isinstance(reminders, list):
        return {'version': CONFIG_VERSION, 'reminders': []}

    normalized = []
    for entry in reminders:
        if not isinstance(entry, dict):
            continue
        spec = bot_by_key(str(entry.get('bot') or ''))
        if spec is None:
            continue
        normalized.append(_normalize_entry(entry, spec))

    return {'version': CONFIG_VERSION, 'reminders': normalized}


def _normalize_entry(entry: Dict[str, Any], spec) -> Dict[str, Any]:
    interval = entry.get('interval')
    if not isinstance(interval, int) or not MIN_INTERVAL <= interval <= MAX_INTERVAL:
        interval = spec.default_interval

    ping_mode = entry.get('ping_mode')
    if ping_mode not in PING_MODES:
        ping_mode = DEFAULT_PING_MODE

    role_ids = entry.get('role_ids')
    if not isinstance(role_ids, list):
        role_ids = []

    return {
        'id': entry.get('id') or new_reminder_id(),
        'bot': spec.key,
        'channel_id': entry.get('channel_id'),
        'role_ids': [int(r) for r in role_ids if isinstance(r, (int, str)) and str(r).isdigit()][:MAX_ROLE_MENTIONS],
        'ping_mode': ping_mode,
        'interval': interval,
        'enabled': bool(entry.get('enabled', True)),
        'created_by': entry.get('created_by'),
        'created_at': entry.get('created_at'),
    }


def new_entry(bot_key: str, *, channel_id: int, role_ids: List[int],
              ping_mode: str, interval: int, created_by: Optional[int]) -> Dict[str, Any]:
    """Build a fresh entry, already normalized."""
    spec = bot_by_key(bot_key)
    return _normalize_entry({
        'id': new_reminder_id(),
        'bot': bot_key,
        'channel_id': channel_id,
        'role_ids': role_ids,
        'ping_mode': ping_mode,
        'interval': interval,
        'enabled': True,
        'created_by': created_by,
        'created_at': datetime.now(timezone.utc).isoformat(),
    }, spec)


async def reminders_per_bot(bot, guild_id: int) -> int:
    """How many reminders this guild may keep **per directory**.

    Uses the subscription helper, never a guild attribute — there is no PREMIUM
    guild attribute (docs/PREMIUM.md), and the helper is the Redis-cached path
    the config panel can afford to call on every render.
    """
    from utils.subscription import is_guild_premium
    return (PREMIUM_REMINDERS_PER_BOT if await is_guild_premium(bot, guild_id)
            else FREE_REMINDERS_PER_BOT)


def count_by_bot(reminders: List[Dict[str, Any]]) -> Dict[str, int]:
    """How many entries each directory already has."""
    counts: Dict[str, int] = {}
    for entry in reminders:
        counts[entry['bot']] = counts.get(entry['bot'], 0) + 1
    return counts


# --------------------------------------------------------------------------- #
# Module
# --------------------------------------------------------------------------- #
class BumpReminderModule(ModuleBase):
    """Detects successful bumps and schedules the follow-up reminder."""

    MODULE_ID = "bump_reminder"
    MODULE_NAME = "Bump Reminder"
    MODULE_DESCRIPTION = "Thanks whoever bumps and calls the channel back when it can be bumped again"
    MODULE_EMOJI = ROCKET_LAUNCH
    MODULE_ORDER = 85

    def __init__(self, bot, guild_id: int):
        super().__init__(bot, guild_id)
        self.reminders: List[Dict[str, Any]] = []
        # (channel_id, bot_key) -> entry, rebuilt on every load. Keyed on the
        # pair, not the channel: one #bump channel hosting DISBOARD *and*
        # DiscordL is the normal case, not the exception. The listener runs for
        # every message a directory posts, so this has to be a dict lookup.
        self._watch: Dict[Tuple[int, str], Dict[str, Any]] = {}

    async def load_config(self, config_data: Dict[str, Any]) -> bool:
        try:
            normalized = normalize_config(config_data)
            self.config = normalized
            self.reminders = normalized['reminders']
            self._watch = {
                (entry['channel_id'], entry['bot']): entry
                for entry in self.reminders
                if entry.get('enabled', True) and entry.get('channel_id')
            }
            self.enabled = bool(self._watch)
            return True
        except Exception as e:
            logger.error(f"Error loading bump_reminder config: {e}", exc_info=True)
            return False

    async def validate_config(self, config_data: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        guild = self.bot.get_guild(self.guild_id)
        locale = await self._locale()
        if not guild:
            return False, t('modules.bump_reminder.errors.guild_not_found', locale=locale)

        reminders = normalize_config(config_data)['reminders']

        cap = await reminders_per_bot(self.bot, self.guild_id)
        for bot_key, count in count_by_bot(reminders).items():
            if count > cap:
                spec = bot_by_key(bot_key)
                return False, t('modules.bump_reminder.errors.quota',
                                locale=locale, name=spec.name if spec else bot_key,
                                max=cap)

        seen_ids = set()
        seen_targets = set()
        for entry in reminders:
            spec = bot_by_key(entry['bot'])
            if spec is None:
                return False, t('modules.bump_reminder.errors.unknown_bot', locale=locale)

            if entry['id'] in seen_ids:
                return False, t('modules.bump_reminder.errors.duplicate_id', locale=locale)
            seen_ids.add(entry['id'])

            channel_id = entry.get('channel_id')
            if not channel_id:
                return False, t('modules.bump_reminder.errors.channel_required', locale=locale)

            channel = guild.get_channel(channel_id)
            if not channel:
                return False, t('modules.bump_reminder.errors.channel_not_found', locale=locale)
            if not isinstance(channel, discord.TextChannel):
                return False, t('modules.bump_reminder.errors.channel_type', locale=locale)

            perms = channel.permissions_for(guild.me)
            if not perms.view_channel or not perms.send_messages:
                return False, t('modules.bump_reminder.errors.no_send_permission',
                                locale=locale, channel=channel.mention)

            # Two reminders for the same directory in the same channel would
            # post the same card twice, every time.
            target = (entry['bot'], channel_id)
            if target in seen_targets:
                return False, t('modules.bump_reminder.errors.duplicate_target',
                                locale=locale, name=spec.name, channel=channel.mention)
            seen_targets.add(target)

            if len(entry['role_ids']) > MAX_ROLE_MENTIONS:
                return False, t('modules.bump_reminder.errors.too_many_roles',
                                locale=locale, max=MAX_ROLE_MENTIONS)

            interval = entry.get('interval')
            if not isinstance(interval, int) or not MIN_INTERVAL <= interval <= MAX_INTERVAL:
                return False, t('modules.bump_reminder.errors.invalid_interval', locale=locale)

        return True, None

    def get_default_config(self) -> Dict[str, Any]:
        return {'version': CONFIG_VERSION, 'reminders': []}

    async def _locale(self) -> str:
        """Server language — this module only ever writes to a whole channel."""
        from utils.guild_language import guild_locale
        return await guild_locale(self.bot, self.guild_id)

    async def on_external_config_change(self, action: str) -> Dict[str, Any]:
        """Drop pending rows the config no longer accounts for.

        The module posts no persistent panel, so there is nothing to repair in
        Discord — but a reminder whose entry was deleted from the dashboard must
        not still fire an hour later.
        """
        try:
            keys = {entry['bot'] for entry in self.reminders}
            states = await self.bot.db.get_guild_bump_states(self.guild_id)
            for stale in set(states) - keys:
                await self.bot.db.drop_bump_reminder(self.guild_id, stale)
        except Exception as e:
            logger.error(f"Error syncing bump reminders after {action}: {e}", exc_info=True)
        return {}

    # ----------------------------------------------------------------- events
    def entry_for(self, channel_id: int, bot_key: str) -> Optional[Dict[str, Any]]:
        """The reminder watching this directory in this channel, if any."""
        return self._watch.get((channel_id, bot_key))

    def entries_for_bot(self, bot_key: str) -> List[Dict[str, Any]]:
        """Every enabled reminder for one directory.

        A bump puts the **whole server** on that directory's cooldown, not one
        channel — so one detected bump is owed to every channel the server
        pointed at that directory, not only the one the reply landed in. That is
        the entire point of a premium server configuring three of them.
        """
        return [entry for (_, key), entry in self._watch.items() if key == bot_key]

    async def on_message(self, message: discord.Message, spec) -> Optional[Dict[str, Any]]:
        """Read a successful bump out of a directory's reply.

        ``spec`` is the directory the cog already resolved from the author, so
        the guard here is a single dict lookup on the pair the config actually
        keys on. A bump run in a channel this server did not point at is
        ignored on purpose: the reminder belongs to a channel, and answering in
        one the server never chose would be Moddy talking out of turn.

        Returns the hit and its entry for the cog to act on, or ``None``. It
        sends nothing itself, which is what keeps the decision testable.
        """
        entry = self._watch.get((message.channel.id, spec.key))
        if entry is None:
            return None

        hit = detect(message, entry['interval'])
        if hit is None:
            return None

        return {'hit': hit, 'entry': entry}
