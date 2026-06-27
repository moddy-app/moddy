"""
Commandes slash pour le module Auto Restore Roles
"""

import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional
import logging

from utils.i18n import t

logger = logging.getLogger('moddy.cogs.auto_restore_roles_commands')


class AutoRestoreRolesCommands(commands.Cog):
    """
    Cog contenant les commandes slash pour Auto Restore Roles
    """

    def __init__(self, bot):
        self.bot = bot

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
        Supprime les rôles sauvegardés d'un utilisateur qui a quitté le serveur
        Permet aux admins d'éviter qu'un utilisateur récupère ses rôles s'il revient
        """
        locale = str(interaction.locale)

        # Vérifie que le module est activé
        if not self.bot.module_manager:
            await interaction.response.send_message(
                t('modules.auto_restore_roles.commands.error.no_manager', locale=locale),
                ephemeral=True
            )
            return

        try:
            # Récupère l'instance du module
            auto_restore_module = await self.bot.module_manager.get_module_instance(
                interaction.guild.id,
                'auto_restore_roles'
            )

            if not auto_restore_module:
                await interaction.response.send_message(
                    t('modules.auto_restore_roles.commands.error.not_configured', locale=locale),
                    ephemeral=True
                )
                return

            if not auto_restore_module.enabled:
                await interaction.response.send_message(
                    t('modules.auto_restore_roles.commands.error.not_enabled', locale=locale),
                    ephemeral=True
                )
                return

            # Vérifie si l'utilisateur a des rôles sauvegardés
            saved_info = await auto_restore_module.get_saved_roles_info(user.id)
            if not saved_info:
                await interaction.response.send_message(
                    t('modules.auto_restore_roles.commands.clear.no_roles', locale=locale, user=user.mention),
                    ephemeral=True
                )
                return

            # Récupère les informations sur les rôles sauvegardés
            role_count = len(saved_info['roles'])
            guild = interaction.guild

            # Crée un embed avec les informations
            embed = discord.Embed(
                title=f"<:history:1519796822963392755> {t('modules.auto_restore_roles.commands.clear.confirm_title', locale=locale)}",
                description=t('modules.auto_restore_roles.commands.clear.confirm_description', locale=locale, user=user.mention),
                color=0xFF5555
            )
            embed.add_field(
                name=t('modules.auto_restore_roles.commands.clear.user_field', locale=locale),
                value=f"{user.mention} (`{user.id}`)",
                inline=False
            )

            # Affiche les rôles qui seront supprimés
            role_mentions = []
            for role_id in saved_info['roles']:
                role = guild.get_role(role_id)
                if role:
                    role_mentions.append(role.mention)
                else:
                    role_mentions.append(f"<@&{role_id}> (supprimé)")

            embed.add_field(
                name=t('modules.auto_restore_roles.commands.clear.roles_field', locale=locale, count=role_count),
                value=", ".join(role_mentions) if role_mentions else t('modules.auto_restore_roles.commands.clear.no_roles_found', locale=locale),
                inline=False
            )

            embed.add_field(
                name=t('modules.auto_restore_roles.commands.clear.saved_at_field', locale=locale),
                value=f"<t:{int(discord.utils.parse_time(saved_info['saved_at']).timestamp())}:R>",
                inline=False
            )

            # Supprime les rôles sauvegardés
            success = await auto_restore_module.clear_saved_roles(user.id)

            if success:
                embed.color = 0x00FF00
                embed.title = f"<:done:1519800188925902881> {t('modules.auto_restore_roles.commands.clear.success_title', locale=locale)}"
                embed.description = t('modules.auto_restore_roles.commands.clear.success_description', locale=locale, user=user.mention)
                await interaction.response.send_message(embed=embed, ephemeral=True)
                logger.info(f"Cleared saved roles for user {user.id} in guild {interaction.guild.id} by admin {interaction.user.id}")
            else:
                await interaction.response.send_message(
                    t('modules.auto_restore_roles.commands.clear.error', locale=locale),
                    ephemeral=True
                )

        except Exception as e:
            logger.error(f"Error clearing saved roles: {e}", exc_info=True)
            await interaction.response.send_message(
                t('modules.auto_restore_roles.commands.error.internal', locale=locale),
                ephemeral=True
            )

    @app_commands.command(
        name="view-saved-roles",
        description="View all users with saved roles in this server"
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def view_saved_roles(self, interaction: discord.Interaction):
        """
        Affiche la liste de tous les utilisateurs qui ont des rôles sauvegardés
        """
        locale = str(interaction.locale)

        # Vérifie que le module est activé
        if not self.bot.module_manager:
            await interaction.response.send_message(
                t('modules.auto_restore_roles.commands.error.no_manager', locale=locale),
                ephemeral=True
            )
            return

        try:
            # Récupère l'instance du module
            auto_restore_module = await self.bot.module_manager.get_module_instance(
                interaction.guild.id,
                'auto_restore_roles'
            )

            if not auto_restore_module:
                await interaction.response.send_message(
                    t('modules.auto_restore_roles.commands.error.not_configured', locale=locale),
                    ephemeral=True
                )
                return

            if not auto_restore_module.enabled:
                await interaction.response.send_message(
                    t('modules.auto_restore_roles.commands.error.not_enabled', locale=locale),
                    ephemeral=True
                )
                return

            # Récupère les rôles sauvegardés
            all_saved_roles = await auto_restore_module.get_all_saved_roles()

            if not all_saved_roles:
                await interaction.response.send_message(
                    t('modules.auto_restore_roles.commands.view.no_saved_roles', locale=locale),
                    ephemeral=True
                )
                return

            # Crée un embed avec la liste
            saved_count = len(all_saved_roles)
            embed = discord.Embed(
                title=f"<:history:1519796822963392755> {t('modules.auto_restore_roles.commands.view.title', locale=locale)}",
                description=t('modules.auto_restore_roles.commands.view.description', locale=locale, count=saved_count),
                color=0x5865F2
            )

            # Ajoute chaque utilisateur
            guild = interaction.guild
            for saved_info in all_saved_roles:
                user_id = saved_info['user_id']
                username = saved_info.get('username', 'Unknown User')
                role_count = len(saved_info['roles'])
                saved_at = saved_info.get('saved_at')

                # Essaie de récupérer l'utilisateur
                try:
                    user = await self.bot.fetch_user(user_id)
                    user_display = f"{user.mention} (`{user.id}`)"
                except:
                    user_display = f"`{username}` (`{user_id}`)"

                field_value = t(
                    'modules.auto_restore_roles.commands.view.user_info',
                    locale=locale,
                    role_count=role_count
                )

                if saved_at:
                    timestamp = int(discord.utils.parse_time(saved_at).timestamp())
                    field_value += f"\n-# {t('modules.auto_restore_roles.commands.view.saved_at', locale=locale)} <t:{timestamp}:R>"

                embed.add_field(
                    name=user_display,
                    value=field_value,
                    inline=False
                )

            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Error viewing saved roles: {e}", exc_info=True)
            await interaction.response.send_message(
                t('modules.auto_restore_roles.commands.error.internal', locale=locale),
                ephemeral=True
            )


async def setup(bot):
    """Charge le cog"""
    await bot.add_cog(AutoRestoreRolesCommands(bot))
