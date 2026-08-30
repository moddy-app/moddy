"""
Configuration UI pour le module Starboard
Interface pour configurer le tableau d'honneur des messages populaires
"""

import discord
from discord import ui
from typing import Optional, Dict, Any
import logging

from utils.i18n import i18n, t
from utils.interaction_response import safe_defer
from cogs.error_handler import BaseView, BaseModal
from utils.emojis import STAR, REQUIRED_FIELDS, EDIT, BACK, SAVE, UNDONE, DELETE, is_standard_discord_emoji
from modules.configs._common import check_guild_perms

logger = logging.getLogger('moddy.modules.starboard_config')

_CID_CHANNEL = "moddy:starboard:config:channel"
_CID_EDIT_COUNT = "moddy:starboard:config:edit_count"
_CID_EDIT_EMOJI = "moddy:starboard:config:edit_emoji"
_CID_BACK = "moddy:starboard:config:back"
_CID_SAVE = "moddy:starboard:config:save"
_CID_CANCEL = "moddy:starboard:config:cancel"
_CID_DELETE = "moddy:starboard:config:delete"


class EmojiModal(BaseModal, title="Émoji de réaction"):
    """Modal pour éditer l'émoji de réaction du starboard"""

    def __init__(self, locale: str, current_value: str, callback_func):
        super().__init__(timeout=300)
        self.locale = locale
        self.callback_func = callback_func

        self.emoji_input = ui.TextInput(
            label=t('modules.starboard.config.emoji.modal.label', locale=locale),
            placeholder=t('modules.starboard.config.emoji.modal.placeholder', locale=locale),
            default=current_value,
            style=discord.TextStyle.short,
            max_length=8,
            required=True
        )
        self.add_item(self.emoji_input)

    async def on_submit(self, interaction: discord.Interaction):
        value = self.emoji_input.value.strip()

        partial_emoji = discord.PartialEmoji.from_str(value)
        if partial_emoji.is_custom_emoji():
            await interaction.response.send_message(
                t('modules.starboard.config.emoji.modal.error_custom', locale=self.locale),
                ephemeral=True
            )
            return

        if not is_standard_discord_emoji(value):
            await interaction.response.send_message(
                t('modules.starboard.config.emoji.modal.error_invalid', locale=self.locale),
                ephemeral=True
            )
            return

        await self.callback_func(interaction, value)


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
            max_values=1,
            custom_id=_CID_CHANNEL,
        )

        # Pre-select current channel if set
        if self.working_config.get('channel_id') and self.bot is not None:
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
            custom_id=_CID_EDIT_COUNT
        )
        edit_count_btn.callback = self.on_edit_reaction_count
        reaction_row.add_item(edit_count_btn)

        container.add_item(reaction_row)

        # Reaction emoji configuration (standard Discord emojis only, no custom emoji)
        container.add_item(ui.TextDisplay(
            f"**{t('modules.starboard.config.emoji.section_title', locale=self.locale)}**\n"
            f"-# {t('modules.starboard.config.emoji.section_description', locale=self.locale)}\n"
            f"-# {t('modules.config.current_value', locale=self.locale)} {self.working_config['emoji']}"
        ))

        emoji_row = ui.ActionRow()

        edit_emoji_btn = ui.Button(
            label=t('modules.starboard.config.emoji.edit_button', locale=self.locale),
            style=discord.ButtonStyle.primary,
            emoji=discord.PartialEmoji.from_str(EDIT),
            custom_id=_CID_EDIT_EMOJI
        )
        edit_emoji_btn.callback = self.on_edit_emoji
        emoji_row.add_item(edit_emoji_btn)

        container.add_item(emoji_row)

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
        from modules.starboard import StarboardModule
        default_config = StarboardModule(bot, interaction.guild_id).get_default_config()
        saved = await bot.module_manager.get_module_config(interaction.guild_id, 'starboard')
        if saved and saved.get('channel_id') is not None:
            default_config.update(saved)
        return default_config

    async def _rebuild(self, interaction: discord.Interaction, working_config: Dict[str, Any],
                        has_changes: bool) -> "StarboardConfigView":
        """Always construct a NEW view instance rather than mutate/resend
        self — self may be the single shared shell serving every guild after
        a restart."""
        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        saved = await bot.module_manager.get_module_config(interaction.guild_id, 'starboard')
        view = StarboardConfigView(bot, interaction.guild_id, interaction.user.id, locale, current_config=saved)
        view.working_config = working_config
        view.has_changes = has_changes
        view._build_view()
        return view

    # === CALLBACKS ===

    async def on_channel_select(self, interaction: discord.Interaction):
        """Channel selector callback"""
        if not await check_guild_perms(interaction):
            return

        # The stored config is read below: acknowledge before the round-trip.
        await safe_defer(interaction, thinking=False)

        working_config = await self._fresh_working_config(interaction)
        if interaction.data['values']:
            working_config['channel_id'] = int(interaction.data['values'][0])
        else:
            working_config['channel_id'] = None

        view = await self._rebuild(interaction, working_config, has_changes=True)
        await interaction.edit_original_response(view=view)

    async def on_edit_reaction_count(self, interaction: discord.Interaction):
        """Edit reaction count"""
        if not await check_guild_perms(interaction):
            return

        working_config = await self._fresh_working_config(interaction)
        locale = i18n.get_user_locale(interaction)

        async def _on_submit(modal_interaction: discord.Interaction, new_count: int):
            working_config['reaction_count'] = new_count
            view = await self._rebuild(modal_interaction, working_config, has_changes=True)
            await modal_interaction.response.edit_message(view=view)

        modal = ReactionCountModal(locale, working_config['reaction_count'], _on_submit)
        modal.bot = interaction.client
        await interaction.response.send_modal(modal)

    async def on_edit_emoji(self, interaction: discord.Interaction):
        """Edit reaction emoji"""
        if not await check_guild_perms(interaction):
            return

        working_config = await self._fresh_working_config(interaction)
        locale = i18n.get_user_locale(interaction)

        async def _on_submit(modal_interaction: discord.Interaction, new_emoji: str):
            working_config['emoji'] = new_emoji
            view = await self._rebuild(modal_interaction, working_config, has_changes=True)
            await modal_interaction.response.edit_message(view=view)

        modal = EmojiModal(locale, working_config['emoji'], _on_submit)
        modal.bot = interaction.client
        await interaction.response.send_modal(modal)

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
            interaction.guild_id, 'starboard', working_config,
            actor_id=interaction.user.id,
        )

        if success:
            view = StarboardConfigView(bot, interaction.guild_id, interaction.user.id, locale,
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

        await safe_defer(interaction, thinking=False)

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        saved = await bot.module_manager.get_module_config(interaction.guild_id, 'starboard')
        view = StarboardConfigView(bot, interaction.guild_id, interaction.user.id, locale, current_config=saved)
        await interaction.edit_original_response(view=view)

    async def on_delete(self, interaction: discord.Interaction):
        """Delete configuration"""
        if not await check_guild_perms(interaction):
            return

        await interaction.response.defer()

        bot = interaction.client
        locale = i18n.get_user_locale(interaction)
        success = await bot.module_manager.delete_module_config(interaction.guild_id, 'starboard')

        if success:
            view = StarboardConfigView(bot, interaction.guild_id, interaction.user.id, locale, current_config=None)
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
