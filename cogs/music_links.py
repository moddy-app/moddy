"""
``/music link`` — the same song on every platform, from a Spotify link.

Same resolution as the ``music_links`` module (``services/music_links_service.py``)
but on demand and richer: cover, title, artist, album, year, duration, then a
button per platform, Spotify included. Ephemeral by default (``incognito``).

Playlists, episodes and shows have no equivalent elsewhere and are refused with
an explanation; when Deezer and Apple Music have no match the card still offers
the YouTube Music search. See docs/features/MUSIC_LINKS.md.
"""

from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from services.music_links_service import MusicLinksError
from tools import music as mu
from utils.i18n import i18n
from utils.incognito import add_incognito_option, get_incognito_setting
from utils.music_links_views import build_link_card
from utils.tool_card import NO_MENTIONS, ToolError, loading_card, respond, tool_error_card

logger = logging.getLogger("moddy.music_links")

MAX_INPUT_LENGTH = 300


class MusicLinks(commands.Cog):
    music_group = app_commands.Group(
        name="music",
        description="Music tools",
        allowed_installs=app_commands.AppInstallationType(guild=True, user=True),
        allowed_contexts=app_commands.AppCommandContext(
            guild=True, dm_channel=True, private_channel=True),
    )

    def __init__(self, bot):
        self.bot = bot

    @music_group.command(name="link", description="Find a Spotify song, album or artist on other platforms")
    @app_commands.describe(url="The Spotify link (a song, an album or an artist)")
    @add_incognito_option()
    async def link(self, interaction: discord.Interaction,
                   url: app_commands.Range[str, 1, MAX_INPUT_LENGTH],
                   incognito: Optional[bool] = None):
        locale = i18n.get_user_locale(interaction)
        ephemeral = get_incognito_setting(interaction)
        service = getattr(self.bot, "music_links", None)
        if service is None:
            await respond(interaction, view=tool_error_card("unavailable", locale, tool="music"))
            return

        # Cheap refusals first: nothing to wait for, so no loading card.
        parsed = mu.find_links(url, limit=1, include_unsupported=True)
        first = parsed[0] if parsed else None
        if first is None:
            await respond(interaction, view=tool_error_card("not_spotify", locale, tool="music"))
            return
        if first.type == mu.LINK_UNSUPPORTED:
            await respond(interaction, view=tool_error_card("unsupported", locale, tool="music"))
            return

        await interaction.response.send_message(view=loading_card(locale), ephemeral=ephemeral)
        try:
            ref = first.ref
            if first.type == mu.LINK_SHORT:
                ref = await service.resolve_short(first.url)
            info = await service.resolve(ref, source="command")
        except MusicLinksError as exc:
            view = tool_error_card(ToolError(exc.code, tool="music"), locale)
            await interaction.edit_original_response(view=view)
            return
        except Exception:
            # An unexpected bug: do not leave the spinner up, then let the
            # central handler report it.
            await interaction.edit_original_response(view=tool_error_card("failed", locale))
            raise

        requester = None if ephemeral else interaction.user.id
        await interaction.edit_original_response(
            view=build_link_card(info, locale, requested_by_id=requester),
            allowed_mentions=NO_MENTIONS)


async def setup(bot):
    await bot.add_cog(MusicLinks(bot))
