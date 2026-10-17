"""
Commandes slash pour le module Auto Restore Roles
"""

import discord
from discord import app_commands, ui
from discord.ext import commands
from typing import Optional
import logging

from config import COLORS
from utils.emojis import EMOJIS
from utils.errors import ModdyError, ModuleDisabled, Unavailable
from utils.i18n import t, i18n

logger = logging.getLogger('moddy.cogs.auto_restore_roles_commands')


class AutoRestoreRolesCommands(commands.Cog):
    """
    Cog contenant les commandes slash pour Auto Restore Roles
    """

    def __init__(self, bot):
        self.bot = bot

    async def _get_module(self, interaction: discord.Interaction):
        """Return the active module instance or raise an expected error."""
        if not self.bot.module_manager:
            raise Unavailable()
        module = await self.bot.module_manager.get_module_instance(
            interaction.guild.id, 'auto_restore_roles'
        )
        if not module or not module.enabled:
            raise ModuleDisabled("Auto Restore Roles")
        return module

    @staticmethod
    def _card(title: str, accent: int, description: str, fields: list) -> ui.LayoutView:
        container = ui.Container(accent_colour=discord.Colour(accent))
        container.add_item(ui.TextDisplay(f"### {title}\n{description}"))
        if fields:
            container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
            for name, value in fields:
                container.add_item(ui.TextDisplay(f"**{name}**\n{value}"))
        view = ui.LayoutView()
        view.add_item(container)
        return view

    @app_commands.command(
        name="clear-saved-roles",
        description="Clear saved roles for a user who left the server"
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        user="The user whose saved roles should be cleared"
    )
    async def clear_saved_roles(
        self,
        interaction: discord.Interaction,
        user: discord.User
    ):
        """
        Clear the saved roles of a user who left the server, so they do not
        get them back if they return.
        """
        locale = i18n.get_user_locale(interaction)
        module = await self._get_module(interaction)

        saved_info = await module.get_saved_roles_info(user.id)
        if not saved_info:
            raise ModdyError(
                t('modules.auto_restore_roles.commands.clear.no_roles', locale=locale, user=user.mention),
                severity="info", icon="",
            )

        guild = interaction.guild
        role_mentions = []
        for role_id in saved_info['roles']:
            role = guild.get_role(role_id)
            role_mentions.append(role.mention if role else f"<@&{role_id}>")

        if not await module.clear_saved_roles(user.id):
            raise ModdyError(
                t('modules.auto_restore_roles.commands.clear.error', locale=locale),
                icon="",
            )

        ts = int(discord.utils.parse_time(saved_info['saved_at']).timestamp())
        view = self._card(
            f"{EMOJIS['done']} {t('modules.auto_restore_roles.commands.clear.success_title', locale=locale)}",
            COLORS["success"],
            t('modules.auto_restore_roles.commands.clear.success_description', locale=locale, user=user.mention),
            [
                (t('modules.auto_restore_roles.commands.clear.user_field', locale=locale),
                 f"{user.mention} (`{user.id}`)"),
                (t('modules.auto_restore_roles.commands.clear.roles_field', locale=locale,
                   count=len(saved_info['roles'])),
                 ", ".join(role_mentions)
                 or t('modules.auto_restore_roles.commands.clear.no_roles_found', locale=locale)),
                (t('modules.auto_restore_roles.commands.clear.saved_at_field', locale=locale), f"<t:{ts}:R>"),
            ],
        )
        await interaction.response.send_message(view=view, ephemeral=True)
        logger.info(f"Cleared saved roles for user {user.id} in guild {guild.id} by admin {interaction.user.id}")

    @app_commands.command(
        name="view-saved-roles",
        description="View all users with saved roles in this server"
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def view_saved_roles(self, interaction: discord.Interaction):
        """List every user who has saved roles."""
        locale = i18n.get_user_locale(interaction)
        module = await self._get_module(interaction)

        all_saved_roles = await module.get_all_saved_roles()
        if not all_saved_roles:
            raise ModdyError(
                t('modules.auto_restore_roles.commands.view.no_saved_roles', locale=locale),
                severity="info", icon="",
            )

        lines = []
        shown = 0
        for saved_info in all_saved_roles:
            user_id = saved_info['user_id']
            username = saved_info.get('username', 'Unknown User')
            try:
                user = await self.bot.fetch_user(user_id)
                user_display = f"{user.mention} (`{user.id}`)"
            except discord.HTTPException:
                user_display = f"`{username}` (`{user_id}`)"

            line = f"**{user_display}**\n" + t(
                'modules.auto_restore_roles.commands.view.user_info',
                locale=locale, role_count=len(saved_info['roles'])
            )
            if saved_info.get('saved_at'):
                ts = int(discord.utils.parse_time(saved_info['saved_at']).timestamp())
                line += f"\n-# {t('modules.auto_restore_roles.commands.view.saved_at', locale=locale)} <t:{ts}:R>"
            # A Components V2 message holds at most 4000 characters of text
            if sum(len(x) for x in lines) + len(line) > 3000:
                break
            lines.append(line)
            shown += 1

        container = ui.Container(accent_colour=discord.Colour(COLORS["primary"]))
        container.add_item(ui.TextDisplay(
            f"### {EMOJIS['history']} {t('modules.auto_restore_roles.commands.view.title', locale=locale)}\n"
            + t('modules.auto_restore_roles.commands.view.description', locale=locale,
                count=len(all_saved_roles))
        ))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(ui.TextDisplay("\n\n".join(lines)))
        view = ui.LayoutView()
        view.add_item(container)
        await interaction.response.send_message(view=view, ephemeral=True)


async def setup(bot):
    """Load the cog."""
    cog = AutoRestoreRolesCommands(bot)
    await bot.add_cog(cog)
    # Published only in servers where the module is active.
    bot.register_module_commands('auto_restore_roles', list(cog.get_app_commands()))
