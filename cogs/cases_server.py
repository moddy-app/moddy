"""
Server Cases command — ``/cases``.

Lets a server moderator browse every sanction/case issued in their server
(scope = this guild), with live filters (status, sanction type, period, user)
and pagination, or jump straight to a case via its public reference. Read +
full case management (add/revoke sanction, comment, edit reason, close/reopen).
Internal Moddy-staff notes are never shown.

Access requires **Manage Messages** in the guild (or Administrator). Modifying
a case further requires the permission specific to the action (e.g. Ban Members
to add/lift a ban) — enforced inside the browser.
"""

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from staff.framework import design
from utils.i18n import t, get_locale
from utils.cases_views import CasesBrowserView, can_view_server_cases

logger = logging.getLogger('moddy.cases_server')


class CasesServerCog(commands.Cog):
    """Server command to browse cases issued in the guild."""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="cases",
        description="Browse the moderation cases issued in this server",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.describe(case="Optional: open a specific case directly by its reference (e.g. A7F2K9)")
    async def cases_command(self, interaction: discord.Interaction, case: Optional[str] = None):
        locale = get_locale(interaction)

        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                t("commands.cases.browser.guild_only", locale=locale), ephemeral=True)
            return

        if not can_view_server_cases(interaction.user):
            await interaction.response.send_message(view=design.error(
                t("commands.cases.browser.permission_denied_title", locale=locale),
                t("commands.cases.browser.permission_denied", locale=locale),
            ), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        # Unexpected errors propagate to the centralized app-command error
        # handler (see CLAUDE.md) instead of being masked by a generic message.
        view = CasesBrowserView(
            self.bot,
            mode="server",
            viewer_id=interaction.user.id,
            locale=locale,
            scope_type="discord_guild",
            scope_id=interaction.guild.id,
        )

        # Optional: jump straight to a case by reference.
        if case:
            opened = await view.open_reference(case.strip())
            if not opened:
                await interaction.followup.send(view=design.error(
                    t("commands.cases.browser.not_found_title", locale=locale),
                    t("commands.cases.browser.not_found", locale=locale, id=f"`{case.strip()}`"),
                ), ephemeral=True)
                return
            await interaction.followup.send(view=view, ephemeral=True)
            return

        await view.refresh()
        await interaction.followup.send(view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(CasesServerCog(bot))
