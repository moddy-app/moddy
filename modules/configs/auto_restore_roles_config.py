"""
Configuration UI pour le module Auto Restore Roles
Interface pour configurer la restauration automatique des rôles
"""

import discord
from discord import ui
from typing import Optional, Dict, Any
import logging

from utils.i18n import t
from cogs.error_handler import BaseView
from utils.emojis import HISTORY, REQUIRED_FIELDS, DONE, DELETE, BACK, SAVE, UNDONE

logger = logging.getLogger('moddy.modules.auto_restore_roles_config')


class AutoRestoreRolesConfigView(BaseView):
    """
    Interface de configuration du module Auto Restore Roles
    """

    def __init__(self, bot, guild_id: int, user_id: int, locale: str, current_config: Optional[Dict[str, Any]] = None):
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id
        self.locale = locale

        # Load default config
        from modules.auto_restore_roles import AutoRestoreRolesModule
        default_config = AutoRestoreRolesModule(bot, guild_id).get_default_config()

        # Check if we have a real saved config (check for any non-default value)
        if current_config and (
            current_config.get('mode') != default_config.get('mode') or
            current_config.get('log_channel_id') is not None or
            current_config.get('excluded_roles') or
            current_config.get('included_roles')
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
            f"### {HISTORY} {t('modules.auto_restore_roles.config.title', locale=self.locale)}"
        ))
        container.add_item(ui.TextDisplay(
            t('modules.auto_restore_roles.config.description', locale=self.locale)
        ))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Mode selector (Required field)
        from modules.auto_restore_roles import AutoRestoreRolesModule
        container.add_item(ui.TextDisplay(
            f"**{t('modules.auto_restore_roles.config.mode.section_title', locale=self.locale)}**{REQUIRED_FIELDS}\n"
            f"-# {t('modules.auto_restore_roles.config.mode.section_description', locale=self.locale)}"
        ))

        mode_row = ui.ActionRow()
        mode_select = ui.Select(
            placeholder=t('modules.auto_restore_roles.config.mode.placeholder', locale=self.locale),
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=t('modules.auto_restore_roles.config.mode.all.label', locale=self.locale),
                    value=AutoRestoreRolesModule.MODE_ALL,
                    description=t('modules.auto_restore_roles.config.mode.all.description', locale=self.locale),
                    emoji=DONE,
                    default=self.working_config['mode'] == AutoRestoreRolesModule.MODE_ALL
                ),
                discord.SelectOption(
                    label=t('modules.auto_restore_roles.config.mode.except.label', locale=self.locale),
                    value=AutoRestoreRolesModule.MODE_EXCEPT,
                    description=t('modules.auto_restore_roles.config.mode.except.description', locale=self.locale),
                    emoji=DELETE,
                    default=self.working_config['mode'] == AutoRestoreRolesModule.MODE_EXCEPT
                ),
                discord.SelectOption(
                    label=t('modules.auto_restore_roles.config.mode.only.label', locale=self.locale),
                    value=AutoRestoreRolesModule.MODE_ONLY,
                    description=t('modules.auto_restore_roles.config.mode.only.description', locale=self.locale),
                    emoji="<:label:1398729473649676440>",
                    default=self.working_config['mode'] == AutoRestoreRolesModule.MODE_ONLY
                )
            ]
        )
        mode_select.callback = self.on_mode_select
        mode_row.add_item(mode_select)
        container.add_item(mode_row)

        # Excluded roles selector (visible only in EXCEPT mode)
        if self.working_config['mode'] == AutoRestoreRolesModule.MODE_EXCEPT:
            container.add_item(ui.TextDisplay(
                f"**{t('modules.auto_restore_roles.config.excluded_roles.section_title', locale=self.locale)}**{REQUIRED_FIELDS}\n"
                f"-# {t('modules.auto_restore_roles.config.excluded_roles.section_description', locale=self.locale)}"
            ))

            if self.working_config.get('excluded_roles'):
                # Show current excluded roles
                excluded_role_names = []
                guild = self.bot.get_guild(self.guild_id)
                if guild:
                    for role_id in self.working_config['excluded_roles']:
                        role = guild.get_role(role_id)
                        if role:
                            excluded_role_names.append(role.mention)

                if excluded_role_names:
                    container.add_item(ui.TextDisplay(
                        f"-# {t('modules.config.current_value', locale=self.locale)} {', '.join(excluded_role_names)}"
                    ))

            excluded_row = ui.ActionRow()
            excluded_select = ui.RoleSelect(
                placeholder=t('modules.auto_restore_roles.config.excluded_roles.placeholder', locale=self.locale),
                min_values=0,
                max_values=25
            )

            # Pre-select current excluded roles
            if self.working_config.get('excluded_roles'):
                guild = self.bot.get_guild(self.guild_id)
                if guild:
                    default_roles = []
                    for role_id in self.working_config['excluded_roles']:
                        role = guild.get_role(role_id)
                        if role:
                            default_roles.append(role)
                    if default_roles:
                        excluded_select.default_values = default_roles

            excluded_select.callback = self.on_excluded_roles_select
            excluded_row.add_item(excluded_select)
            container.add_item(excluded_row)

        # Included roles selector (visible only in ONLY mode)
        if self.working_config['mode'] == AutoRestoreRolesModule.MODE_ONLY:
            container.add_item(ui.TextDisplay(
                f"**{t('modules.auto_restore_roles.config.included_roles.section_title', locale=self.locale)}**{REQUIRED_FIELDS}\n"
                f"-# {t('modules.auto_restore_roles.config.included_roles.section_description', locale=self.locale)}"
            ))

            if self.working_config.get('included_roles'):
                # Show current included roles
                included_role_names = []
                guild = self.bot.get_guild(self.guild_id)
                if guild:
                    for role_id in self.working_config['included_roles']:
                        role = guild.get_role(role_id)
                        if role:
                            included_role_names.append(role.mention)

                if included_role_names:
                    container.add_item(ui.TextDisplay(
                        f"-# {t('modules.config.current_value', locale=self.locale)} {', '.join(included_role_names)}"
                    ))

            included_row = ui.ActionRow()
            included_select = ui.RoleSelect(
                placeholder=t('modules.auto_restore_roles.config.included_roles.placeholder', locale=self.locale),
                min_values=0,
                max_values=25
            )

            # Pre-select current included roles
            if self.working_config.get('included_roles'):
                guild = self.bot.get_guild(self.guild_id)
                if guild:
                    default_roles = []
                    for role_id in self.working_config['included_roles']:
                        role = guild.get_role(role_id)
                        if role:
                            default_roles.append(role)
                    if default_roles:
                        included_select.default_values = default_roles

            included_select.callback = self.on_included_roles_select
            included_row.add_item(included_select)
            container.add_item(included_row)

        # Log channel selector (Optional)
        container.add_item(ui.TextDisplay(
            f"**{t('modules.auto_restore_roles.config.log_channel.section_title', locale=self.locale)}**\n"
            f"-# {t('modules.auto_restore_roles.config.log_channel.section_description', locale=self.locale)}"
        ))

        log_channel_row = ui.ActionRow()
        log_channel_select = ui.ChannelSelect(
            placeholder=t('modules.auto_restore_roles.config.log_channel.placeholder', locale=self.locale),
            channel_types=[discord.ChannelType.text],
            min_values=0,
            max_values=1
        )

        # Pre-select current log channel if set
        if self.working_config.get('log_channel_id'):
            channel = self.bot.get_channel(self.working_config['log_channel_id'])
            if channel:
                log_channel_select.default_values = [channel]

        log_channel_select.callback = self.on_log_channel_select
        log_channel_row.add_item(log_channel_select)
        container.add_item(log_channel_row)

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

    async def on_mode_select(self, interaction: discord.Interaction):
        """Callback quand le mode est sélectionné"""
        if not await self.check_user(interaction):
            return

        selected_mode = interaction.data['values'][0]
        self.working_config['mode'] = selected_mode

        # Reset role lists when changing mode
        if selected_mode != 'except':
            self.working_config['excluded_roles'] = []
        if selected_mode != 'only':
            self.working_config['included_roles'] = []

        self.has_changes = True
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_excluded_roles_select(self, interaction: discord.Interaction):
        """Callback quand les rôles exclus sont sélectionnés"""
        if not await self.check_user(interaction):
            return

        if interaction.data['values']:
            role_ids = [int(role_id) for role_id in interaction.data['values']]
            self.working_config['excluded_roles'] = role_ids
        else:
            self.working_config['excluded_roles'] = []

        self.has_changes = True
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_included_roles_select(self, interaction: discord.Interaction):
        """Callback quand les rôles inclus sont sélectionnés"""
        if not await self.check_user(interaction):
            return

        if interaction.data['values']:
            role_ids = [int(role_id) for role_id in interaction.data['values']]
            self.working_config['included_roles'] = role_ids
        else:
            self.working_config['included_roles'] = []

        self.has_changes = True
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_log_channel_select(self, interaction: discord.Interaction):
        """Callback quand le salon de logs est sélectionné"""
        if not await self.check_user(interaction):
            return

        if interaction.data['values']:
            channel_id = int(interaction.data['values'][0])
            self.working_config['log_channel_id'] = channel_id
        else:
            self.working_config['log_channel_id'] = None

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
            self.guild_id, 'auto_restore_roles', self.working_config
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
        success = await module_manager.delete_module_config(self.guild_id, 'auto_restore_roles')

        if success:
            from modules.auto_restore_roles import AutoRestoreRolesModule
            default_config = AutoRestoreRolesModule(self.bot, self.guild_id).get_default_config()
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
