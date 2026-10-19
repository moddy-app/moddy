"""
Translate Button module — offers a translation under a message written in a
language the server does not speak.

When a member writes a message in a language that is neither the server's
language nor one usually spoken there, Moddy answers it with a tiny card — the
detected language and a **Translate** button. Whoever clicks gets the
translation privately, in their own language. Nothing is sent anywhere until a
click: detection is local and free (``tools/langdetect.py``).

A server's *usual* languages are learnt from the last two weeks of messages
(``services/translate_button_service.py``), so a bilingual community is never
nagged. Extra languages can be added by hand, channels ignored, and the
prompt's lifetime chosen in /config.

**Default-enabled** (``DEFAULT_ENABLED``): runs everywhere until a server pauses
it from /config -> Manage. See docs/features/TRANSLATE_BUTTON.md.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import discord

from modules.module_manager import ModuleBase
from services.translate_button_service import DEFAULT_DELETE_AFTER, DELETE_AFTER_OPTIONS
from tools import langdetect as ld
from utils.emojis import TRANSLATE

logger = logging.getLogger("moddy.modules.translate_button")

#: Discord caps a select at 25 values — the ignored channel list follows.
MAX_IGNORED_CHANNELS = 25

#: Message types worth looking at (a join notice or a pin has no prose).
_TEXT_TYPES = (discord.MessageType.default, discord.MessageType.reply)


def normalize_config(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Stored config -> a complete, valid one (unknown values dropped)."""
    config = dict(config or {})
    channels: List[int] = []
    raw_channels = config.get("ignored_channel_ids") or []
    if isinstance(raw_channels, list):
        for value in raw_channels:
            if str(value).isdigit() and int(value) not in channels:
                channels.append(int(value))
    delay = config.get("delete_after", DEFAULT_DELETE_AFTER)
    if isinstance(delay, bool) or delay not in DELETE_AFTER_OPTIONS:
        delay = DEFAULT_DELETE_AFTER
    raw_languages = config.get("extra_languages")
    return {**config,
            "ignored_channel_ids": channels[:MAX_IGNORED_CHANNELS],
            "extra_languages": ld.sanitize_languages(
                raw_languages if isinstance(raw_languages, list) else []),
            "delete_after": delay}


class TranslateButtonModule(ModuleBase):
    """Posts a Translate button under messages in a foreign language."""

    MODULE_ID = "translate_button"
    MODULE_NAME = "Translate Button"
    MODULE_DESCRIPTION = "Propose une traduction sous les messages dans une autre langue"
    MODULE_EMOJI = TRANSLATE
    MODULE_ORDER = 106
    DEFAULT_ENABLED = True
    # Replies in the channel the message was posted in; the prompt is deleted
    # by the bot itself (its own message), so no Manage Messages either.
    REQUIRED_BOT_PERMISSIONS: List[str] = []

    def __init__(self, bot, guild_id: int):
        super().__init__(bot, guild_id)
        self.ignored_channel_ids: List[int] = []
        self.extra_languages: List[str] = []
        self.delete_after: int = DEFAULT_DELETE_AFTER

    # ----------------------------------------------------------------- #
    # Configuration
    # ----------------------------------------------------------------- #

    def get_default_config(self) -> Dict[str, Any]:
        return {"ignored_channel_ids": [], "extra_languages": [],
                "delete_after": DEFAULT_DELETE_AFTER}

    async def load_config(self, config_data: Dict[str, Any]) -> bool:
        try:
            config = normalize_config(config_data)
            self.config = config
            self.ignored_channel_ids = config["ignored_channel_ids"]
            self.extra_languages = config["extra_languages"]
            self.delete_after = config["delete_after"]
            # Pause / resume is the generic module state (apply_state).
            self.enabled = True
            return True
        except Exception as e:  # noqa: BLE001
            logger.error(f"Error loading translate_button config: {e}")
            return False

    async def validate_config(self, config_data: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        channels = config_data.get("ignored_channel_ids") or []
        if not isinstance(channels, list) or not all(str(c).isdigit() for c in channels):
            return False, "La liste des salons est invalide"
        if len(channels) > MAX_IGNORED_CHANNELS:
            return False, f"Maximum {MAX_IGNORED_CHANNELS} salons"
        languages = config_data.get("extra_languages") or []
        if not isinstance(languages, list) or any(
                lang not in ld.SUPPORTED_LANGUAGES for lang in languages):
            return False, "Langues invalides"
        delay = config_data.get("delete_after", DEFAULT_DELETE_AFTER)
        if isinstance(delay, bool) or delay not in DELETE_AFTER_OPTIONS:
            return False, "Délai invalide"
        return True, None

    # ----------------------------------------------------------------- #
    # Runtime
    # ----------------------------------------------------------------- #

    def handles_channel(self, channel) -> bool:
        """False in an ignored channel (a thread follows its parent)."""
        ids = {getattr(channel, "id", None), getattr(channel, "parent_id", None)}
        return not (ids & set(self.ignored_channel_ids))

    async def on_message(self, message: discord.Message) -> None:
        if not self.enabled or message.guild is None:
            return
        # Members only: bots and webhooks (relays, logs) are not conversation.
        if message.author.bot or message.webhook_id is not None:
            return
        if message.type not in _TEXT_TYPES or not message.content:
            return
        if not self.handles_channel(message.channel):
            return

        service = getattr(self.bot, "translate_button", None)
        if service is None or not service.available():
            return

        text = ld.eligible_text(message.content)
        if text is None:
            return
        detection = await service.detect(text)
        if detection is None:
            return

        # Every confident detection is counted — known languages included — so
        # the shares the server's usual languages come from are representative.
        service.count(self.guild_id, detection.language)

        from utils.guild_language import guild_locale
        locale = await guild_locale(self.bot, message.guild)
        known = await service.known_languages(self.guild_id, locale, self.extra_languages)
        if detection.language in known:
            return

        me = message.guild.me
        if me is not None and not message.channel.permissions_for(me).send_messages:
            return

        if await service.offer(message, detection.language, locale, self.delete_after):
            self.count("prompt")
            stats = getattr(self.bot, "stats", None)
            if stats is not None:
                stats.incr("translate_button.prompted", dims={"language": detection.language})
