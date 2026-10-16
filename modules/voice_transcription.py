"""
Voice Transcription module — turns Discord voice messages into readable text.

When a member posts a voice message, the module answers it with either:

- ``button`` mode (default): a single "Transcribe" button, so the audio is only
  sent to the model when somebody actually wants to read it;
- ``auto`` mode: the transcription right away, no click needed.

The transcription itself is not implemented here — it lives in
``services/transcription_service.py`` and is shared with the ``Transcribe``
context menu, which works everywhere (including DMs) regardless of this module.
This module only decides *when* Moddy offers a transcription in a server.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import discord

from modules.module_manager import ModuleBase
from utils.bot_permissions import channel_map, channel_validation_error
from utils.emojis import VOICE_CHAT
from utils.transcription_views import (
    NO_MENTIONS,
    TranscribePromptView,
    build_transcription_message,
    render_loading_card,
)

logger = logging.getLogger("moddy.modules.voice_transcription")

MODE_BUTTON = "button"
MODE_AUTO = "auto"
MODES = (MODE_BUTTON, MODE_AUTO)

# Discord caps a select at 25 options — the channel restriction list follows.
MAX_CHANNELS = 25


def migrate_legacy_enabled(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Fold the retired ``enabled`` flag into the generic module state.

    ``enabled: false`` becomes ``state: "paused"`` (unless a state is already
    stored); the key is dropped so the next save writes the new shape.
    """
    config = dict(config or {})
    legacy = config.pop("enabled", None)
    if legacy is False and not config.get("state"):
        config["state"] = "paused"
    return config


class VoiceTranscriptionModule(ModuleBase):
    """Offers a transcription under every voice message of the server."""

    MODULE_ID = "voice_transcription"

    @classmethod
    def stored_state(cls, config_data: Optional[Dict[str, Any]]) -> str:
        """Generic state, reading a legacy ``enabled: false`` as paused."""
        return super().stored_state(migrate_legacy_enabled(config_data))

    MODULE_NAME = "Voice Transcription"
    MODULE_DESCRIPTION = "Transcrit les messages vocaux en texte"
    MODULE_EMOJI = VOICE_CHAT
    MODULE_ORDER = 110

    @classmethod
    def channel_requirements(cls, config):
        return channel_map(*(
            (channel_id, ["view_channel", "send_messages"])
            for channel_id in (config.get("channel_ids") or [])
        ))

    def __init__(self, bot, guild_id: int):
        super().__init__(bot, guild_id)
        self.mode: str = MODE_BUTTON
        # Empty list = every channel. Non-empty = only those channels.
        self.channel_ids: List[int] = []

    # ----------------------------------------------------------------- #
    # Configuration
    # ----------------------------------------------------------------- #

    def get_default_config(self) -> Dict[str, Any]:
        return {
            "mode": MODE_BUTTON,
            "channel_ids": [],
        }

    async def load_config(self, config_data: Dict[str, Any]) -> bool:
        try:
            config_data = migrate_legacy_enabled(config_data)
            self.config = config_data
            self.mode = config_data.get("mode") or MODE_BUTTON
            if self.mode not in MODES:
                self.mode = MODE_BUTTON
            raw_channels = config_data.get("channel_ids") or []
            self.channel_ids = [int(c) for c in raw_channels if str(c).isdigit()]
            # Pause / resume is the generic module state (apply_state).
            self.enabled = True
            return True
        except Exception as e:
            logger.error(f"Error loading voice_transcription config: {e}")
            return False

    async def validate_config(self, config_data: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        if config_data.get("mode") not in MODES:
            return False, "Mode de transcription invalide"

        channel_ids = config_data.get("channel_ids") or []
        if not isinstance(channel_ids, list):
            return False, "La liste des salons est invalide"
        if len(channel_ids) > MAX_CHANNELS:
            return False, f"Maximum {MAX_CHANNELS} salons"

        guild = self.bot.get_guild(self.guild_id) if self.bot else None
        if guild is None:
            return True, None

        for channel_id in channel_ids:
            channel = guild.get_channel(int(channel_id))
            if channel is None:
                return False, "Salon introuvable"
            from utils.guild_language import guild_locale
            error = channel_validation_error(
                channel, guild, ["view_channel", "send_messages"],
                await guild_locale(self.bot, guild))
            if error:
                return False, error

        return True, None

    # ----------------------------------------------------------------- #
    # Runtime
    # ----------------------------------------------------------------- #

    def handles_channel(self, channel_id: int) -> bool:
        """True when the module should answer in this channel."""
        return not self.channel_ids or channel_id in self.channel_ids

    async def on_message(self, message: discord.Message) -> None:
        """Offer (or post) a transcription under a voice message."""
        from services.transcription_service import (
            ErrorCode, TranscriptionError, is_voice_message,
        )

        if not self.enabled or message.author.bot:
            return
        if not is_voice_message(message):
            return
        if not self.handles_channel(message.channel.id):
            return

        service = getattr(self.bot, "transcription", None)
        if service is None or not service.available():
            return

        # Never reply where Moddy cannot speak — a Forbidden here would just be
        # noise in the logs on every voice message of a locked channel.
        me = message.guild.me if message.guild else None
        if me is not None and not message.channel.permissions_for(me).send_messages:
            return

        locale = await self._locale()

        try:
            if self.mode == MODE_BUTTON:
                await message.reply(
                    view=TranscribePromptView(message.channel.id, message.id, locale),
                    mention_author=False,
                    allowed_mentions=NO_MENTIONS,
                )
                return

            # Auto mode: post the loading card, then edit it with the result.
            placeholder = await message.reply(
                view=render_loading_card(locale),
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
        except discord.Forbidden:
            logger.debug(f"No permission to reply to a voice message in guild {self.guild_id}")
            return
        except discord.HTTPException as e:
            logger.warning(f"Could not reply to a voice message in guild {self.guild_id}: {e}")
            return

        try:
            result = await service.transcribe_message(
                message, requester_id=message.author.id
            )
        except TranscriptionError as exc:
            # An automatic transcription that fails should not shout about it in
            # the channel: drop the placeholder and stay quiet, except when the
            # feature is simply unavailable, which the logs already cover.
            logger.debug(
                f"Auto transcription failed in guild {self.guild_id}: {exc.code}"
            )
            if exc.code != ErrorCode.IN_PROGRESS:
                await self._delete_quietly(placeholder)
            return

        # requester_id stays None — nobody asked, it is automatic.
        view, files = build_transcription_message(result, locale=locale)
        try:
            await placeholder.edit(
                view=view, attachments=files, allowed_mentions=NO_MENTIONS,
            )
        except discord.HTTPException as e:
            logger.warning(f"Could not post transcription in guild {self.guild_id}: {e}")

    # ----------------------------------------------------------------- #
    # Helpers
    # ----------------------------------------------------------------- #

    async def _locale(self) -> str:
        """Server language — these messages are read by the whole channel.

        One setting for the whole server: /config -> Server settings.
        """
        from utils.guild_language import guild_locale
        return await guild_locale(self.bot, self.guild_id)

    @staticmethod
    async def _delete_quietly(message: discord.Message) -> None:
        try:
            await message.delete()
        except discord.HTTPException:
            pass
