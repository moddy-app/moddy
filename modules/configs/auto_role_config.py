"""
Configuration UI pour le module Auto Role
Interface pour configurer l'attribution automatique de rôles
"""

import discord
from discord import ui
from typing import Optional, Dict, Any
import logging

from utils.i18n import i18n, t
from cogs.error_handler import BaseView
from utils.emojis import MANAGE_USER, BACK, SAVE, UNDONE, DELETE
from modules.configs._common import check_guild_perms

logger = logging.getLogger('moddy.modules.auto_role_config')

_CID_MEMBER_ROLES = "moddy:autorole:config:member_roles"
_CID_BOT_ROLES = "moddy:autorole:config:bot_roles"
_CID_BACK = "moddy:autorole:config:back"
_CID_SAVE = "moddy:autorole:config:save"
_CID_CANCEL = "moddy:autorole:config:cancel"
_CID_DELETE = "moddy:autorole:config:delete"


class AutoRoleConfigView(BaseView):
    """
    Interface de configuration du module Auto Role

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
            max_values=25,
            custom_id=_CID_MEMBER_ROLES,
        )

        # Pre-select current member roles
        if self.working_config.get('member_roles') and self.bot is not None:
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
            max_values=25,
            custom_id=_CID_BOT_ROLES,
        )

        # Pre-select current bot roles
        if self.working_config.get('bot_roles') and self.bot is not None:
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
            custom_id=_CID_BACK,
            disabled=self.has_changes
        )
        back_btn.callback = self.on_back
        button_row.add_item(back_btn)

        # Registration shell (self.bot is None): register EVERY button's
        # custom_id regardless of has_changes/has_existing_config, or a
        # click on a live message stuck in a state this bare shell doesn't
        # happen to be in would dispatch nowhere.
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
        """True if self was actually built for this guild (a live view, not
        the shared registered shell falling back after a restart — or a
        leftover instance from a different guild)."""
        return self.bot is not None and self.guild_id == interaction.guild_id

    async def _fresh_working_config(self, interaction: discord.Interaction) -> Dict[str, Any]:
        if self._is_live_for(interaction):
            return self.working_config.copy()
        bot = interaction.client
        from modules.auto_role import AutoRoleModule
        default_config = AutoRoleModule(bot, interaction.guild_id).get_default_config()
        saved = await bot.module_manager.get_module_config(interaction.guild_id, 'auto_role')
        if saved and (saved.get('member_roles') or saved.get('bot_roles')):
            default_config.update(saved)
        return default_config

    async def _rebuild(self, interaction: discord.Interaction, working_config: Dict[str, Any],
                        has_changes: bool) -> "AutoRoleConfigView":
        """Always construct a NEW view instance rather than mutate/resend
        self — self may be the single shared shell serving every guild after
        a restart."""
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        saved = await bot.module_manager.get_module_config(interaction.guild_id, 'auto_role')
        view = AutoRoleConfigView(bot, interaction.guild_id, interaction.user.id, locale, current_config=saved)
        view.working_config = working_config
        view.has_changes = has_changes
        view._build_view()
        return view

    async def on_member_roles_select(self, interaction: discord.Interaction):
        """Callback quand les rôles membres sont sélectionnés"""
        if not await check_guild_perms(interaction):
            return

        working_config = await self._fresh_working_config(interaction)
        if interaction.data['values']:
            working_config['member_roles'] = [int(role_id) for role_id in interaction.data['values']]
        else:
            working_config['member_roles'] = []

        view = await self._rebuild(interaction, working_config, has_changes=True)
        await interaction.response.edit_message(view=view)

    async def on_bot_roles_select(self, interaction: discord.Interaction):
        """Callback quand les rôles bots sont sélectionnés"""
        if not await check_guild_perms(interaction):
            return

        working_config = await self._fresh_working_config(interaction)
        if interaction.data['values']:
            working_config['bot_roles'] = [int(role_id) for role_id in interaction.data['values']]
        else:
            working_config['bot_roles'] = []

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
            interaction.guild_id, 'auto_role', working_config
        )

        if success:
            view = AutoRoleConfigView(bot, interaction.guild_id, interaction.user.id, locale,
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
        saved = await bot.module_manager.get_module_config(interaction.guild_id, 'auto_role')
        view = AutoRoleConfigView(bot, interaction.guild_id, interaction.user.id, locale, current_config=saved)
        await interaction.response.edit_message(view=view)

    async def on_delete(self, interaction: discord.Interaction):
        """Supprime la configuration"""
        if not await check_guild_perms(interaction):
            return

        await interaction.response.defer()

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        success = await bot.module_manager.delete_module_config(interaction.guild_id, 'auto_role')

        if success:
            view = AutoRoleConfigView(bot, interaction.guild_id, interaction.user.id, locale, current_config=None)
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
