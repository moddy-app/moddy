"""
Inspection tool: ``/sauce`` (where an image comes from). See
docs/features/TOOLS_INSPECT.md.

``/sauce`` runs SauceNAO and trace.moe in parallel through the gateway
(``services/sauce_service.py``). The image comes from an attachment or from a
link to a message of this server. There is no message context menu: Discord caps
an app at five and all five are taken (docs/features/VOICE_TRANSCRIPTION.md).
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from services import sauce_service as ss
from tools import sauce as sc
from utils.i18n import i18n
from utils.incognito import add_incognito_option, get_incognito_setting
from utils.tool_card import NO_MENTIONS, ToolError
from utils.tools_inspect_views import build_sauce_card, render_error, render_loading

logger = logging.getLogger("moddy.tools_inspect")

MESSAGE_LINK_RE = re.compile(
    r"https?://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/channels/"
    r"(?P<guild_id>\d+)/(?P<channel_id>\d+)/(?P<message_id>\d+)"
)


async def _card_locale(interaction: discord.Interaction, ephemeral: bool) -> str:
    """A public card speaks the server language; a private one the reader's."""
    if ephemeral or interaction.guild is None:
        return i18n.get_user_locale(interaction)
    from utils.transcription_views import card_locale
    return await card_locale(interaction)


class ToolsInspect(commands.Cog):
    """``/sauce``"""

    def __init__(self, bot):
        self.bot = bot

    # ------------------------------------------------------------------ #
    # /sauce
    # ------------------------------------------------------------------ #

    @app_commands.command(name="sauce", description="Find the source of an image")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(
        image="The image to search (PNG, JPEG, WebP or GIF)",
        message="Link to a message of this server containing the image",
        incognito="Make response visible only to you",
    )
    @add_incognito_option()
    async def sauce_command(self, interaction: discord.Interaction,
                            image: Optional[discord.Attachment] = None,
                            message: Optional[str] = None,
                            incognito: Optional[bool] = None):
        ephemeral = get_incognito_setting(interaction)
        locale = i18n.get_user_locale(interaction)
        if image is None and not message:
            await interaction.response.send_message(
                view=render_error("no_image", locale, "sauce"), ephemeral=True)
            return

        if image is not None:
            try:
                source = ss.from_attachment(image)
            except ToolError as exc:
                await interaction.response.send_message(
                    view=render_error(exc, locale, "sauce"), ephemeral=True)
                return
            origin = "slash"
        else:
            # Resolving the link needs a REST call: acknowledge first.
            await interaction.response.defer(ephemeral=ephemeral, thinking=True)
            try:
                source = await self._source_from_link(interaction, message)
            except ToolError as exc:
                await interaction.edit_original_response(view=render_error(exc, locale, "sauce"))
                return
            origin = "link"
        await self._run(interaction, source, ephemeral=ephemeral, origin=origin)

    @staticmethod
    def _nsfw(interaction: discord.Interaction) -> bool:
        return sc.is_nsfw_context(interaction.channel)

    async def _source_from_link(self, interaction: discord.Interaction,
                                link: str) -> ss.ImageSource:
        """The first image of a linked message. Only for a server channel the
        requester can read: the command must never look into a hidden channel."""
        match = MESSAGE_LINK_RE.search(link or "")
        if match is None:
            raise ToolError("bad_link", tool="sauce")
        guild = interaction.guild
        if guild is None or int(match["guild_id"]) != guild.id:
            raise ToolError("other_server", tool="sauce")
        channel = guild.get_channel_or_thread(int(match["channel_id"]))
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if channel is None or member is None:
            raise ToolError("gone", tool="sauce")
        perms = channel.permissions_for(member)
        if not (perms.view_channel and perms.read_message_history):
            raise ToolError("gone", tool="sauce")
        try:
            target = await channel.fetch_message(int(match["message_id"]))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
            raise ToolError("gone", tool="sauce") from exc
        source = ss.from_message(target)
        if source is None:
            raise ToolError("no_image", tool="sauce")
        return source

    def _available(self) -> bool:
        return any(ss.available(self.bot))

    async def _run(self, interaction: discord.Interaction, source: ss.ImageSource, *,
                   ephemeral: bool, origin: str) -> None:
        """Loading card -> search -> result card (errors stay private)."""
        err_locale = i18n.get_user_locale(interaction)
        if not self._available():
            await self._reply(interaction, render_error("unavailable", err_locale, "sauce"),
                              ephemeral=True)
            return
        locale = await _card_locale(interaction, ephemeral)
        if not interaction.response.is_done():
            await interaction.response.send_message(view=render_loading(locale, "sauce"),
                                                    ephemeral=ephemeral)
        else:
            await interaction.edit_original_response(view=render_loading(locale, "sauce"))
        try:
            result = await ss.search(
                self.bot, source, user_id=interaction.user.id, guild_id=interaction.guild_id,
                allow_explicit=self._nsfw(interaction), origin=origin)
        except ToolError as exc:
            await interaction.edit_original_response(view=render_error(exc, err_locale, "sauce"))
            return
        view = build_sauce_card(result, locale,
                                requester_id=None if ephemeral else interaction.user.id)
        await interaction.edit_original_response(view=view, allowed_mentions=NO_MENTIONS)

    @staticmethod
    async def _reply(interaction: discord.Interaction, view, *, ephemeral: bool) -> None:
        if interaction.response.is_done():
            await interaction.edit_original_response(view=view)
        else:
            await interaction.response.send_message(view=view, ephemeral=ephemeral)

    def _count(self, metric: str, **dims) -> None:
        stats = getattr(self.bot, "stats", None)
        if stats is None:
            return
        try:
            stats.incr(metric, dims=dims)
        except Exception:  # noqa: BLE001 — statistics never break a command
            pass


async def setup(bot):
    await bot.add_cog(ToolsInspect(bot))
