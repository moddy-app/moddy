"""
Configuration UI pour le module Auto Restore Roles
Interface pour configurer la restauration automatique des rôles
"""

import discord
from discord import ui
from typing import Optional, Dict, Any
import logging

from utils.i18n import i18n, t
from cogs.error_handler import BaseView
from utils.emojis import HISTORY, REQUIRED_FIELDS, DONE, DELETE, BACK, SAVE, UNDONE
from modules.configs._common import check_guild_perms

logger = logging.getLogger('moddy.modules.auto_restore_roles_config')

_CID_MODE = "moddy:autorestore:config:mode"
_CID_EXCLUDED_ROLES = "moddy:autorestore:config:excluded_roles"
_CID_INCLUDED_ROLES = "moddy:autorestore:config:included_roles"
_CID_LOG_CHANNEL = "moddy:autorestore:config:log_channel"
_CID_BACK = "moddy:autorestore:config:back"
_CID_SAVE = "moddy:autorestore:config:save"
_CID_CANCEL = "moddy:autorestore:config:cancel"
_CID_DELETE = "moddy:autorestore:config:delete"


class AutoRestoreRolesConfigView(BaseView):
    """
    Interface de configuration du module Auto Restore Roles

    Persistent: yes. Auth: Manage Server in the guild (checked on every
    click via check_guild_perms — NOT via a stored user_id, which cannot
    survive a restarted shell).
    """

    __persistent__ = True

    def __init__(self, bot=None, guild_id: Optional[int] = None, user_id: Optional[int] = None,
                 locale: str = "en-US", current_config: Optional[Dict[str, Any]] = None):
        super().__init__()  # timeout=None
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
                    emoji="<:label:1519800544456343744>",
                    default=self.working_config['mode'] == AutoRestoreRolesModule.MODE_ONLY
                )
            ],
            custom_id=_CID_MODE,
        )
        mode_select.callback = self.on_mode_select
        mode_row.add_item(mode_select)
        container.add_item(mode_row)

        # Registration shell (self.bot is None): render EVERY mode-dependent
        # selector regardless of the current mode, so a click on a live
        # message in a different mode still has its custom_id registered.
        is_shell = self.bot is None

        # Excluded roles selector (visible only in EXCEPT mode)
        if self.working_config['mode'] == AutoRestoreRolesModule.MODE_EXCEPT or is_shell:
            container.add_item(ui.TextDisplay(
                f"**{t('modules.auto_restore_roles.config.excluded_roles.section_title', locale=self.locale)}**{REQUIRED_FIELDS}\n"
                f"-# {t('modules.auto_restore_roles.config.excluded_roles.section_description', locale=self.locale)}"
            ))

            if self.working_config.get('excluded_roles') and self.bot is not None:
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
                max_values=25,
                custom_id=_CID_EXCLUDED_ROLES,
            )

            # Pre-select current excluded roles
            if self.working_config.get('excluded_roles') and self.bot is not None:
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
        if self.working_config['mode'] == AutoRestoreRolesModule.MODE_ONLY or is_shell:
            container.add_item(ui.TextDisplay(
                f"**{t('modules.auto_restore_roles.config.included_roles.section_title', locale=self.locale)}**{REQUIRED_FIELDS}\n"
                f"-# {t('modules.auto_restore_roles.config.included_roles.section_description', locale=self.locale)}"
            ))

            if self.working_config.get('included_roles') and self.bot is not None:
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
                max_values=25,
                custom_id=_CID_INCLUDED_ROLES,
            )

            # Pre-select current included roles
            if self.working_config.get('included_roles') and self.bot is not None:
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
            max_values=1,
            custom_id=_CID_LOG_CHANNEL,
        )

        # Pre-select current log channel if set
        if self.working_config.get('log_channel_id') and self.bot is not None:
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
            custom_id=_CID_BACK,
            disabled=self.has_changes
        )
        back_btn.callback = self.on_back
        button_row.add_item(back_btn)

        # Registration shell (self.bot is None): register EVERY button's
        # custom_id regardless of has_changes/has_existing_config.
        is_shell = self.bot is None

        if self.has_changes or is_shell:
            # Save button
            save_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str(SAVE),
                label=t('modules.config.buttons.save', locale=self.locale),
                style=discord.ButtonStyle.success,
                custom_id=_CID_SAVE,
            )
            save_btn.callback = self.on_save
            button_row.add_item(save_btn)

            # Cancel button
            cancel_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str(UNDONE),
                label=t('modules.config.buttons.cancel', locale=self.locale),
                style=discord.ButtonStyle.danger,
                custom_id=_CID_CANCEL,
            )
            cancel_btn.callback = self.on_cancel
            button_row.add_item(cancel_btn)
        if (not self.has_changes and self.has_existing_config) or is_shell:
            # Delete button
            delete_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str(DELETE),
                label=t('modules.config.buttons.delete', locale=self.locale),
                style=discord.ButtonStyle.danger,
                custom_id=_CID_DELETE,
            )
            delete_btn.callback = self.on_delete
            button_row.add_item(delete_btn)

        self.add_item(button_row)

    def _is_live_for(self, interaction: discord.Interaction) -> bool:
        return self.bot is not None and self.guild_id == interaction.guild_id

    async def _fresh_working_config(self, interaction: discord.Interaction) -> Dict[str, Any]:
        if self._is_live_for(interaction):
            return self.working_config.copy()
        bot = interaction.client
        from modules.auto_restore_roles import AutoRestoreRolesModule
        default_config = AutoRestoreRolesModule(bot, interaction.guild_id).get_default_config()
        saved = await bot.module_manager.get_module_config(interaction.guild_id, 'auto_restore_roles')
        if saved and (
            saved.get('mode') != default_config.get('mode')
            or saved.get('log_channel_id') is not None
            or saved.get('excluded_roles') or saved.get('included_roles')
        ):
            default_config.update(saved)
        return default_config

    async def _rebuild(self, interaction: discord.Interaction, working_config: Dict[str, Any],
                        has_changes: bool) -> "AutoRestoreRolesConfigView":
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        saved = await bot.module_manager.get_module_config(interaction.guild_id, 'auto_restore_roles')
        view = AutoRestoreRolesConfigView(bot, interaction.guild_id, interaction.user.id, locale, current_config=saved)
        view.working_config = working_config
        view.has_changes = has_changes
        view._build_view()
        return view

    async def on_mode_select(self, interaction: discord.Interaction):
        """Callback quand le mode est sélectionné"""
        if not await check_guild_perms(interaction):
            return

        working_config = await self._fresh_working_config(interaction)
        selected_mode = interaction.data['values'][0]
        working_config['mode'] = selected_mode
        if selected_mode != 'except':
            working_config['excluded_roles'] = []
        if selected_mode != 'only':
            working_config['included_roles'] = []

        view = await self._rebuild(interaction, working_config, has_changes=True)
        await interaction.response.edit_message(view=view)

    async def on_excluded_roles_select(self, interaction: discord.Interaction):
        """Callback quand les rôles exclus sont sélectionnés"""
        if not await check_guild_perms(interaction):
            return

        working_config = await self._fresh_working_config(interaction)
        if interaction.data['values']:
            working_config['excluded_roles'] = [int(role_id) for role_id in interaction.data['values']]
        else:
            working_config['excluded_roles'] = []

        view = await self._rebuild(interaction, working_config, has_changes=True)
        await interaction.response.edit_message(view=view)

    async def on_included_roles_select(self, interaction: discord.Interaction):
        """Callback quand les rôles inclus sont sélectionnés"""
        if not await check_guild_perms(interaction):
            return

        working_config = await self._fresh_working_config(interaction)
        if interaction.data['values']:
            working_config['included_roles'] = [int(role_id) for role_id in interaction.data['values']]
        else:
            working_config['included_roles'] = []

        view = await self._rebuild(interaction, working_config, has_changes=True)
        await interaction.response.edit_message(view=view)

    async def on_log_channel_select(self, interaction: discord.Interaction):
        """Callback quand le salon de logs est sélectionné"""
        if not await check_guild_perms(interaction):
            return

        working_config = await self._fresh_working_config(interaction)
        if interaction.data['values']:
            working_config['log_channel_id'] = int(interaction.data['values'][0])
        else:
            working_config['log_channel_id'] = None

        view = await self._rebuild(interaction, working_config, has_changes=True)
        await interaction.response.edit_message(view=view)

    async def on_save(self, interaction: discord.Interaction):
        """Sauvegarde la configuration"""
        if not await check_guild_perms(interaction):
            return

        await interaction.response.defer()

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        working_config = await self._fresh_working_config(interaction)

        success, error_msg = await bot.module_manager.save_module_config(
            interaction.guild_id, 'auto_restore_roles', working_config,
            actor_id=interaction.user.id,
        )

        if success:
            view = AutoRestoreRolesConfigView(bot, interaction.guild_id, interaction.user.id, locale,
                                               current_config=working_config)
            await interaction.followup.send(t('modules.config.save.success', locale=locale), ephemeral=True)
            await interaction.edit_original_response(view=view)
        else:
            await interaction.followup.send(
                t('modules.config.save.error', locale=locale, error=error_msg), ephemeral=True,
            )

    async def on_cancel(self, interaction: discord.Interaction):
        """Annule les modifications"""
        if not await check_guild_perms(interaction):
            return

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        saved = await bot.module_manager.get_module_config(interaction.guild_id, 'auto_restore_roles')
        view = AutoRestoreRolesConfigView(bot, interaction.guild_id, interaction.user.id, locale, current_config=saved)
        await interaction.response.edit_message(view=view)

    async def on_delete(self, interaction: discord.Interaction):
        """Supprime la configuration"""
        if not await check_guild_perms(interaction):
            return

        await interaction.response.defer()

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        success = await bot.module_manager.delete_module_config(interaction.guild_id, 'auto_restore_roles')

        if success:
            view = AutoRestoreRolesConfigView(bot, interaction.guild_id, interaction.user.id, locale, current_config=None)
            await interaction.followup.send(t('modules.config.delete.success', locale=locale), ephemeral=True)
            await interaction.edit_original_response(view=view)
        else:
            await interaction.followup.send(t('modules.config.delete.error', locale=locale), ephemeral=True)

    async def on_back(self, interaction: discord.Interaction):
        """Retourne au menu principal"""
        if not await check_guild_perms(interaction):
            return

        from cogs.config import ConfigMainView
        locale = i18n.get_user_locale(interaction)
        main_view = ConfigMainView(interaction.client, interaction.guild_id, interaction.user.id, locale)
        await interaction.response.edit_message(view=main_view)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await check_guild_perms(interaction)

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: Manage Server in the guild (checked on every click)."""
        bot.add_view(cls())
