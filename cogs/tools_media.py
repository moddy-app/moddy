"""
Media utility tools: ``/image`` and ``/convert``. See docs/features/TOOLS_MEDIA.md
and the shared contract in docs/features/TOOLS.md.

* ``/image`` shows a picture (preview, format, size, weight, transparency) with
  four buttons: Resize, Compress, Convert and Remove background. The card keeps
  the image as an attachment, so the persistent buttons re-read it from
  ``interaction.message`` and need no stored state.
* ``/convert`` detects what a file really is and offers only the formats it can
  become, grouped by kind. Almost everything is converted on Moddy; only layout
  office documents and ebooks go to CloudConvert (hidden without an API key).

The orchestration and every persistent component live in
``utils/tools_media_views.py``; the engines in ``services/media_tools_service.py``
and ``tools/``.
"""

from __future__ import annotations

import logging
from typing import List, Optional

import discord
from discord import app_commands
from discord.ext import commands

from services.media_tools_service import get_service
from tools.convert import formats
from utils import tools_media_views as views
from utils.incognito import add_incognito_option, get_incognito_setting

logger = logging.getLogger("moddy.tools_media")


class ToolsMedia(commands.Cog):
    """/image /convert"""

    def __init__(self, bot):
        self.bot = bot
        self.service = get_service(bot)

    async def cog_unload(self) -> None:
        await self.service.close()

    # ------------------------------------------------------------------ #
    # /image
    # ------------------------------------------------------------------ #

    @app_commands.command(name="image",
                          description="Preview an image, then resize, compress, convert it or remove its background")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(file="The image to work on (PNG, JPEG, WebP, GIF, HEIC, AVIF...)",
                           incognito="Make response visible only to you")
    @add_incognito_option()
    async def image_command(self, interaction: discord.Interaction, file: discord.Attachment,
                            incognito: Optional[bool] = None):
        await views.start_image(interaction, file, ephemeral=get_incognito_setting(interaction))

    # ------------------------------------------------------------------ #
    # /convert
    # ------------------------------------------------------------------ #

    @app_commands.command(name="convert",
                          description="Convert a file to another format (images, documents, audio, video, archives...)")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(file="The file to convert (max 25 MB)",
                           format="Target format (leave empty to pick from a list)",
                           incognito="Make response visible only to you")
    @add_incognito_option()
    async def convert_command(self, interaction: discord.Interaction, file: discord.Attachment,
                              format: Optional[app_commands.Range[str, 1, 20]] = None,
                              incognito: Optional[bool] = None):
        await views.start_convert(interaction, file, format,
                                  ephemeral=get_incognito_setting(interaction))

    @convert_command.autocomplete("format")
    async def convert_format_autocomplete(self, interaction: discord.Interaction,
                                          current: str) -> List[app_commands.Choice[str]]:
        needle = (current or "").strip().lower().lstrip(".")
        cloud = self.service.cloud_available()
        choices = []
        for key, fmt in formats.FORMATS.items():
            if not cloud and fmt.group == formats.EBOOK:
                continue
            if needle and needle not in fmt.label.lower() and needle not in fmt.ext \
                    and not any(needle in alias for alias in fmt.aliases):
                continue
            choices.append(app_commands.Choice(name=fmt.label, value=key))
        return choices[:25]


async def setup(bot):
    await bot.add_cog(ToolsMedia(bot))
