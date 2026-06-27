"""
User Cases command — ``/cases``.

Lets a member browse their own moderation cases (subject = their Discord
account) across every server, with live filters and pagination. Internal staff
notes are never shown. Read-only, ephemeral.
"""

import logging

import discord
from discord import app_commands, ui
from discord.ext import commands

from cogs.error_handler import BaseView
from config import COLORS
from utils import emojis
from utils.i18n import t, get_locale
from utils.cases_views import CasesBrowserView

logger = logging.getLogger('moddy.cases_user')


class CasesUserCog(commands.Cog):
    """User command to browse their own cases."""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="cases", description="Browse your moderation cases across all servers")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def cases_command(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        locale = get_locale(interaction)

        try:
            view = CasesBrowserView(
                self.bot,
                mode="user",
                viewer_id=interaction.user.id,
                locale=locale,
                subject_type="discord_user",
                subject_id=interaction.user.id,
            )
            await view.refresh()

            # Friendly empty state when the member has no cases at all.
            if view.total == 0 and not view._has_active_filters():
                empty = BaseView()
                container = ui.Container(accent_colour=discord.Colour(COLORS["success"]))
                container.add_item(ui.TextDisplay(
                    f"### {emojis.DONE} {t('commands.cases.empty_title', locale=locale)}"
                ))
                container.add_item(ui.TextDisplay(t('commands.cases.empty', locale=locale)))
                empty.add_item(container)
                await interaction.followup.send(view=empty, ephemeral=True)
                return

            await interaction.followup.send(view=view, ephemeral=True)

        except Exception as e:
            logger.error(f"Error fetching cases for user {interaction.user.id}: {e}", exc_info=True)
            await interaction.followup.send(
                t("commands.cases.error", locale=locale), ephemeral=True)


async def setup(bot):
    await bot.add_cog(CasesUserCog(bot))
