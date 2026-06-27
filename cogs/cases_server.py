"""
Server Sanctions command — ``/sanctions``.

Lets a server moderator browse every sanction issued in their server (scope =
this guild), with live filters (status, sanction type, period, user) and
pagination. Read-only, ephemeral. Internal Moddy-staff notes are never shown.

Access requires a moderation permission in the guild (ban / kick / timeout
members, or manage server / administrator).
"""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from staff.framework import design
from utils.i18n import t, get_locale
from utils.cases_views import CasesBrowserView

logger = logging.getLogger('moddy.cases_server')


def _is_guild_moderator(member: discord.Member) -> bool:
    """Whether a member has any moderation capability in the guild."""
    p = member.guild_permissions
    return any((
        p.administrator, p.manage_guild,
        p.ban_members, p.kick_members, p.moderate_members,
    ))


class CasesServerCog(commands.Cog):
    """Server command to browse sanctions issued in the guild."""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="sanctions",
        description="Browse the moderation sanctions issued in this server",
    )
    @app_commands.guild_only()
    async def sanctions_command(self, interaction: discord.Interaction):
        locale = get_locale(interaction)

        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                t("commands.cases.browser.guild_only", locale=locale), ephemeral=True)
            return

        if not _is_guild_moderator(interaction.user):
            await interaction.response.send_message(view=design.error(
                t("commands.cases.browser.permission_denied_title", locale=locale),
                t("commands.cases.browser.permission_denied", locale=locale),
            ), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            view = CasesBrowserView(
                self.bot,
                mode="server",
                viewer_id=interaction.user.id,
                locale=locale,
                scope_type="discord_guild",
                scope_id=interaction.guild.id,
            )
            await view.refresh()
            await interaction.followup.send(view=view, ephemeral=True)

        except Exception as e:
            logger.error(
                f"Error fetching sanctions for guild {interaction.guild.id}: {e}",
                exc_info=True,
            )
            await interaction.followup.send(
                t("commands.cases.error", locale=locale), ephemeral=True)


async def setup(bot):
    await bot.add_cog(CasesServerCog(bot))
