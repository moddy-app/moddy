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
from utils.emojis import MANAGE_USER, BACK, SAVE, UNDONE
from utils import guild_tags
from modules.configs._common import (
    check_guild_perms, ack, show, state_accent, config_state, module_manage_button,
    open_main_config, crumb,
)

logger = logging.getLogger('moddy.modules.auto_role_config')

_CID_MEMBER_ROLES = "moddy:autorole:config:member_roles"
_CID_BOT_ROLES = "moddy:autorole:config:bot_roles"
_CID_TAG_ROLES = "moddy:autorole:config:tag_roles"
_CID_BACK = "moddy:autorole:config:back"
_CID_SAVE = "moddy:autorole:config:save"
_CID_CANCEL = "moddy:autorole:config:cancel"


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
            current_config.get('bot_roles') or
            current_config.get('tag_roles')
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

        # Accent follows the module state (gray when paused).
        state = config_state(self.current_config) if self.has_existing_config else None
        container = ui.Container(accent_colour=state_accent(state))
        container.add_item(crumb(self.locale, 'auto_role'))

        # Header
        container.add_item(ui.TextDisplay(
            f"### {MANAGE_USER} {t('modules.auto_role.config.title', locale=self.locale)}"
        ))
        container.add_item(ui.TextDisplay(
            t('modules.auto_role.config.description', locale=self.locale)
        ))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        guild = self.bot.get_guild(self.guild_id) if self.bot is not None else None

        self._add_role_section(container, guild, 'member_roles', _CID_MEMBER_ROLES,
                               self.on_member_roles_select)
        self._add_role_section(container, guild, 'bot_roles', _CID_BOT_ROLES,
                               self.on_bot_roles_select)

        # Server tag roles: only for a server that has a tag. The registration
        # shell (no bot) always carries the select so its custom_id dispatches.
        if self.bot is None or guild_tags.guild_has_tag(guild):
            tag = guild_tags.known_tag(self.guild_id) if self.guild_id else None
            self._add_role_section(
                container, guild, 'tag_roles', _CID_TAG_ROLES, self.on_tag_roles_select,
                description_key='section_description_tag' if tag else 'section_description',
                tag=tag,
            )

        self.add_item(container)
        self._add_action_buttons()

    def _add_role_section(self, container: ui.Container, guild: Optional[discord.Guild],
                          key: str, custom_id: str, callback, *,
                          description_key: str = 'section_description', **params):
        """One "title + description + RoleSelect" block for the ``key`` role list."""
        base = f'modules.auto_role.config.{key}'
        container.add_item(ui.TextDisplay(
            f"**{t(f'{base}.section_title', locale=self.locale)}**\n"
            f"-# {t(f'{base}.{description_key}', locale=self.locale, **params)}"
        ))

        select = ui.RoleSelect(
            placeholder=t(f'{base}.placeholder', locale=self.locale),
            min_values=0,
            max_values=25,
            custom_id=custom_id,
        )
        # Pre-select the current roles
        if guild is not None:
            default_roles = [r for r in map(guild.get_role, self.working_config.get(key) or []) if r]
            if default_roles:
                select.default_values = default_roles

        select.callback = callback
        row = ui.ActionRow()
        row.add_item(select)
        container.add_item(row)

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
        if not self.has_changes and self.has_existing_config:
            button_row.add_item(module_manage_button('auto_role', self.locale))

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
        if saved and (saved.get('member_roles') or saved.get('bot_roles') or saved.get('tag_roles')):
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

    async def _on_roles_select(self, interaction: discord.Interaction, key: str):
        if not await check_guild_perms(interaction):
            return
        await ack(interaction)

        working_config = await self._fresh_working_config(interaction)
        working_config[key] = [int(role_id) for role_id in interaction.data.get('values', [])]

        view = await self._rebuild(interaction, working_config, has_changes=True)
        await show(interaction, view)

    async def on_member_roles_select(self, interaction: discord.Interaction):
        """Callback quand les rôles membres sont sélectionnés"""
        await self._on_roles_select(interaction, 'member_roles')

    async def on_bot_roles_select(self, interaction: discord.Interaction):
        """Callback quand les rôles bots sont sélectionnés"""
        await self._on_roles_select(interaction, 'bot_roles')

    async def on_tag_roles_select(self, interaction: discord.Interaction):
        """Callback quand les rôles du tag du serveur sont sélectionnés"""
        await self._on_roles_select(interaction, 'tag_roles')

    async def on_save(self, interaction: discord.Interaction):
        """Sauvegarde la configuration"""
        if not await check_guild_perms(interaction):
            return

        await ack(interaction)

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        working_config = await self._fresh_working_config(interaction)

        success, error_msg = await bot.module_manager.save_module_config(
            interaction.guild_id, 'auto_role', working_config,
            actor_id=interaction.user.id,
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
        await ack(interaction)

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        saved = await bot.module_manager.get_module_config(interaction.guild_id, 'auto_role')
        view = AutoRoleConfigView(bot, interaction.guild_id, interaction.user.id, locale, current_config=saved)
        await show(interaction, view)

    async def on_back(self, interaction: discord.Interaction):
        """Retourne au menu principal"""
        if not await check_guild_perms(interaction):
            return

        await open_main_config(interaction)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await check_guild_perms(interaction)

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: Manage Server in the guild (checked on every click)."""
        bot.add_view(cls())
