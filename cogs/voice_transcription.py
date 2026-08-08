"""
Voice transcription — the ``Transcribe`` message context menu.

Works on any message carrying audio (a Discord voice message first and
foremost, but any audio attachment too), everywhere the bot is reachable:
servers, DMs and user-installed contexts. The server-side automation — offering
a button under every voice message — is the ``voice_transcription`` module; both
share ``services/transcription_service.py``.

The answer is deliberately **public**: a transcription exists so the channel can
read a voice note, not just the person who asked. Only failures are private.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from utils.i18n import i18n
from utils.transcription_views import (
    NO_MENTIONS,
    build_transcription_message,
    card_locale,
    render_error_card,
    render_loading_card,
)

logger = logging.getLogger("moddy.voice_transcription")


class VoiceTranscription(commands.Cog):
    """Message context menu turning a voice message into readable text."""

    def __init__(self, bot):
        self.bot = bot

        # Discord allows 5 message context menus globally; this is the fifth
        # (Save Message, Get Emojis, Translate, AI text tools, Transcribe).
        self.transcribe_menu = app_commands.ContextMenu(
            name="Transcribe",
            callback=self.transcribe_context_menu,
            allowed_installs=app_commands.AppInstallationType(guild=True, user=True),
            allowed_contexts=app_commands.AppCommandContext(
                guild=True, dm_channel=True, private_channel=True
            ),
        )
        self.bot.tree.add_command(self.transcribe_menu)

    async def cog_unload(self):
        self.bot.tree.remove_command(
            self.transcribe_menu.name, type=self.transcribe_menu.type
        )

    async def transcribe_context_menu(self, interaction: discord.Interaction,
                                      message: discord.Message):
        from services.transcription_service import ErrorCode, TranscriptionError

        # Errors are ephemeral (the clicker's language); the transcription
        # itself stays in the channel, so it speaks the server's.
        locale = i18n.get_user_locale(interaction)
        public_locale = card_locale(interaction)
        service = getattr(self.bot, "transcription", None)

        if service is None:
            await interaction.response.send_message(
                view=render_error_card(ErrorCode.UNAVAILABLE, locale), ephemeral=True
            )
            return

        # Everything checkable without a network call happens before the public
        # message is sent, so "this message has no audio" never leaves a stray
        # card in the channel.
        try:
            service.preflight(message, requester_id=interaction.user.id)
        except TranscriptionError as exc:
            await interaction.response.send_message(
                view=render_error_card(exc.code, locale, **exc.params), ephemeral=True
            )
            return

        await interaction.response.send_message(view=render_loading_card(public_locale))

        try:
            result = await service.transcribe_message(
                message, requester_id=interaction.user.id
            )
        except TranscriptionError as exc:
            await interaction.edit_original_response(
                view=render_error_card(exc.code, locale, **exc.params)
            )
            return

        view, files = build_transcription_message(
            result, locale=public_locale, requester_id=interaction.user.id
        )
        await interaction.edit_original_response(
            view=view, attachments=files, allowed_mentions=NO_MENTIONS,
        )


async def setup(bot):
    await bot.add_cog(VoiceTranscription(bot))
