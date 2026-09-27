"""
Voice transcription — the ``Transcribe`` message context menu.

Works on any message carrying audio (a Discord voice message first and
foremost, but any audio attachment too), everywhere the bot is reachable:
servers, DMs and user-installed contexts. The server-side automation — offering
a button under every voice message — is the ``voice_transcription`` module; both
share ``services/transcription_service.py``.

The answer is deliberately **public**: a transcription exists so the channel can
read a voice note, not just the person who asked. Only failures are private.

The menu also reads **images** (OCR, ``services/ocr_service.py``): Discord caps
apps at five message context menus and all five are taken, so a message with an
image and no audio gets its text extracted instead of a sixth "OCR" menu.
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
        # (Save Message, Get Emojis, Translate, AI text tools, Transcribe) —
        # which is why it also covers images (OCR) rather than a sixth menu.
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
        from services.transcription_service import (
            ErrorCode, TranscriptionError, find_audio_attachment,
        )
        from services.ocr_service import find_image_attachment

        # An image and no audio: read its text (OCR) instead.
        if find_audio_attachment(message) is None and find_image_attachment(message) is not None:
            await self._ocr_message(interaction, message)
            return

        # Errors are ephemeral (the clicker's language); the transcription
        # itself stays in the channel, so it speaks the server's.
        locale = i18n.get_user_locale(interaction)
        public_locale = await card_locale(interaction)
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

    async def _ocr_message(self, interaction: discord.Interaction, message: discord.Message):
        """Transcribe on an image: public OCR card, like a transcription."""
        from services.ocr_service import ErrorCode, OcrError, find_image_attachment
        from utils.ocr_views import (
            NO_MENTIONS as OCR_NO_MENTIONS, build_ocr_message,
            render_error_card as render_ocr_error,
            render_loading_card as render_ocr_loading,
        )
        locale = i18n.get_user_locale(interaction)
        public_locale = await card_locale(interaction)
        service = getattr(self.bot, "ocr", None)
        attachment = find_image_attachment(message)
        if service is None:
            await interaction.response.send_message(
                view=render_ocr_error(ErrorCode.UNAVAILABLE, locale), ephemeral=True)
            return
        try:
            service.preflight(attachment)
        except OcrError as exc:
            await interaction.response.send_message(
                view=render_ocr_error(exc.code, locale, **exc.params), ephemeral=True)
            return
        await interaction.response.send_message(view=render_ocr_loading(public_locale))
        try:
            result = await service.extract(
                attachment, user_id=interaction.user.id, guild_id=interaction.guild_id,
                source="context")
        except OcrError as exc:
            await interaction.edit_original_response(
                view=render_ocr_error(exc.code, locale, **exc.params))
            return
        view, files = build_ocr_message(result, locale=public_locale,
                                        requester_id=interaction.user.id)
        await interaction.edit_original_response(
            view=view, attachments=files, allowed_mentions=OCR_NO_MENTIONS)


async def setup(bot):
    await bot.add_cog(VoiceTranscription(bot))
