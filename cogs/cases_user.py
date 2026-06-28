"""
Personal Cases command — ``/mycases``.

Lets a member browse their own moderation cases (subject = their Discord
account) across every server, with live filters and pagination, or jump
straight to a case via its public reference. Internal staff notes are never
shown. Read-only, ephemeral.
"""

import logging
from typing import Optional

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

    @app_commands.command(name="mycases", description="Browse your moderation cases across all servers")
    @app_commands.describe(case="Optional: open a specific case directly by its reference (e.g. A7F2K9)")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def mycases_command(self, interaction: discord.Interaction, case: Optional[str] = None):
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

            # Optional: jump straight to a case by reference.
            if case:
                opened = await view.open_reference(case.strip())
                if not opened:
                    await interaction.followup.send(view=_not_found(locale, case), ephemeral=True)
                    return
                await interaction.followup.send(view=view, ephemeral=True)
                return

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


def _not_found(locale: str, reference: str) -> BaseView:
    view = BaseView()
    container = ui.Container(accent_colour=discord.Colour(COLORS["error"]))
    container.add_item(ui.TextDisplay(
        f"### {emojis.ERROR} {t('commands.cases.browser.not_found_title', locale=locale)}"
    ))
    container.add_item(ui.TextDisplay(
        t('commands.cases.browser.not_found', locale=locale, id=f"`{reference}`")))
    view.add_item(container)
    return view


async def setup(bot):
    await bot.add_cog(CasesUserCog(bot))
