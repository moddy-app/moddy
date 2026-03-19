"""
Configuration UI pour le module Auto Role
Interface pour configurer l'attribution automatique de rôles
"""

import discord
from discord import ui
from typing import Optional, Dict, Any
import logging

from utils.i18n import t
from cogs.error_handler import BaseView
from utils.emojis import MANAGE_USER, BACK, SAVE, UNDONE, DELETE

logger = logging.getLogger('moddy.modules.auto_role_config')


class AutoRoleConfigView(BaseView):
    """
    Interface de configuration du module Auto Role
    """

    def __init__(self, bot, guild_id: int, user_id: int, locale: str, current_config: Optional[Dict[str, Any]] = None):
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id
        self.locale = locale

        # Load default config
        from modules.auto_role import AutoRoleModule
        default_config = AutoRoleModule(bot, guild_id).get_default_config()

        # Check if we have a real saved config (check for any configured roles)
        if current_config and (
            current_config.get('member_roles') or
            current_config.get('bot_roles')
        ):
            # Merge with defaults to ensure all keys exist
            self.current_config = default_config.copy()
            self.current_config.update(current_config)
            self.has_existing_config = True
        else:
            # Use default config
            self.current_config = default_config
            self.has_existing_config = False

        # Working copy
        self.working_config = self.current_config.copy()
        self.has_changes = False

        self._build_view()

    def _build_view(self):
        """Construit l'interface de configuration"""
        self.clear_items()

        container = ui.Container()

        # Header
        container.add_item(ui.TextDisplay(
            f"### {MANAGE_USER} {t('modules.auto_role.config.title', locale=self.locale)}"
        ))
        container.add_item(ui.TextDisplay(
            t('modules.auto_role.config.description', locale=self.locale)
        ))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Member roles selector
        container.add_item(ui.TextDisplay(
            f"**{t('modules.auto_role.config.member_roles.section_title', locale=self.locale)}**\n"
            f"-# {t('modules.auto_role.config.member_roles.section_description', locale=self.locale)}"
        ))

        member_roles_row = ui.ActionRow()
        member_roles_select = ui.RoleSelect(
            placeholder=t('modules.auto_role.config.member_roles.placeholder', locale=self.locale),
            min_values=0,
            max_values=25
        )

        # Pre-select current member roles
        if self.working_config.get('member_roles'):
            guild = self.bot.get_guild(self.guild_id)
            if guild:
                default_roles = []
                for role_id in self.working_config['member_roles']:
                    role = guild.get_role(role_id)
                    if role:
                        default_roles.append(role)
                if default_roles:
                    member_roles_select.default_values = default_roles

        member_roles_select.callback = self.on_member_roles_select
        member_roles_row.add_item(member_roles_select)
        container.add_item(member_roles_row)

        # Bot roles selector
        container.add_item(ui.TextDisplay(
            f"**{t('modules.auto_role.config.bot_roles.section_title', locale=self.locale)}**\n"
            f"-# {t('modules.auto_role.config.bot_roles.section_description', locale=self.locale)}"
        ))

        bot_roles_row = ui.ActionRow()
        bot_roles_select = ui.RoleSelect(
            placeholder=t('modules.auto_role.config.bot_roles.placeholder', locale=self.locale),
            min_values=0,
            max_values=25
        )

        # Pre-select current bot roles
        if self.working_config.get('bot_roles'):
            guild = self.bot.get_guild(self.guild_id)
            if guild:
                default_roles = []
                for role_id in self.working_config['bot_roles']:
                    role = guild.get_role(role_id)
                    if role:
                        default_roles.append(role)
                if default_roles:
                    bot_roles_select.default_values = default_roles

        bot_roles_select.callback = self.on_bot_roles_select
        bot_roles_row.add_item(bot_roles_select)
        container.add_item(bot_roles_row)

        self.add_item(container)
        self._add_action_buttons()

    def _add_action_buttons(self):
        """Ajoute les boutons Back/Save/Cancel/Delete"""
        button_row = ui.ActionRow()

        # Back button (disabled if changes pending)
        back_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(BACK),
            label=t('modules.config.buttons.back', locale=self.locale),
            style=discord.ButtonStyle.secondary,
            disabled=self.has_changes
        )
        back_btn.callback = self.on_back
        button_row.add_item(back_btn)

        if self.has_changes:
            # Save button
            save_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str(SAVE),
                label=t('modules.config.buttons.save', locale=self.locale),
                style=discord.ButtonStyle.success
            )
            save_btn.callback = self.on_save
            button_row.add_item(save_btn)

            # Cancel button
            cancel_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str(UNDONE),
                label=t('modules.config.buttons.cancel', locale=self.locale),
                style=discord.ButtonStyle.danger
            )
            cancel_btn.callback = self.on_cancel
            button_row.add_item(cancel_btn)
        else:
            if self.has_existing_config:
                # Delete button
                delete_btn = ui.Button(
                    emoji=discord.PartialEmoji.from_str(DELETE),
                    label=t('modules.config.buttons.delete', locale=self.locale),
                    style=discord.ButtonStyle.danger
                )
                delete_btn.callback = self.on_delete
                button_row.add_item(delete_btn)

        self.add_item(button_row)

    async def on_member_roles_select(self, interaction: discord.Interaction):
        """Callback quand les rôles membres sont sélectionnés"""
        if not await self.check_user(interaction):
            return

        if interaction.data['values']:
            role_ids = [int(role_id) for role_id in interaction.data['values']]
            self.working_config['member_roles'] = role_ids
        else:
            self.working_config['member_roles'] = []

        self.has_changes = True
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_bot_roles_select(self, interaction: discord.Interaction):
        """Callback quand les rôles bots sont sélectionnés"""
        if not await self.check_user(interaction):
            return

        if interaction.data['values']:
            role_ids = [int(role_id) for role_id in interaction.data['values']]
            self.working_config['bot_roles'] = role_ids
        else:
            self.working_config['bot_roles'] = []

        self.has_changes = True
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_save(self, interaction: discord.Interaction):
        """Sauvegarde la configuration"""
        if not await self.check_user(interaction):
            return

        await interaction.response.defer()

        module_manager = self.bot.module_manager
        success, error_msg = await module_manager.save_module_config(
            self.guild_id, 'auto_role', self.working_config
        )

        if success:
            self.current_config = self.working_config.copy()
            self.has_changes = False
            self.has_existing_config = True
            self._build_view()
            await interaction.followup.send(
                t('modules.config.save.success', locale=self.locale),
                ephemeral=True
            )
            await interaction.edit_original_response(view=self)
        else:
            await interaction.followup.send(
                t('modules.config.save.error', locale=self.locale, error=error_msg),
                ephemeral=True
            )

    async def on_cancel(self, interaction: discord.Interaction):
        """Annule les modifications"""
        if not await self.check_user(interaction):
            return

        self.working_config = self.current_config.copy()
        self.has_changes = False
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_delete(self, interaction: discord.Interaction):
        """Supprime la configuration"""
        if not await self.check_user(interaction):
            return

        await interaction.response.defer()

        module_manager = self.bot.module_manager
        success = await module_manager.delete_module_config(self.guild_id, 'auto_role')

        if success:
            from modules.auto_role import AutoRoleModule
            default_config = AutoRoleModule(self.bot, self.guild_id).get_default_config()
            self.current_config = default_config
            self.working_config = default_config.copy()
            self.has_changes = False
            self.has_existing_config = False
            self._build_view()
            await interaction.followup.send(
                t('modules.config.delete.success', locale=self.locale),
                ephemeral=True
            )
            await interaction.edit_original_response(view=self)
        else:
            await interaction.followup.send(
                t('modules.config.delete.error', locale=self.locale),
                ephemeral=True
            )

    async def on_back(self, interaction: discord.Interaction):
        """Retourne au menu principal"""
        if not await self.check_user(interaction):
            return

        from cogs.config import ConfigMainView
        main_view = ConfigMainView(self.bot, self.guild_id, self.user_id, self.locale)
        await interaction.response.edit_message(view=main_view)

    async def check_user(self, interaction: discord.Interaction) -> bool:
        """Vérifie que c'est le bon utilisateur"""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                t('modules.config.errors.wrong_user', locale=self.locale),
                ephemeral=True
            )
            return False
        return True
