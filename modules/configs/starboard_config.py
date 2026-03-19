"""
Configuration UI pour le module Starboard
Interface pour configurer le tableau d'honneur des messages populaires
"""

import discord
from discord import ui
from typing import Optional, Dict, Any
import logging

from utils.i18n import t
from cogs.error_handler import BaseView, BaseModal
from utils.emojis import STAR, REQUIRED_FIELDS, EDIT, BACK, SAVE, UNDONE, DELETE

logger = logging.getLogger('moddy.modules.starboard_config')


class ReactionCountModal(BaseModal, title="Nombre de réactions"):
    """Modal pour éditer le nombre de réactions requis"""

    def __init__(self, locale: str, current_value: int, callback_func):
        super().__init__(timeout=300)
        self.locale = locale
        self.callback_func = callback_func

        self.count_input = ui.TextInput(
            label=t('modules.starboard.config.reaction_count.modal.label', locale=locale),
            placeholder=t('modules.starboard.config.reaction_count.modal.placeholder', locale=locale),
            default=str(current_value),
            style=discord.TextStyle.short,
            max_length=3,
            required=True
        )
        self.add_item(self.count_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            count = int(self.count_input.value)
            if count < 1 or count > 100:
                await interaction.response.send_message(
                    t('modules.starboard.config.reaction_count.modal.error_range', locale=self.locale),
                    ephemeral=True
                )
                return
            await self.callback_func(interaction, count)
        except ValueError:
            await interaction.response.send_message(
                t('modules.starboard.config.reaction_count.modal.error_invalid', locale=self.locale),
                ephemeral=True
            )


class StarboardConfigView(BaseView):
    """
    Interface de configuration du module Starboard
    """

    def __init__(self, bot, guild_id: int, user_id: int, locale: str, current_config: Optional[Dict[str, Any]] = None):
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id
        self.locale = locale

        # Load default config
        from modules.starboard import StarboardModule
        default_config = StarboardModule(bot, guild_id).get_default_config()

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
        """Build configuration interface"""
        self.clear_items()

        container = ui.Container()

        # Header
        container.add_item(ui.TextDisplay(
            f"### {STAR} {t('modules.starboard.config.title', locale=self.locale)}"
        ))
        container.add_item(ui.TextDisplay(
            t('modules.starboard.config.description', locale=self.locale)
        ))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Channel selector (Required field)
        container.add_item(ui.TextDisplay(
            f"**{t('modules.starboard.config.channel.section_title', locale=self.locale)}**{REQUIRED_FIELDS}\n"
            f"-# {t('modules.starboard.config.channel.section_description', locale=self.locale)}"
        ))

        channel_row = ui.ActionRow()
        channel_select = ui.ChannelSelect(
            placeholder=t('modules.starboard.config.channel.placeholder', locale=self.locale),
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

        # Reaction count configuration
        container.add_item(ui.TextDisplay(
            f"**{t('modules.starboard.config.reaction_count.section_title', locale=self.locale)}**\n"
            f"-# {t('modules.starboard.config.reaction_count.section_description', locale=self.locale)}\n"
            f"-# {t('modules.config.current_value', locale=self.locale)} **{self.working_config['reaction_count']}** {self.working_config['emoji']}"
        ))

        reaction_row = ui.ActionRow()

        edit_count_btn = ui.Button(
            label=t('modules.starboard.config.reaction_count.edit_button', locale=self.locale),
            style=discord.ButtonStyle.primary,
            emoji=discord.PartialEmoji.from_str(EDIT),
            custom_id="edit_reaction_count"
        )
        edit_count_btn.callback = self.on_edit_reaction_count
        reaction_row.add_item(edit_count_btn)

        container.add_item(reaction_row)

        self.add_item(container)

        # Add action buttons at the bottom
        self._add_action_buttons()

    def _add_action_buttons(self):
        """Add action buttons at the bottom of the view"""
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

    async def on_edit_reaction_count(self, interaction: discord.Interaction):
        """Edit reaction count"""
        if not await self.check_user(interaction):
            return

        modal = ReactionCountModal(
            self.locale,
            self.working_config['reaction_count'],
            self._on_reaction_count_edited
        )
        modal.bot = self.bot  # Set bot for error handling
        await interaction.response.send_modal(modal)

    async def _on_reaction_count_edited(self, interaction: discord.Interaction, new_count: int):
        """Callback after reaction count edit"""
        self.working_config['reaction_count'] = new_count
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
            'starboard',
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

        success = await module_manager.delete_module_config(self.guild_id, 'starboard')

        if success:
            from modules.starboard import StarboardModule
            self.current_config = StarboardModule(self.bot, self.guild_id).get_default_config()
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
