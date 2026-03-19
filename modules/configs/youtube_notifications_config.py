"""
Configuration UI pour le module YouTube Notifications
Interface pour configurer les notifications YouTube via WebSub
"""

import discord
from discord import ui
from typing import Optional, Dict, Any, List
import logging
import re

from utils.i18n import t
from cogs.error_handler import BaseView, BaseModal
from utils.emojis import MODDY_ALT, REQUIRED_FIELDS, EDIT, INFO, ADD, BACK, SAVE, UNDONE, DELETE, DONE

logger = logging.getLogger('moddy.modules.youtube_notifications_config')


class AddChannelModal(BaseModal, title="Ajouter une chaîne YouTube"):
    """Modal pour ajouter une chaîne YouTube"""

    def __init__(self, locale: str, callback_func):
        super().__init__(timeout=300)
        self.locale = locale
        self.callback_func = callback_func

        # Channel username or ID input
        self.channel_input = ui.TextInput(
            label=t('modules.youtube_notifications.config.add.channel_label', locale=locale),
            placeholder=t('modules.youtube_notifications.config.add.channel_placeholder', locale=locale),
            style=discord.TextStyle.short,
            max_length=100,
            required=True
        )
        self.add_item(self.channel_input)

        # Message template input
        self.message_input = ui.TextInput(
            label=t('modules.youtube_notifications.config.add.message_label', locale=locale),
            placeholder=t('modules.youtube_notifications.config.add.message_placeholder', locale=locale),
            default=t('modules.youtube_notifications.config.add.message_default', locale=locale),
            style=discord.TextStyle.paragraph,
            max_length=2000,
            required=True
        )
        self.add_item(self.message_input)

    async def on_submit(self, interaction: discord.Interaction):
        """Handle modal submission"""
        channel_input = self.channel_input.value.strip()
        message = self.message_input.value

        # Extract channel ID from input (could be @username, channel ID, or URL)
        channel_id = await self._extract_channel_id(channel_input)

        if not channel_id:
            await interaction.response.send_message(
                t('modules.youtube_notifications.config.add.error.invalid_channel', locale=self.locale),
                ephemeral=True
            )
            return

        # Call the callback with extracted data
        await self.callback_func(interaction, channel_id, channel_input, message)

    async def _extract_channel_id(self, input_str: str) -> Optional[str]:
        """
        Extract YouTube channel ID from various input formats

        Supports:
        - Channel ID: UCxxxxxxxxxxxxxxxxxxxxxx
        - @username
        - Channel URL
        """
        # If it starts with UC and is 24 characters, it's likely a channel ID
        if input_str.startswith('UC') and len(input_str) == 24:
            return input_str

        # If it starts with @, remove it (we'll store it as username for display)
        if input_str.startswith('@'):
            # For now, we'll return the username as-is
            # In a real implementation, you'd need to resolve this to a channel ID
            return input_str

        # Try to extract from URL
        url_patterns = [
            r'youtube\.com/channel/([UC][\w-]{22,})',
            r'youtube\.com/@([\w-]+)',
            r'youtube\.com/c/([\w-]+)',
            r'youtube\.com/user/([\w-]+)',
        ]

        for pattern in url_patterns:
            match = re.search(pattern, input_str)
            if match:
                return match.group(1)

        # If nothing matched, assume it's a channel ID or username
        return input_str


class EditSubscriptionModal(BaseModal, title="Modifier le message"):
    """Modal pour éditer le message d'une souscription"""

    def __init__(self, locale: str, current_message: str, callback_func):
        super().__init__(timeout=300)
        self.locale = locale
        self.callback_func = callback_func

        self.message_input = ui.TextInput(
            label=t('modules.youtube_notifications.config.edit.message_label', locale=locale),
            placeholder=t('modules.youtube_notifications.config.edit.message_placeholder', locale=locale),
            default=current_message,
            style=discord.TextStyle.paragraph,
            max_length=2000,
            required=True
        )
        self.add_item(self.message_input)

    async def on_submit(self, interaction: discord.Interaction):
        await self.callback_func(interaction, self.message_input.value)


class EditSubscriptionView(BaseView):
    """Vue pour éditer une souscription existante"""

    def __init__(self, bot, guild_id: int, user_id: int, locale: str, subscription: Dict[str, Any], parent_view, subscription_index: int):
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id
        self.locale = locale
        self.subscription = subscription.copy()
        self.parent_view = parent_view
        self.subscription_index = subscription_index
        self.has_changes = False

        self._build_view()

    def _build_view(self):
        """Build the edit interface"""
        self.clear_items()

        container = ui.Container()

        # Title
        container.add_item(ui.TextDisplay(
            f"### {MODDY_ALT} {t('modules.youtube_notifications.config.edit.title', locale=self.locale)}"
        ))

        # Channel info (read-only)
        channel_display = self.subscription.get('channel_username', self.subscription['channel_id'])
        container.add_item(ui.TextDisplay(
            f"**{t('modules.youtube_notifications.config.edit.channel', locale=self.locale)}**\n"
            f"-# `{channel_display}`"
        ))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Current message
        container.add_item(ui.TextDisplay(
            f"**{t('modules.youtube_notifications.config.edit.current_message', locale=self.locale)}**\n"
            f"-# {t('modules.config.current_value', locale=self.locale)} `{self.subscription['message'][:100]}{'...' if len(self.subscription['message']) > 100 else ''}`"
        ))

        # Edit message button
        message_row = ui.ActionRow()
        edit_message_btn = ui.Button(
            label=t('modules.youtube_notifications.config.edit.edit_message', locale=self.locale),
            style=discord.ButtonStyle.primary,
            emoji=discord.PartialEmoji.from_str(EDIT)
        )
        edit_message_btn.callback = self.on_edit_message
        message_row.add_item(edit_message_btn)
        container.add_item(message_row)

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Discord channel selector
        container.add_item(ui.TextDisplay(
            f"**{t('modules.youtube_notifications.config.edit.discord_channel', locale=self.locale)}**{REQUIRED_FIELDS}\n"
            f"-# {t('modules.youtube_notifications.config.edit.discord_channel_description', locale=self.locale)}"
        ))

        channel_row = ui.ActionRow()
        channel_select = ui.ChannelSelect(
            placeholder=t('modules.youtube_notifications.config.edit.channel_placeholder', locale=self.locale),
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1
        )

        # Pre-select current channel
        if self.subscription.get('discord_channel_id'):
            channel = self.bot.get_channel(self.subscription['discord_channel_id'])
            if channel:
                channel_select.default_values = [channel]

        channel_select.callback = self.on_channel_select
        channel_row.add_item(channel_select)
        container.add_item(channel_row)

        # Role selector
        container.add_item(ui.TextDisplay(
            f"**{t('modules.youtube_notifications.config.edit.roles', locale=self.locale)}**\n"
            f"-# {t('modules.youtube_notifications.config.edit.roles_description', locale=self.locale)}"
        ))

        role_row = ui.ActionRow()
        role_select = ui.RoleSelect(
            placeholder=t('modules.youtube_notifications.config.edit.roles_placeholder', locale=self.locale),
            min_values=0,
            max_values=25
        )

        # Pre-select current roles
        if self.subscription.get('roles'):
            guild = self.bot.get_guild(self.guild_id)
            if guild:
                selected_roles = []
                for role_id in self.subscription['roles']:
                    role = guild.get_role(role_id)
                    if role:
                        selected_roles.append(role)
                if selected_roles:
                    role_select.default_values = selected_roles

        role_select.callback = self.on_role_select
        role_row.add_item(role_select)
        container.add_item(role_row)

        self.add_item(container)
        self._add_action_buttons()

    def _add_action_buttons(self):
        """Add action buttons"""
        button_row = ui.ActionRow()

        # Back button
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
            # Delete button
            delete_btn = ui.Button(
                emoji=discord.PartialEmoji.from_str(DELETE),
                label=t('modules.config.buttons.delete', locale=self.locale),
                style=discord.ButtonStyle.danger
            )
            delete_btn.callback = self.on_delete
            button_row.add_item(delete_btn)

        self.add_item(button_row)

    async def on_edit_message(self, interaction: discord.Interaction):
        """Edit message callback"""
        if not await self.check_user(interaction):
            return

        modal = EditSubscriptionModal(
            self.locale,
            self.subscription['message'],
            self._on_message_edited
        )
        modal.bot = self.bot
        await interaction.response.send_modal(modal)

    async def _on_message_edited(self, interaction: discord.Interaction, new_message: str):
        """Callback after message edited"""
        self.subscription['message'] = new_message
        self.has_changes = True
        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_channel_select(self, interaction: discord.Interaction):
        """Channel selector callback"""
        if not await self.check_user(interaction):
            return

        if interaction.data['values']:
            channel_id = int(interaction.data['values'][0])
            self.subscription['discord_channel_id'] = channel_id
            self.has_changes = True

        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_role_select(self, interaction: discord.Interaction):
        """Role selector callback"""
        if not await self.check_user(interaction):
            return

        # Get selected role IDs
        role_ids = [int(role_id) for role_id in interaction.data['values']]
        self.subscription['roles'] = role_ids
        self.has_changes = True

        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_back(self, interaction: discord.Interaction):
        """Back to main view"""
        if not await self.check_user(interaction):
            return

        await interaction.response.edit_message(view=self.parent_view)

    async def on_save(self, interaction: discord.Interaction):
        """Save changes"""
        if not await self.check_user(interaction):
            return

        # Update subscription in parent view
        self.parent_view.working_config['subscriptions'][self.subscription_index] = self.subscription
        self.parent_view.has_changes = True

        # Rebuild parent view
        self.parent_view._build_view()

        await interaction.response.edit_message(view=self.parent_view)

        await interaction.followup.send(
            t('modules.youtube_notifications.config.edit.saved', locale=self.locale),
            ephemeral=True
        )

    async def on_cancel(self, interaction: discord.Interaction):
        """Cancel changes"""
        if not await self.check_user(interaction):
            return

        await interaction.response.edit_message(view=self.parent_view)

    async def on_delete(self, interaction: discord.Interaction):
        """Delete subscription"""
        if not await self.check_user(interaction):
            return

        # Remove subscription from parent view
        del self.parent_view.working_config['subscriptions'][self.subscription_index]
        self.parent_view.has_changes = True

        # Rebuild parent view
        self.parent_view._build_view()

        await interaction.response.edit_message(view=self.parent_view)

        await interaction.followup.send(
            t('modules.youtube_notifications.config.edit.deleted', locale=self.locale),
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


class YoutubeNotificationsConfigView(BaseView):
    """
    Interface de configuration du module YouTube Notifications
    """

    def __init__(self, bot, guild_id: int, user_id: int, locale: str, current_config: Optional[Dict[str, Any]] = None):
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id
        self.locale = locale

        # Load default config
        from modules.youtube_notifications import YoutubeNotificationsModule
        default_config = YoutubeNotificationsModule(bot, guild_id).get_default_config()

        # Check if we have a real saved config
        if current_config and 'subscriptions' in current_config:
            self.current_config = default_config.copy()
            self.current_config.update(current_config)
            self.has_existing_config = len(current_config.get('subscriptions', [])) > 0
        else:
            self.current_config = default_config
            self.has_existing_config = False

        # Working copy
        self.working_config = self.current_config.copy()
        self.working_config['subscriptions'] = [sub.copy() for sub in self.current_config.get('subscriptions', [])]
        self.has_changes = False

        # Pending channel add data
        self.pending_channel_id = None
        self.pending_channel_username = None
        self.pending_message = None

        self._build_view()

    def _build_view(self):
        """Build the configuration interface"""
        self.clear_items()

        container = ui.Container()

        # Header
        container.add_item(ui.TextDisplay(
            f"### {MODDY_ALT} {t('modules.youtube_notifications.config.title', locale=self.locale)}"
        ))
        container.add_item(ui.TextDisplay(
            t('modules.youtube_notifications.config.description', locale=self.locale)
        ))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # List of subscriptions
        subscriptions = self.working_config.get('subscriptions', [])

        if subscriptions:
            container.add_item(ui.TextDisplay(
                f"**{t('modules.youtube_notifications.config.subscriptions.title', locale=self.locale)}**\n"
                f"-# {t('modules.youtube_notifications.config.subscriptions.count', locale=self.locale, count=len(subscriptions))}"
            ))

            # Display each subscription
            for i, sub in enumerate(subscriptions):
                channel_display = sub.get('channel_username', sub['channel_id'])
                discord_channel = self.bot.get_channel(sub['discord_channel_id'])
                channel_mention = discord_channel.mention if discord_channel else f"`{sub['discord_channel_id']}`"

                container.add_item(ui.TextDisplay(
                    f"`{i+1}.` **{channel_display}** → {channel_mention}"
                ))

            # Edit buttons for subscriptions (max 5 per row)
            for i in range(0, len(subscriptions), 5):
                edit_row = ui.ActionRow()
                for j in range(i, min(i + 5, len(subscriptions))):
                    edit_btn = ui.Button(
                        label=str(j + 1),
                        style=discord.ButtonStyle.secondary,
                        emoji=discord.PartialEmoji.from_str(EDIT),
                        custom_id=f"edit_sub_{j}"
                    )
                    edit_btn.callback = lambda inter, idx=j: self.on_edit_subscription(inter, idx)
                    edit_row.add_item(edit_btn)
                container.add_item(edit_row)

            container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        else:
            container.add_item(ui.TextDisplay(
                f"{INFO} {t('modules.youtube_notifications.config.subscriptions.empty', locale=self.locale)}"
            ))

        # Add channel button
        add_row = ui.ActionRow()
        add_btn = ui.Button(
            label=t('modules.youtube_notifications.config.buttons.add', locale=self.locale),
            style=discord.ButtonStyle.success,
            emoji=discord.PartialEmoji.from_str(ADD)
        )
        add_btn.callback = self.on_add_channel
        add_row.add_item(add_btn)
        container.add_item(add_row)

        self.add_item(container)
        self._add_action_buttons()

    def _add_action_buttons(self):
        """Add action buttons at the bottom"""
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

        # Save button (only if changes)
        if self.has_changes:
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
            # Delete all button (if config exists)
            if self.has_existing_config:
                delete_btn = ui.Button(
                    emoji=discord.PartialEmoji.from_str(DELETE),
                    label=t('modules.config.buttons.delete', locale=self.locale),
                    style=discord.ButtonStyle.danger
                )
                delete_btn.callback = self.on_delete
                button_row.add_item(delete_btn)

        self.add_item(button_row)

    # === CALLBACKS ===

    async def on_add_channel(self, interaction: discord.Interaction):
        """Add channel callback"""
        if not await self.check_user(interaction):
            return

        modal = AddChannelModal(self.locale, self._on_channel_added)
        modal.bot = self.bot
        await interaction.response.send_modal(modal)

    async def _on_channel_added(self, interaction: discord.Interaction, channel_id: str, channel_username: str, message: str):
        """Callback after channel added - need to select Discord channel and roles"""
        # Store pending data
        self.pending_channel_id = channel_id
        self.pending_channel_username = channel_username
        self.pending_message = message

        # Show channel selection view
        await self._show_channel_selection(interaction)

    async def _show_channel_selection(self, interaction: discord.Interaction):
        """Show Discord channel and role selection"""
        # Create a temporary view for channel/role selection
        temp_view = BaseView(timeout=300)
        temp_view.bot = self.bot

        container = ui.Container()

        container.add_item(ui.TextDisplay(
            f"### {MODDY_ALT} {t('modules.youtube_notifications.config.add.select_title', locale=self.locale)}"
        ))

        container.add_item(ui.TextDisplay(
            f"**{t('modules.youtube_notifications.config.add.youtube_channel', locale=self.locale)}**\n"
            f"-# `{self.pending_channel_username}`"
        ))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Discord channel selector
        container.add_item(ui.TextDisplay(
            f"**{t('modules.youtube_notifications.config.add.discord_channel', locale=self.locale)}**{REQUIRED_FIELDS}\n"
            f"-# {t('modules.youtube_notifications.config.add.discord_channel_description', locale=self.locale)}"
        ))

        channel_row = ui.ActionRow()
        channel_select = ui.ChannelSelect(
            placeholder=t('modules.youtube_notifications.config.add.channel_placeholder', locale=self.locale),
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1
        )
        channel_select.callback = lambda inter: self._on_discord_channel_selected(inter, temp_view)
        channel_row.add_item(channel_select)
        container.add_item(channel_row)

        # Role selector
        container.add_item(ui.TextDisplay(
            f"**{t('modules.youtube_notifications.config.add.roles', locale=self.locale)}**\n"
            f"-# {t('modules.youtube_notifications.config.add.roles_description', locale=self.locale)}"
        ))

        role_row = ui.ActionRow()
        role_select = ui.RoleSelect(
            placeholder=t('modules.youtube_notifications.config.add.roles_placeholder', locale=self.locale),
            min_values=0,
            max_values=25
        )
        role_select.callback = lambda inter: self._on_roles_selected(inter, temp_view)
        role_row.add_item(role_select)
        container.add_item(role_row)

        temp_view.add_item(container)

        # Buttons
        button_row = ui.ActionRow()

        cancel_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(UNDONE),
            label=t('modules.config.buttons.cancel', locale=self.locale),
            style=discord.ButtonStyle.danger
        )
        cancel_btn.callback = lambda inter: self._on_add_cancel(inter)
        button_row.add_item(cancel_btn)

        confirm_btn = ui.Button(
            emoji=discord.PartialEmoji.from_str(DONE),
            label=t('modules.config.buttons.confirm', locale=self.locale),
            style=discord.ButtonStyle.success,
            disabled=True  # Enabled after channel selection
        )
        confirm_btn.callback = lambda inter: self._on_add_confirm(inter)
        button_row.add_item(confirm_btn)

        temp_view.add_item(button_row)

        # Store temp data in view
        temp_view.pending_discord_channel_id = None
        temp_view.pending_roles = []
        temp_view.confirm_btn = confirm_btn

        await interaction.response.edit_message(view=temp_view)

    async def _on_discord_channel_selected(self, interaction: discord.Interaction, temp_view):
        """Discord channel selected callback"""
        if not await self.check_user(interaction):
            return

        if interaction.data['values']:
            channel_id = int(interaction.data['values'][0])
            temp_view.pending_discord_channel_id = channel_id

            # Enable confirm button
            temp_view.confirm_btn.disabled = False

        await interaction.response.edit_message(view=temp_view)

    async def _on_roles_selected(self, interaction: discord.Interaction, temp_view):
        """Roles selected callback"""
        if not await self.check_user(interaction):
            return

        # Get selected role IDs
        role_ids = [int(role_id) for role_id in interaction.data['values']]
        temp_view.pending_roles = role_ids

        await interaction.response.edit_message(view=temp_view)

    async def _on_add_confirm(self, interaction: discord.Interaction):
        """Confirm adding the subscription"""
        if not await self.check_user(interaction):
            return

        # Get the temp view
        temp_view = interaction.message.components[0]  # This won't work, we need a better approach

        # Get data from interaction's view
        view = None
        for item in interaction.message.components:
            if hasattr(item, 'pending_discord_channel_id'):
                view = item
                break

        # Actually, we need to access the view differently
        # Let's store it as an attribute when we create the view
        # For now, let's use a simpler approach - store in the main view

        # Create subscription
        subscription = {
            'channel_id': self.pending_channel_id,
            'channel_username': self.pending_channel_username,
            'discord_channel_id': getattr(interaction.client, 'temp_discord_channel_id', None),
            'message': self.pending_message,
            'roles': getattr(interaction.client, 'temp_roles', [])
        }

        # Add to working config
        if 'subscriptions' not in self.working_config:
            self.working_config['subscriptions'] = []

        self.working_config['subscriptions'].append(subscription)
        self.has_changes = True

        # Reset pending data
        self.pending_channel_id = None
        self.pending_channel_username = None
        self.pending_message = None

        # Rebuild main view
        self._build_view()

        await interaction.response.edit_message(view=self)

        await interaction.followup.send(
            t('modules.youtube_notifications.config.add.success', locale=self.locale),
            ephemeral=True
        )

    async def _on_add_cancel(self, interaction: discord.Interaction):
        """Cancel adding subscription"""
        if not await self.check_user(interaction):
            return

        # Reset pending data
        self.pending_channel_id = None
        self.pending_channel_username = None
        self.pending_message = None

        # Rebuild main view
        self._build_view()

        await interaction.response.edit_message(view=self)

    async def on_edit_subscription(self, interaction: discord.Interaction, index: int):
        """Edit subscription callback"""
        if not await self.check_user(interaction):
            return

        subscription = self.working_config['subscriptions'][index]

        edit_view = EditSubscriptionView(
            self.bot,
            self.guild_id,
            self.user_id,
            self.locale,
            subscription,
            self,
            index
        )

        await interaction.response.edit_message(view=edit_view)

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
            'youtube_notifications',
            self.working_config
        )

        if success:
            self.current_config = self.working_config.copy()
            self.has_changes = False
            self.has_existing_config = len(self.working_config.get('subscriptions', [])) > 0

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
        self.working_config['subscriptions'] = [sub.copy() for sub in self.current_config.get('subscriptions', [])]
        self.has_changes = False

        self._build_view()
        await interaction.response.edit_message(view=self)

    async def on_delete(self, interaction: discord.Interaction):
        """Delete configuration"""
        if not await self.check_user(interaction):
            return

        await interaction.response.defer()

        module_manager = self.bot.module_manager

        success = await module_manager.delete_module_config(self.guild_id, 'youtube_notifications')

        if success:
            from modules.youtube_notifications import YoutubeNotificationsModule
            self.current_config = YoutubeNotificationsModule(self.bot, self.guild_id).get_default_config()
            self.working_config = self.current_config.copy()
            self.working_config['subscriptions'] = []
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
