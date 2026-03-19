"""
Configuration UI pour le module Welcome Channel
Interface pour configurer les messages de bienvenue dans un salon
"""

import discord
from discord import ui
from typing import Optional, Dict, Any
import logging

from utils.i18n import t
from cogs.error_handler import BaseView, BaseModal
from utils.emojis import WAVING_HAND, REQUIRED_FIELDS, EDIT, DONE, UNDONE, BACK, SAVE, DELETE

logger = logging.getLogger('moddy.modules.welcome_channel_config')


class MessageEditModal(BaseModal, title="Modifier le message"):
    """Modal pour éditer le message de bienvenue"""

    def __init__(self, locale: str, current_value: str, callback_func):
        super().__init__(timeout=300)
        self.locale = locale
        self.callback_func = callback_func

        self.message_input = ui.TextInput(
            label=t('modules.welcome_channel.config.message.modal.label', locale=locale),
            placeholder=t('modules.welcome_channel.config.message.modal.placeholder', locale=locale),
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
            label=t('modules.welcome_channel.config.embed.title_modal.label', locale=locale),
            placeholder=t('modules.welcome_channel.config.embed.title_modal.placeholder', locale=locale),
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
            label=t('modules.welcome_channel.config.embed.description_modal.label', locale=locale),
            placeholder=t('modules.welcome_channel.config.embed.description_modal.placeholder', locale=locale),
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
            label=t('modules.welcome_channel.config.embed.color_modal.label', locale=locale),
            placeholder=t('modules.welcome_channel.config.embed.color_modal.placeholder', locale=locale),
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
                t('modules.welcome_channel.config.embed.color_modal.error', locale=self.locale),
                ephemeral=True
            )


class WelcomeChannelConfigView(BaseView):
    """
    Interface de configuration du module Welcome Channel
    """

    def __init__(self, bot, guild_id: int, user_id: int, locale: str, current_config: Optional[Dict[str, Any]] = None):
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id
        self.locale = locale

        # Load default config
        from modules.welcome_channel import WelcomeChannelModule
        default_config = WelcomeChannelModule(bot, guild_id).get_default_config()

        # Check if we have a real saved config
        if current_config and current_config.get('channel_id') is not None:
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
            f"### {WAVING_HAND} {t('modules.welcome_channel.config.title', locale=self.locale)}"
        ))
        container.add_item(ui.TextDisplay(
            t('modules.welcome_channel.config.description', locale=self.locale)
        ))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Channel selector (Required field)
        container.add_item(ui.TextDisplay(
            f"**{t('modules.welcome_channel.config.channel.section_title', locale=self.locale)}**{REQUIRED_FIELDS}\n"
            f"-# {t('modules.welcome_channel.config.channel.section_description', locale=self.locale)}"
        ))

        channel_row = ui.ActionRow()
        channel_select = ui.ChannelSelect(
            placeholder=t('modules.welcome_channel.config.channel.placeholder', locale=self.locale),
            channel_types=[discord.ChannelType.text],
            min_values=0,
            max_values=1
        )

        # Pre-select current channel if set
        if self.working_config.get('channel_id'):
            channel = self.bot.get_channel(self.working_config['channel_id'])
            if channel:
                channel_select.default_values = [channel]

        channel_select.callback = self.on_channel_select
        channel_row.add_item(channel_select)
        container.add_item(channel_row)

        # Message configuration (Required field)
        container.add_item(ui.TextDisplay(
            f"**{t('modules.welcome_channel.config.message.section_title', locale=self.locale)}**{REQUIRED_FIELDS}\n"
            f"-# {t('modules.welcome_channel.config.message.section_description', locale=self.locale)}\n"
            f"-# {t('modules.config.current_value', locale=self.locale)} `{self.working_config['message_template'][:100]}{'...' if len(self.working_config['message_template']) > 100 else ''}`"
        ))

        # Buttons for message and mention
        message_row = ui.ActionRow()

        edit_message_btn = ui.Button(
            label=t('modules.welcome_channel.config.message.edit_button', locale=self.locale),
            style=discord.ButtonStyle.primary,
            emoji=discord.PartialEmoji.from_str(EDIT),
            custom_id="edit_message"
        )
        edit_message_btn.callback = self.on_edit_message
        message_row.add_item(edit_message_btn)

        mention_btn = ui.Button(
            label=t('modules.welcome_channel.config.message.mention_user', locale=self.locale),
            style=discord.ButtonStyle.success if self.working_config['mention_user'] else discord.ButtonStyle.secondary,
            emoji=discord.PartialEmoji.from_str(DONE if self.working_config['mention_user'] else UNDONE),
            custom_id="toggle_mention"
        )
        mention_btn.callback = self.on_toggle_mention
        message_row.add_item(mention_btn)

        container.add_item(message_row)

        # Embed toggle
        container.add_item(ui.TextDisplay(
            f"**{t('modules.welcome_channel.config.embed.section_title', locale=self.locale)}**\n"
            f"-# {t('modules.welcome_channel.config.embed.section_description', locale=self.locale) if self.working_config['embed_enabled'] else t('modules.welcome_channel.config.embed.section_description', locale=self.locale)}"
        ))

        embed_toggle_row = ui.ActionRow()
        embed_btn = ui.Button(
            label=t('modules.welcome_channel.config.embed.toggle', locale=self.locale),
            style=discord.ButtonStyle.success if self.working_config['embed_enabled'] else discord.ButtonStyle.secondary,
            emoji=discord.PartialEmoji.from_str(DONE if self.working_config['embed_enabled'] else UNDONE),
            custom_id="toggle_embed"
        )
        embed_btn.callback = self.on_toggle_embed
        embed_toggle_row.add_item(embed_btn)
        container.add_item(embed_toggle_row)

        # Embed options (only if embed enabled)
        if self.working_config['embed_enabled']:
            # Buttons for title and color
            embed_row1 = ui.ActionRow()

            edit_title_btn = ui.Button(
                label=t('modules.welcome_channel.config.embed.edit_title', locale=self.locale),
                style=discord.ButtonStyle.primary,
                emoji=discord.PartialEmoji.from_str(EDIT),
                custom_id="edit_embed_title"
            )
            edit_title_btn.callback = self.on_edit_embed_title
            embed_row1.add_item(edit_title_btn)

            edit_color_btn = ui.Button(
                label=t('modules.welcome_channel.config.embed.edit_color', locale=self.locale),
                style=discord.ButtonStyle.primary,
                emoji=discord.PartialEmoji.from_str("<:color:1398729435565396008>"),
                custom_id="edit_embed_color"
            )
            edit_color_btn.callback = self.on_edit_embed_color
            embed_row1.add_item(edit_color_btn)

            container.add_item(embed_row1)

            # Button for description
            embed_row2 = ui.ActionRow()

            edit_desc_btn = ui.Button(
                label=t('modules.welcome_channel.config.embed.edit_description', locale=self.locale),
                style=discord.ButtonStyle.primary,
                emoji=discord.PartialEmoji.from_str(EDIT),
                custom_id="edit_embed_description"
            )
            edit_desc_btn.callback = self.on_edit_embed_description
            embed_row2.add_item(edit_desc_btn)

            container.add_item(embed_row2)

            # Toggles for thumbnail and author
            embed_row3 = ui.ActionRow()

            thumbnail_btn = ui.Button(
                label=t('modules.welcome_channel.config.embed.thumbnail', locale=self.locale),
                style=discord.ButtonStyle.success if self.working_config['embed_thumbnail_enabled'] else discord.ButtonStyle.secondary,
                emoji=discord.PartialEmoji.from_str(DONE if self.working_config['embed_thumbnail_enabled'] else UNDONE),
                custom_id="toggle_thumbnail"
            )
            thumbnail_btn.callback = self.on_toggle_thumbnail
            embed_row3.add_item(thumbnail_btn)

            author_btn = ui.Button(
                label=t('modules.welcome_channel.config.embed.author', locale=self.locale),
                style=discord.ButtonStyle.success if self.working_config['embed_author_enabled'] else discord.ButtonStyle.secondary,
                emoji=discord.PartialEmoji.from_str(DONE if self.working_config['embed_author_enabled'] else UNDONE),
                custom_id="toggle_author"
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
            custom_id="back_btn",
            disabled=self.has_changes
        )
        back_btn.callback = self.on_back
        button_row.add_item(back_btn)

        # Save button (only if changes)
        if self.has_changes:
            save_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str(SAVE),
                label=t('modules.config.buttons.save', locale=self.locale),
                style=discord.ButtonStyle.success,
                custom_id="save_btn"
            )
            save_btn.callback = self.on_save
            button_row.add_item(save_btn)

            # Cancel button
            cancel_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str(UNDONE),
                label=t('modules.config.buttons.cancel', locale=self.locale),
                style=discord.ButtonStyle.danger,
                custom_id="cancel_btn"
            )
            cancel_btn.callback = self.on_cancel
            button_row.add_item(cancel_btn)
        else:
            # Delete button (if config exists)
            if self.has_existing_config:
                delete_btn = ui.Button(
                    emoji=discord.PartialEmoji.from_str(DELETE),
                    label=t('modules.config.buttons.delete', locale=self.locale),
                    style=discord.ButtonStyle.danger,
                    custom_id="delete_btn"
                )
                delete_btn.callback = self.on_delete
                button_row.add_item(delete_btn)

        self.add_item(button_row)

    # === CALLBACKS ===

    async def on_channel_select(self, interaction: discord.Interaction):
        """Channel selector callback"""
        if not await self.check_user(interaction):
            return

        if interaction.data['values']:
            channel_id = int(interaction.data['values'][0])
            self.working_config['channel_id'] = channel_id
        else:
            self.working_config['channel_id'] = None

        self.has_changes = True
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_edit_message(self, interaction: discord.Interaction):
        """Edit message"""
        if not await self.check_user(interaction):
            return

        modal = MessageEditModal(
            self.locale,
            self.working_config['message_template'],
            self._on_message_edited
        )
        modal.bot = self.bot  # Set bot for error handling
        await interaction.response.send_modal(modal)

    async def _on_message_edited(self, interaction: discord.Interaction, new_message: str):
        """Callback after message edit"""
        self.working_config['message_template'] = new_message
        self.has_changes = True
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_toggle_mention(self, interaction: discord.Interaction):
        """Toggle mention"""
        if not await self.check_user(interaction):
            return

        self.working_config['mention_user'] = not self.working_config['mention_user']
        self.has_changes = True
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_toggle_embed(self, interaction: discord.Interaction):
        """Toggle embed"""
        if not await self.check_user(interaction):
            return

        self.working_config['embed_enabled'] = not self.working_config['embed_enabled']
        self.has_changes = True
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_edit_embed_title(self, interaction: discord.Interaction):
        """Edit embed title"""
        if not await self.check_user(interaction):
            return

        modal = EmbedTitleModal(
            self.locale,
            self.working_config['embed_title'],
            self._on_embed_title_edited
        )
        modal.bot = self.bot  # Set bot for error handling
        await interaction.response.send_modal(modal)

    async def _on_embed_title_edited(self, interaction: discord.Interaction, new_title: str):
        """Callback after embed title edit"""
        self.working_config['embed_title'] = new_title
        self.has_changes = True
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_edit_embed_description(self, interaction: discord.Interaction):
        """Edit embed description"""
        if not await self.check_user(interaction):
            return

        modal = EmbedDescriptionModal(
            self.locale,
            self.working_config.get('embed_description'),
            self._on_embed_description_edited
        )
        modal.bot = self.bot  # Set bot for error handling
        await interaction.response.send_modal(modal)

    async def _on_embed_description_edited(self, interaction: discord.Interaction, new_desc: Optional[str]):
        """Callback after embed description edit"""
        self.working_config['embed_description'] = new_desc
        self.has_changes = True
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_edit_embed_color(self, interaction: discord.Interaction):
        """Edit embed color"""
        if not await self.check_user(interaction):
            return

        modal = EmbedColorModal(
            self.locale,
            self.working_config['embed_color'],
            self._on_embed_color_edited
        )
        modal.bot = self.bot  # Set bot for error handling
        await interaction.response.send_modal(modal)

    async def _on_embed_color_edited(self, interaction: discord.Interaction, new_color: int):
        """Callback after embed color edit"""
        self.working_config['embed_color'] = new_color
        self.has_changes = True
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_toggle_thumbnail(self, interaction: discord.Interaction):
        """Toggle thumbnail"""
        if not await self.check_user(interaction):
            return

        self.working_config['embed_thumbnail_enabled'] = not self.working_config['embed_thumbnail_enabled']
        self.has_changes = True
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_toggle_author(self, interaction: discord.Interaction):
        """Toggle author"""
        if not await self.check_user(interaction):
            return

        self.working_config['embed_author_enabled'] = not self.working_config['embed_author_enabled']
        self.has_changes = True
        self._build_view()
        await interaction.response.edit_message(view=self)

    # === ACTION BUTTON CALLBACKS ===

    async def on_back(self, interaction: discord.Interaction):
        """Return to main menu"""
        if not await self.check_user(interaction):
            return

        from cogs.config import ConfigMainView
        main_view = ConfigMainView(self.bot, self.guild_id, self.user_id, self.locale)
        await interaction.response.edit_message(view=main_view)

    async def on_save(self, interaction: discord.Interaction):
        """Save configuration"""
        if not await self.check_user(interaction):
            return

        await interaction.response.defer()

        module_manager = self.bot.module_manager

        success, error_msg = await module_manager.save_module_config(
            self.guild_id,
            'welcome_channel',
            self.working_config
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
        """Cancel changes"""
        if not await self.check_user(interaction):
            return

        self.working_config = self.current_config.copy()
        self.has_changes = False

        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_delete(self, interaction: discord.Interaction):
        """Delete configuration"""
        if not await self.check_user(interaction):
            return

        await interaction.response.defer()

        module_manager = self.bot.module_manager

        success = await module_manager.delete_module_config(self.guild_id, 'welcome_channel')

        if success:
            from modules.welcome_channel import WelcomeChannelModule
            self.current_config = WelcomeChannelModule(self.bot, self.guild_id).get_default_config()
            self.working_config = self.current_config.copy()
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

    async def check_user(self, interaction: discord.Interaction) -> bool:
        """Check if the user is the one who started the config"""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                t('modules.config.errors.wrong_user', locale=self.locale),
                ephemeral=True
            )
            return False
        return True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Check permissions for each interaction"""
        return await self.check_user(interaction)
