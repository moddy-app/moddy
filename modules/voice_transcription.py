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
from utils.emojis import VOICE_CHAT
from utils.transcription_views import (
    NO_MENTIONS,
    TranscribePromptView,
    render_loading_card,
    render_transcription_card,
)

logger = logging.getLogger("moddy.modules.voice_transcription")

MODE_BUTTON = "button"
MODE_AUTO = "auto"
MODES = (MODE_BUTTON, MODE_AUTO)

# Discord caps a select at 25 options — the channel restriction list follows.
MAX_CHANNELS = 25


class VoiceTranscriptionModule(ModuleBase):
    """Offers a transcription under every voice message of the server."""

    MODULE_ID = "voice_transcription"
    MODULE_NAME = "Voice Transcription"
    MODULE_DESCRIPTION = "Transcrit les messages vocaux en texte"
    MODULE_EMOJI = VOICE_CHAT

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
            "enabled": True,
            "mode": MODE_BUTTON,
            "channel_ids": [],
        }

    async def load_config(self, config_data: Dict[str, Any]) -> bool:
        try:
            self.config = config_data
            self.mode = config_data.get("mode") or MODE_BUTTON
            if self.mode not in MODES:
                self.mode = MODE_BUTTON
            raw_channels = config_data.get("channel_ids") or []
            self.channel_ids = [int(c) for c in raw_channels if str(c).isdigit()]
            self.enabled = bool(config_data.get("enabled", False))
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
            perms = channel.permissions_for(guild.me)
            if not perms.send_messages:
                return False, (
                    f"Je n'ai pas la permission d'envoyer des messages dans "
                    f"{channel.mention}"
                )

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

        locale = self._locale()

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

        try:
            await placeholder.edit(
                view=render_transcription_card(
                    text=result.text,
                    locale=locale,
                    duration=result.duration,
                    language=result.language,
                    requester_id=None,  # nobody asked — it is automatic
                    truncated=result.truncated,
                ),
                allowed_mentions=NO_MENTIONS,
            )
        except discord.HTTPException as e:
            logger.warning(f"Could not post transcription in guild {self.guild_id}: {e}")

    # ----------------------------------------------------------------- #
    # Helpers
    # ----------------------------------------------------------------- #

    def _locale(self) -> str:
        """Server language — these messages are read by the whole channel."""
        try:
            guild = self.bot.get_guild(self.guild_id)
            return str(guild.preferred_locale) if guild and guild.preferred_locale else "en-US"
        except Exception:
            return "en-US"

    @staticmethod
    async def _delete_quietly(message: discord.Message) -> None:
        try:
            await message.delete()
        except discord.HTTPException:
            pass
