"""
Configuration UI pour le module Welcome DM
Interface pour configurer les messages de bienvenue en DM
"""

import discord
from discord import ui
from typing import Optional, Dict, Any
import logging

from utils.i18n import i18n, t
from cogs.error_handler import BaseView, BaseModal
from utils.emojis import WAVING_HAND, REQUIRED_FIELDS, EDIT, DONE, UNDONE, BACK, SAVE, DELETE
from modules.configs._common import check_guild_perms

logger = logging.getLogger('moddy.modules.welcome_dm_config')

_CID_EDIT_MESSAGE = "moddy:welcomedm:config:edit_message"
_CID_TOGGLE_EMBED = "moddy:welcomedm:config:toggle_embed"
_CID_EDIT_EMBED_TITLE = "moddy:welcomedm:config:edit_embed_title"
_CID_EDIT_EMBED_COLOR = "moddy:welcomedm:config:edit_embed_color"
_CID_EDIT_EMBED_DESC = "moddy:welcomedm:config:edit_embed_description"
_CID_TOGGLE_THUMBNAIL = "moddy:welcomedm:config:toggle_thumbnail"
_CID_TOGGLE_AUTHOR = "moddy:welcomedm:config:toggle_author"
_CID_BACK = "moddy:welcomedm:config:back"
_CID_SAVE = "moddy:welcomedm:config:save"
_CID_CANCEL = "moddy:welcomedm:config:cancel"
_CID_DELETE = "moddy:welcomedm:config:delete"


class MessageEditModal(BaseModal, title="Modifier le message"):
    """Modal pour éditer le message de bienvenue"""

    def __init__(self, locale: str, current_value: str, callback_func):
        super().__init__(timeout=300)
        self.locale = locale
        self.callback_func = callback_func

        self.message_input = ui.TextInput(
            label=t('modules.welcome_dm.config.message.modal.label', locale=locale),
            placeholder=t('modules.welcome_dm.config.message.modal.placeholder', locale=locale),
            default=current_value,
            style=discord.TextStyle.paragraph,
            max_length=2000,
            required=True
        )
        self.add_item(self.message_input)

    async def on_submit(self, interaction: discord.Interaction):
        await self.callback_func(interaction, self.message_input.value)


class EmbedTitleModal(BaseModal, title="Modifier le titre de l'embed"):
    """Modal pour éditer le titre de l'embed"""

    def __init__(self, locale: str, current_value: str, callback_func):
        super().__init__(timeout=300)
        self.locale = locale
        self.callback_func = callback_func

        self.title_input = ui.TextInput(
            label=t('modules.welcome_dm.config.embed.title_modal.label', locale=locale),
            placeholder=t('modules.welcome_dm.config.embed.title_modal.placeholder', locale=locale),
            default=current_value,
            style=discord.TextStyle.short,
            max_length=256,
            required=True
        )
        self.add_item(self.title_input)

    async def on_submit(self, interaction: discord.Interaction):
        await self.callback_func(interaction, self.title_input.value)


class EmbedDescriptionModal(BaseModal, title="Modifier la description de l'embed"):
    """Modal pour éditer la description de l'embed"""

    def __init__(self, locale: str, current_value: Optional[str], callback_func):
        super().__init__(timeout=300)
        self.locale = locale
        self.callback_func = callback_func

        self.desc_input = ui.TextInput(
            label=t('modules.welcome_dm.config.embed.description_modal.label', locale=locale),
            placeholder=t('modules.welcome_dm.config.embed.description_modal.placeholder', locale=locale),
            default=current_value or "",
            style=discord.TextStyle.paragraph,
            max_length=4096,
            required=False
        )
        self.add_item(self.desc_input)

    async def on_submit(self, interaction: discord.Interaction):
        await self.callback_func(interaction, self.desc_input.value if self.desc_input.value else None)


class EmbedColorModal(BaseModal, title="Modifier la couleur de l'embed"):
    """Modal pour éditer la couleur de l'embed"""

    def __init__(self, locale: str, current_value: int, callback_func):
        super().__init__(timeout=300)
        self.locale = locale
        self.callback_func = callback_func

        hex_color = f"#{current_value:06X}"

        self.color_input = ui.TextInput(
            label=t('modules.welcome_dm.config.embed.color_modal.label', locale=locale),
            placeholder=t('modules.welcome_dm.config.embed.color_modal.placeholder', locale=locale),
            default=hex_color,
            style=discord.TextStyle.short,
            max_length=7,
            required=True
        )
        self.add_item(self.color_input)

    async def on_submit(self, interaction: discord.Interaction):
        color_str = self.color_input.value.strip()
        if not color_str.startswith('#'):
            color_str = '#' + color_str

        try:
            color_int = int(color_str[1:], 16)
            await self.callback_func(interaction, color_int)
        except ValueError:
            await interaction.response.send_message(
                t('modules.welcome_dm.config.embed.color_modal.error', locale=self.locale),
                ephemeral=True
            )


class WelcomeDmConfigView(BaseView):
    """
    Interface de configuration du module Welcome DM

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
        from modules.welcome_dm import WelcomeDmModule
        default_config = WelcomeDmModule(bot, guild_id).get_default_config()

        # Check if we have a real saved config (check for message_template since there's no channel_id)
        if current_config and current_config.get('message_template') is not None:
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
            f"### {WAVING_HAND} {t('modules.welcome_dm.config.title', locale=self.locale)}"
        ))
        container.add_item(ui.TextDisplay(
            t('modules.welcome_dm.config.description', locale=self.locale)
        ))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Message configuration (Required field)
        container.add_item(ui.TextDisplay(
            f"**{t('modules.welcome_dm.config.message.section_title', locale=self.locale)}**{REQUIRED_FIELDS}\n"
            f"-# {t('modules.welcome_dm.config.message.section_description', locale=self.locale)}\n"
            f"-# {t('modules.config.current_value', locale=self.locale)} `{self.working_config['message_template'][:100]}{'...' if len(self.working_config['message_template']) > 100 else ''}`"
        ))

        # Button for message
        message_row = ui.ActionRow()

        edit_message_btn = ui.Button(
            label=t('modules.welcome_dm.config.message.edit_button', locale=self.locale),
            style=discord.ButtonStyle.primary,
            emoji=discord.PartialEmoji.from_str(EDIT),
            custom_id=_CID_EDIT_MESSAGE
        )
        edit_message_btn.callback = self.on_edit_message
        message_row.add_item(edit_message_btn)

        container.add_item(message_row)

        # Embed toggle
        container.add_item(ui.TextDisplay(
            f"**{t('modules.welcome_dm.config.embed.section_title', locale=self.locale)}**\n"
            f"-# {t('modules.welcome_dm.config.embed.section_description', locale=self.locale) if self.working_config['embed_enabled'] else t('modules.welcome_dm.config.embed.section_description', locale=self.locale)}"
        ))

        embed_toggle_row = ui.ActionRow()
        embed_btn = ui.Button(
            label=t('modules.welcome_dm.config.embed.toggle', locale=self.locale),
            style=discord.ButtonStyle.success if self.working_config['embed_enabled'] else discord.ButtonStyle.secondary,
            emoji=discord.PartialEmoji.from_str(DONE if self.working_config['embed_enabled'] else UNDONE),
            custom_id=_CID_TOGGLE_EMBED
        )
        embed_btn.callback = self.on_toggle_embed
        embed_toggle_row.add_item(embed_btn)
        container.add_item(embed_toggle_row)

        # Registration shell (self.bot is None): render the embed-only
        # section regardless of embed_enabled, so a live message with the
        # embed enabled still has those custom_ids registered post-restart.
        is_shell = self.bot is None

        # Embed options (only if embed enabled)
        if self.working_config['embed_enabled'] or is_shell:
            # Buttons for title and color
            embed_row1 = ui.ActionRow()

            edit_title_btn = ui.Button(
                label=t('modules.welcome_dm.config.embed.edit_title', locale=self.locale),
                style=discord.ButtonStyle.primary,
                emoji=discord.PartialEmoji.from_str(EDIT),
                custom_id=_CID_EDIT_EMBED_TITLE
            )
            edit_title_btn.callback = self.on_edit_embed_title
            embed_row1.add_item(edit_title_btn)

            edit_color_btn = ui.Button(
                label=t('modules.welcome_dm.config.embed.edit_color', locale=self.locale),
                style=discord.ButtonStyle.primary,
                emoji=discord.PartialEmoji.from_str("<:color:1519800727231266858>"),
                custom_id=_CID_EDIT_EMBED_COLOR
            )
            edit_color_btn.callback = self.on_edit_embed_color
            embed_row1.add_item(edit_color_btn)

            container.add_item(embed_row1)

            # Button for description
            embed_row2 = ui.ActionRow()

            edit_desc_btn = ui.Button(
                label=t('modules.welcome_dm.config.embed.edit_description', locale=self.locale),
                style=discord.ButtonStyle.primary,
                emoji=discord.PartialEmoji.from_str(EDIT),
                custom_id=_CID_EDIT_EMBED_DESC
            )
            edit_desc_btn.callback = self.on_edit_embed_description
            embed_row2.add_item(edit_desc_btn)

            container.add_item(embed_row2)

            # Toggles for thumbnail and author
            embed_row3 = ui.ActionRow()

            thumbnail_btn = ui.Button(
                label=t('modules.welcome_dm.config.embed.thumbnail', locale=self.locale),
                style=discord.ButtonStyle.success if self.working_config['embed_thumbnail_enabled'] else discord.ButtonStyle.secondary,
                emoji=discord.PartialEmoji.from_str(DONE if self.working_config['embed_thumbnail_enabled'] else UNDONE),
                custom_id=_CID_TOGGLE_THUMBNAIL
            )
            thumbnail_btn.callback = self.on_toggle_thumbnail
            embed_row3.add_item(thumbnail_btn)

            author_btn = ui.Button(
                label=t('modules.welcome_dm.config.embed.author', locale=self.locale),
                style=discord.ButtonStyle.success if self.working_config['embed_author_enabled'] else discord.ButtonStyle.secondary,
                emoji=discord.PartialEmoji.from_str(DONE if self.working_config['embed_author_enabled'] else UNDONE),
                custom_id=_CID_TOGGLE_AUTHOR
            )
            author_btn.callback = self.on_toggle_author
            embed_row3.add_item(author_btn)

            container.add_item(embed_row3)

        self.add_item(container)

        # Add action buttons at the bottom
        self._add_action_buttons()

    def _add_action_buttons(self):
        """Ajoute les boutons d'action en bas de la vue"""
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

        # Save button (only if changes)
        if self.has_changes or is_shell:
            save_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str(SAVE),
                label=t('modules.config.buttons.save', locale=self.locale),
                style=discord.ButtonStyle.success,
                custom_id=_CID_SAVE
            )
            save_btn.callback = self.on_save
            button_row.add_item(save_btn)

            # Cancel button
            cancel_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str(UNDONE),
                label=t('modules.config.buttons.cancel', locale=self.locale),
                style=discord.ButtonStyle.danger,
                custom_id=_CID_CANCEL
            )
            cancel_btn.callback = self.on_cancel
            button_row.add_item(cancel_btn)
        if (not self.has_changes and self.has_existing_config) or is_shell:
            # Delete button (if config exists)
            delete_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str(DELETE),
                label=t('modules.config.buttons.delete', locale=self.locale),
                style=discord.ButtonStyle.danger,
                custom_id=_CID_DELETE
            )
            delete_btn.callback = self.on_delete
            button_row.add_item(delete_btn)

        self.add_item(button_row)

    # === persistence helpers ===

    def _is_live_for(self, interaction: discord.Interaction) -> bool:
        return self.bot is not None and self.guild_id == interaction.guild_id

    async def _fresh_working_config(self, interaction: discord.Interaction) -> Dict[str, Any]:
        if self._is_live_for(interaction):
            return self.working_config.copy()
        bot = interaction.client
        from modules.welcome_dm import WelcomeDmModule
        default_config = WelcomeDmModule(bot, interaction.guild_id).get_default_config()
        saved = await bot.module_manager.get_module_config(interaction.guild_id, 'welcome_dm')
        if saved and saved.get('message_template') is not None:
            default_config.update(saved)
        return default_config

    async def _rebuild(self, interaction: discord.Interaction, working_config: Dict[str, Any],
                        has_changes: bool) -> "WelcomeDmConfigView":
        """Always construct a NEW view instance rather than mutate/resend
        self — self may be the single shared shell serving every guild after
        a restart."""
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        saved = await bot.module_manager.get_module_config(interaction.guild_id, 'welcome_dm')
        view = WelcomeDmConfigView(bot, interaction.guild_id, interaction.user.id, locale, current_config=saved)
        view.working_config = working_config
        view.has_changes = has_changes
        view._build_view()
        return view

    # === CALLBACKS ===

    async def on_edit_message(self, interaction: discord.Interaction):
        """Edit message"""
        if not await check_guild_perms(interaction):
            return

        working_config = await self._fresh_working_config(interaction)
        locale = i18n.get_user_locale(interaction)

        async def _on_submit(modal_interaction: discord.Interaction, new_message: str):
            working_config['message_template'] = new_message
            view = await self._rebuild(modal_interaction, working_config, has_changes=True)
            await modal_interaction.response.edit_message(view=view)

        modal = MessageEditModal(locale, working_config['message_template'], _on_submit)
        modal.bot = interaction.client
        await interaction.response.send_modal(modal)

    async def on_toggle_embed(self, interaction: discord.Interaction):
        """Toggle embed"""
        if not await check_guild_perms(interaction):
            return

        working_config = await self._fresh_working_config(interaction)
        working_config['embed_enabled'] = not working_config['embed_enabled']
        view = await self._rebuild(interaction, working_config, has_changes=True)
        await interaction.response.edit_message(view=view)

    async def on_edit_embed_title(self, interaction: discord.Interaction):
        """Edit embed title"""
        if not await check_guild_perms(interaction):
            return

        working_config = await self._fresh_working_config(interaction)
        locale = i18n.get_user_locale(interaction)

        async def _on_submit(modal_interaction: discord.Interaction, new_title: str):
            working_config['embed_title'] = new_title
            view = await self._rebuild(modal_interaction, working_config, has_changes=True)
            await modal_interaction.response.edit_message(view=view)

        modal = EmbedTitleModal(locale, working_config['embed_title'], _on_submit)
        modal.bot = interaction.client
        await interaction.response.send_modal(modal)

    async def on_edit_embed_description(self, interaction: discord.Interaction):
        """Edit embed description"""
        if not await check_guild_perms(interaction):
            return

        working_config = await self._fresh_working_config(interaction)
        locale = i18n.get_user_locale(interaction)

        async def _on_submit(modal_interaction: discord.Interaction, new_desc: Optional[str]):
            working_config['embed_description'] = new_desc
            view = await self._rebuild(modal_interaction, working_config, has_changes=True)
            await modal_interaction.response.edit_message(view=view)

        modal = EmbedDescriptionModal(locale, working_config.get('embed_description'), _on_submit)
        modal.bot = interaction.client
        await interaction.response.send_modal(modal)

    async def on_edit_embed_color(self, interaction: discord.Interaction):
        """Edit embed color"""
        if not await check_guild_perms(interaction):
            return

        working_config = await self._fresh_working_config(interaction)
        locale = i18n.get_user_locale(interaction)

        async def _on_submit(modal_interaction: discord.Interaction, new_color: int):
            working_config['embed_color'] = new_color
            view = await self._rebuild(modal_interaction, working_config, has_changes=True)
            await modal_interaction.response.edit_message(view=view)

        modal = EmbedColorModal(locale, working_config['embed_color'], _on_submit)
        modal.bot = interaction.client
        await interaction.response.send_modal(modal)

    async def on_toggle_thumbnail(self, interaction: discord.Interaction):
        """Toggle thumbnail"""
        if not await check_guild_perms(interaction):
            return

        working_config = await self._fresh_working_config(interaction)
        working_config['embed_thumbnail_enabled'] = not working_config['embed_thumbnail_enabled']
        view = await self._rebuild(interaction, working_config, has_changes=True)
        await interaction.response.edit_message(view=view)

    async def on_toggle_author(self, interaction: discord.Interaction):
        """Toggle author"""
        if not await check_guild_perms(interaction):
            return

        working_config = await self._fresh_working_config(interaction)
        working_config['embed_author_enabled'] = not working_config['embed_author_enabled']
        view = await self._rebuild(interaction, working_config, has_changes=True)
        await interaction.response.edit_message(view=view)

    # === ACTION BUTTON CALLBACKS ===

    async def on_back(self, interaction: discord.Interaction):
        """Return to main menu"""
        if not await check_guild_perms(interaction):
            return

        from cogs.config import ConfigMainView
        locale = i18n.get_user_locale(interaction)
        main_view = ConfigMainView(interaction.client, interaction.guild_id, interaction.user.id, locale)
        await interaction.response.edit_message(view=main_view)

    async def on_save(self, interaction: discord.Interaction):
        """Save configuration"""
        if not await check_guild_perms(interaction):
            return

        await interaction.response.defer()

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        working_config = await self._fresh_working_config(interaction)

        success, error_msg = await bot.module_manager.save_module_config(
            interaction.guild_id, 'welcome_dm', working_config,
        )

        if success:
            view = WelcomeDmConfigView(bot, interaction.guild_id, interaction.user.id, locale,
                                        current_config=working_config)
            await interaction.followup.send(t('modules.config.save.success', locale=locale), ephemeral=True)
            await interaction.edit_original_response(view=view)
        else:
            await interaction.followup.send(
                t('modules.config.save.error', locale=locale, error=error_msg), ephemeral=True,
            )

    async def on_cancel(self, interaction: discord.Interaction):
        """Cancel changes"""
        if not await check_guild_perms(interaction):
            return

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        saved = await bot.module_manager.get_module_config(interaction.guild_id, 'welcome_dm')
        view = WelcomeDmConfigView(bot, interaction.guild_id, interaction.user.id, locale, current_config=saved)
        await interaction.response.edit_message(view=view)

    async def on_delete(self, interaction: discord.Interaction):
        """Delete configuration"""
        if not await check_guild_perms(interaction):
            return

        await interaction.response.defer()

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        success = await bot.module_manager.delete_module_config(interaction.guild_id, 'welcome_dm')

        if success:
            view = WelcomeDmConfigView(bot, interaction.guild_id, interaction.user.id, locale, current_config=None)
            await interaction.followup.send(t('modules.config.delete.success', locale=locale), ephemeral=True)
            await interaction.edit_original_response(view=view)
        else:
            await interaction.followup.send(t('modules.config.delete.error', locale=locale), ephemeral=True)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Check permissions for each interaction"""
        return await check_guild_perms(interaction)

    @classmethod
    def register_persistent(cls, bot) -> None:
        """Auth model: Manage Server in the guild (checked on every click)."""
        bot.add_view(cls())
