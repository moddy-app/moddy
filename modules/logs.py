"""Advanced server logs — module (configuration + routing).

The module owns the *configuration*: which channel receives which category of
events, which events are excluded inside a category, and the server-wide
ignore lists. It deliberately owns no Discord listener — those live in
``cogs/logs.py`` (wiring) and :mod:`serverlogs.listeners` (one file per
category, where the log messages are built).

Stored configuration (``guilds.data.modules.logs``)::

    {
      "categories": {
        "server":   {"channel_ids": [123], "disabled_events": ["user_kick"]},
        "messages": {"channel_ids": [456], "disabled_events": []}
      },
      "ignored_channel_ids": [789],
      "ignored_role_ids": [],
      "ignore_bots": false,
      "attach_transcripts": true,
      "locale": "auto"
    }

Only categories the admin actually bound to a channel are stored, so the
config stays small however many events the registry grows to.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import discord

from modules.module_manager import ModuleBase
from serverlogs import registry
from utils.emojis import NOTE

logger = logging.getLogger('moddy.modules.logs')

MODULE_ID = "logs"

#: How many channels a single category may fan out to.
MAX_CHANNELS_PER_CATEGORY = 3
#: How many channels/roles the server-wide ignore lists accept.
MAX_IGNORED_CHANNELS = 25
MAX_IGNORED_ROLES = 25


def _int_list(values: Any, limit: int) -> List[int]:
    """Coerce a stored JSON list into de-duplicated ints (snowflakes may be str)."""
    if not isinstance(values, (list, tuple)):
        return []
    out: List[int] = []
    for value in values:
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number not in out:
            out.append(number)
    return out[:limit]


class CategoryConfig:
    """Configuration of one category: its channels and its excluded events."""

    __slots__ = ("category_id", "channel_ids", "disabled_events")

    def __init__(self, category_id: str, channel_ids: Sequence[int],
                 disabled_events: Sequence[str]):
        self.category_id = category_id
        self.channel_ids: List[int] = list(channel_ids)
        self.disabled_events: Set[str] = set(disabled_events)

    @classmethod
    def from_raw(cls, category_id: str, raw: Any) -> "CategoryConfig":
        raw = raw if isinstance(raw, dict) else {}
        known = set(registry.CATEGORIES[category_id].events)
        disabled = [
            event for event in (raw.get("disabled_events") or [])
            if isinstance(event, str) and event in known
        ]
        return cls(
            category_id,
            _int_list(raw.get("channel_ids"), MAX_CHANNELS_PER_CATEGORY),
            disabled,
        )

    def to_raw(self) -> Dict[str, Any]:
        return {
            "channel_ids": list(self.channel_ids),
            "disabled_events": sorted(self.disabled_events),
        }

    def is_event_enabled(self, event: str) -> bool:
        return event not in self.disabled_events

    @property
    def enabled_count(self) -> int:
        total = len(registry.CATEGORIES[self.category_id].events)
        return total - len(self.disabled_events)

    @property
    def is_bound(self) -> bool:
        return bool(self.channel_ids)


class LogsModule(ModuleBase):
    """Server logs configuration holder and event router."""

    MODULE_ID = MODULE_ID
    MODULE_NAME = "Logs"
    MODULE_DESCRIPTION = "Detailed audit logs for everything that happens on the server"
    MODULE_EMOJI = NOTE
    MODULE_ORDER = 25

    def __init__(self, bot, guild_id: int):
        super().__init__(bot, guild_id)
        self.categories: Dict[str, CategoryConfig] = {}
        self.ignored_channel_ids: List[int] = []
        self.ignored_role_ids: List[int] = []
        self.ignore_bots: bool = False
        self.attach_transcripts: bool = True
        # "auto" follows the server's own language (guild.preferred_locale).
        self.locale: str = "auto"

    # ------------------------------------------------------------------ #
    # ModuleBase contract
    # ------------------------------------------------------------------ #

    def get_default_config(self) -> Dict[str, Any]:
        return {
            "categories": {},
            "ignored_channel_ids": [],
            "ignored_role_ids": [],
            "ignore_bots": False,
            "attach_transcripts": True,
            "locale": "auto",
        }

    async def load_config(self, config_data: Dict[str, Any]) -> bool:
        try:
            self.config = config_data or {}
            raw_categories = self.config.get("categories")
            raw_categories = raw_categories if isinstance(raw_categories, dict) else {}

            self.categories = {
                category_id: CategoryConfig.from_raw(category_id, raw)
                for category_id, raw in raw_categories.items()
                if category_id in registry.CATEGORIES
            }
            self.ignored_channel_ids = _int_list(
                self.config.get("ignored_channel_ids"), MAX_IGNORED_CHANNELS)
            self.ignored_role_ids = _int_list(
                self.config.get("ignored_role_ids"), MAX_IGNORED_ROLES)
            self.ignore_bots = bool(self.config.get("ignore_bots", False))
            self.attach_transcripts = bool(self.config.get("attach_transcripts", True))
            self.locale = str(self.config.get("locale") or "auto")

            # A logs configuration is "on" as soon as one category has a
            # destination — there is no separate on/off switch to forget.
            self.enabled = any(cat.is_bound for cat in self.categories.values())
            return True
        except Exception as e:
            logger.error(f"Error loading logs config for guild {self.guild_id}: {e}",
                         exc_info=True)
            return False

    async def validate_config(self, config_data: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """Check every bound channel exists and is writable by the bot."""
        from utils.i18n import t

        guild = self.bot.get_guild(self.guild_id) if self.bot else None
        locale = str(guild.preferred_locale) if guild and guild.preferred_locale else 'en-US'

        raw_categories = config_data.get("categories")
        if raw_categories is not None and not isinstance(raw_categories, dict):
            return False, t('modules.logs.config.errors.invalid_config', locale=locale)

        for category_id, raw in (raw_categories or {}).items():
            if category_id not in registry.CATEGORIES:
                return False, t('modules.logs.config.errors.unknown_category',
                                locale=locale, category=category_id)
            if not guild:
                continue
            for channel_id in _int_list((raw or {}).get("channel_ids"),
                                        MAX_CHANNELS_PER_CATEGORY):
                channel = guild.get_channel_or_thread(channel_id)
                if channel is None:
                    return False, t('modules.logs.config.errors.channel_not_found',
                                    locale=locale, channel_id=channel_id)
                permissions = channel.permissions_for(guild.me)
                if not (permissions.view_channel and permissions.send_messages
                        and permissions.embed_links):
                    return False, t('modules.logs.config.errors.missing_permissions',
                                    locale=locale, channel=channel.mention)
        return True, None

    # ------------------------------------------------------------------ #
    # Routing — read by the dispatcher on every event
    # ------------------------------------------------------------------ #

    def channels_for(self, event: str) -> List[int]:
        """Channel ids that should receive one *bare* event name.

        The same occurrence can belong to several categories (a ban is both a
        ``server`` and a ``moderation`` event); each bound category that
        declares the event and has not excluded it contributes its channels.
        """
        channel_ids: List[int] = []
        for key in registry.keys_for(event):
            spec = registry.EVENTS[key]
            category = self.categories.get(spec.category)
            if not category or not category.is_bound:
                continue
            if not category.is_event_enabled(spec.event):
                continue
            for channel_id in category.channel_ids:
                if channel_id not in channel_ids:
                    channel_ids.append(channel_id)
        return channel_ids

    def is_event_logged(self, event: str) -> bool:
        return bool(self.channels_for(event))

    def is_ignored_channel(self, channel: Optional[discord.abc.GuildChannel]) -> bool:
        """True when a channel — or its parent category/thread parent — is muted."""
        if channel is None or not self.ignored_channel_ids:
            return False
        candidates = {getattr(channel, "id", None)}
        parent = getattr(channel, "parent", None)
        if parent is not None:
            candidates.add(parent.id)
            candidates.add(getattr(getattr(parent, "category", None), "id", None))
        candidates.add(getattr(getattr(channel, "category", None), "id", None))
        return any(cid in self.ignored_channel_ids for cid in candidates if cid)

    def is_ignored_actor(self, user: Optional[discord.abc.User]) -> bool:
        """True when the subject/author of an event should never be logged."""
        if user is None:
            return False
        if self.ignore_bots and getattr(user, "bot", False):
            return True
        if self.ignored_role_ids:
            role_ids = {role.id for role in getattr(user, "roles", [])}
            if role_ids & set(self.ignored_role_ids):
                return True
        return False

    # ------------------------------------------------------------------ #
    # Config edition helpers (used by the /config panel)
    # ------------------------------------------------------------------ #

    def to_config(self) -> Dict[str, Any]:
        """Serialise the in-memory state back to the stored schema."""
        return {
            "categories": {
                category_id: category.to_raw()
                for category_id, category in self.categories.items()
                # A category with no channel left carries no information.
                if category.is_bound or category.disabled_events
            },
            "ignored_channel_ids": list(self.ignored_channel_ids),
            "ignored_role_ids": list(self.ignored_role_ids),
            "ignore_bots": self.ignore_bots,
            "attach_transcripts": self.attach_transcripts,
            "locale": self.locale,
        }

    def category(self, category_id: str) -> CategoryConfig:
        """Category config, created empty on first access."""
        if category_id not in self.categories:
            self.categories[category_id] = CategoryConfig(category_id, [], [])
        return self.categories[category_id]

    @property
    def bound_categories(self) -> List[str]:
        return [cid for cid, cat in self.categories.items() if cat.is_bound]


def config_from_raw(bot, guild_id: int, raw: Optional[Dict[str, Any]]) -> LogsModule:
    """Build a detached :class:`LogsModule` from a stored config (no DB write).

    Used by the ``/config`` panel, which edits a module instance rather than a
    loose dict so validation and routing rules live in exactly one place.
    """
    module = LogsModule(bot, guild_id)
    raw = raw if isinstance(raw, dict) and raw else module.get_default_config()
    # load_config is intentionally not awaited here: the panel is sync-built.
    # Mirror it manually — same normalisation, no side effect.
    module.config = raw
    raw_categories = raw.get("categories")
    raw_categories = raw_categories if isinstance(raw_categories, dict) else {}
    module.categories = {
        category_id: CategoryConfig.from_raw(category_id, value)
        for category_id, value in raw_categories.items()
        if category_id in registry.CATEGORIES
    }
    module.ignored_channel_ids = _int_list(raw.get("ignored_channel_ids"),
                                           MAX_IGNORED_CHANNELS)
    module.ignored_role_ids = _int_list(raw.get("ignored_role_ids"), MAX_IGNORED_ROLES)
    module.ignore_bots = bool(raw.get("ignore_bots", False))
    module.attach_transcripts = bool(raw.get("attach_transcripts", True))
    module.locale = str(raw.get("locale") or "auto")
    module.enabled = any(cat.is_bound for cat in module.categories.values())
    return module
