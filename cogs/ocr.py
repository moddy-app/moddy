"""
``/ocr`` — extract the text of an image (Google Vision document OCR).

Global command (servers, DMs, user installs). The image can also be OCR'd from
a message with the **Transcribe** context menu (``cogs/voice_transcription.py``):
Discord caps apps at five message context menus and all five are taken, so
"Transcribe" handles audio *and* images instead of a sixth menu.

The result is ephemeral by default (``incognito``) — it is the requester's
text, not the channel's. See docs/OCR.md.
"""

from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils.i18n import i18n
from utils.incognito import add_incognito_option, get_incognito_setting
from utils.ocr_views import (
    NO_MENTIONS, build_ocr_message, render_error_card, render_loading_card,
)

logger = logging.getLogger("moddy.ocr")


class Ocr(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="ocr", description="Extract the text of an image")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(image="The image to read", incognito="Make response visible only to you")
    @add_incognito_option()
    async def ocr_command(self, interaction: discord.Interaction, image: discord.Attachment,
                          incognito: Optional[bool] = None):
        from services.ocr_service import OcrError
        locale = i18n.get_user_locale(interaction)
        ephemeral = get_incognito_setting(interaction)
        service = getattr(self.bot, "ocr", None)
        if service is None:
            from services.ocr_service import ErrorCode
            await interaction.response.send_message(
                view=render_error_card(ErrorCode.UNAVAILABLE, locale), ephemeral=True)
            return
        try:
            service.preflight(image)
        except OcrError as exc:
            await interaction.response.send_message(
                view=render_error_card(exc.code, locale, **exc.params), ephemeral=True)
            return

        await interaction.response.send_message(view=render_loading_card(locale),
                                                ephemeral=ephemeral)
        try:
            result = await service.extract(
                image, user_id=interaction.user.id, guild_id=interaction.guild_id,
                source="slash")
        except OcrError as exc:
            await interaction.edit_original_response(
                view=render_error_card(exc.code, locale, **exc.params))
            return
        view, files = build_ocr_message(result, locale=locale,
                                        requester_id=None if ephemeral else interaction.user.id)
        await interaction.edit_original_response(view=view, attachments=files,
                                                 allowed_mentions=NO_MENTIONS)


async def setup(bot):
    await bot.add_cog(Ocr(bot))
