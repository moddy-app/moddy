"""
Webhook management command for Moddy
Allows users to inspect and manage Discord webhooks
"""

import discord
from discord import app_commands, ui
from discord.ext import commands
from typing import Optional
import aiohttp
import re

from utils.embeds import ModdyEmbed, ModdyResponse, ModdyColors
from utils.incognito import add_incognito_option, get_incognito_setting
from utils.i18n import i18n
from cogs.error_handler import BaseView, BaseModal


class WebhookView(BaseView):
    """View to manage webhook with Components V2"""

    def __init__(self, webhook_data: dict, author: discord.User, locale: str):
        super().__init__(timeout=300)
        self.webhook_data = webhook_data
        self.author = author
        self.locale = locale
        self.build_view()

    def build_view(self):
        """Builds the Components V2 view"""
        self.clear_items()

        # Create main container
        container = ui.Container()

        # Webhook info display
        webhook_info = self.format_webhook_info()
        container.add_item(ui.TextDisplay(webhook_info))

        # Add separator
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Add instruction text
        instruction = i18n.get("commands.webhook.info.instruction", locale=self.locale)
        container.add_item(ui.TextDisplay(instruction))

        # Add container to view
        self.add_item(container)

        # Create buttons manually
        delete_btn = ui.Button(
            label=i18n.get("commands.webhook.buttons.delete", locale=self.locale),
            style=discord.ButtonStyle.danger,
            emoji="<:delete:1401600770431909939>",
            custom_id="delete_webhook"
        )
        delete_btn.callback = self.delete_webhook

        edit_btn = ui.Button(
            label=i18n.get("commands.webhook.buttons.edit", locale=self.locale),
            style=discord.ButtonStyle.primary,
            emoji="<:edit:1401600709824086169>",
            custom_id="edit_webhook"
        )
        edit_btn.callback = self.show_edit_modal

        send_btn = ui.Button(
            label=i18n.get("commands.webhook.buttons.send", locale=self.locale),
            style=discord.ButtonStyle.success,
            emoji="<:send:1519793414185553940>",
            custom_id="send_webhook"
        )
        send_btn.callback = self.show_send_modal

        refresh_btn = ui.Button(
            label=i18n.get("commands.webhook.buttons.refresh", locale=self.locale),
            style=discord.ButtonStyle.secondary,
            emoji="<:sync:1398729150885269546>",
            custom_id="refresh_webhook"
        )
        refresh_btn.callback = self.refresh_webhook

        # Create ActionRow for buttons (required for Components V2)
        button_row = ui.ActionRow()
        button_row.add_item(delete_btn)
        button_row.add_item(edit_btn)
        button_row.add_item(send_btn)
        button_row.add_item(refresh_btn)

        # Add button row to the view
        self.add_item(button_row)

    def format_webhook_info(self) -> str:
        """Formats webhook information for display"""
        data = self.webhook_data

        # Get webhook type
        webhook_type_key = {
            1: "commands.webhook.types.incoming",
            2: "commands.webhook.types.follower",
            3: "commands.webhook.types.application"
        }
        type_key = webhook_type_key.get(data.get('type', 1), "commands.webhook.types.unknown")
        webhook_type = i18n.get(type_key, locale=self.locale)

        # Avatar URL
        avatar_url = None
        if data.get('avatar'):
            avatar_url = f"https://cdn.discordapp.com/avatars/{data['id']}/{data['avatar']}.png?size=128"

        # Format info
        info = i18n.get("commands.webhook.info.title", locale=self.locale) + "\n\n"
        info += i18n.get("commands.webhook.info.fields.name", locale=self.locale, name=data.get('name', 'Unknown')) + "\n"
        info += i18n.get("commands.webhook.info.fields.id", locale=self.locale, id=data.get('id', 'N/A')) + "\n"
        info += i18n.get("commands.webhook.info.fields.type", locale=self.locale, type=webhook_type) + "\n"
        info += i18n.get("commands.webhook.info.fields.channel", locale=self.locale, channel_id=data.get('channel_id', '0')) + "\n"
        info += i18n.get("commands.webhook.info.fields.guild_id", locale=self.locale, guild_id=data.get('guild_id', 'N/A')) + "\n"

        if avatar_url:
            info += i18n.get("commands.webhook.info.fields.avatar", locale=self.locale, url=avatar_url) + "\n"

        return info

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Checks that it's the author using the buttons"""
        if interaction.user != self.author:
            await interaction.response.send_message(
                i18n.get("commands.webhook.errors.author_only", locale=self.locale),
                ephemeral=True
            )
            return False
        return True

    async def delete_webhook(self, interaction: discord.Interaction):
        """Deletes the webhook"""
        webhook_url = self.webhook_data.get('url')

        if not webhook_url:
            error_title = i18n.get("commands.webhook.errors.url_not_found.title", locale=self.locale)
            error_desc = i18n.get("commands.webhook.errors.url_not_found.description", locale=self.locale)
            await interaction.response.send_message(
                embed=ModdyResponse.error(error_title, error_desc),
                ephemeral=True
            )
            return

        # Confirm deletion
        await interaction.response.defer(ephemeral=True)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.delete(webhook_url) as response:
                    if response.status == 204:
                        # Success
                        success_title = i18n.get("commands.webhook.delete.success.title", locale=self.locale)
                        success_desc = i18n.get("commands.webhook.delete.success.description", locale=self.locale, name=self.webhook_data.get('name'))
                        success_embed = ModdyResponse.success(success_title, success_desc)

                        # Disable all buttons (they're inside an ActionRow)
                        for item in self.children:
                            if isinstance(item, ui.ActionRow):
                                for button in item.children:
                                    if isinstance(button, ui.Button):
                                        button.disabled = True

                        await interaction.edit_original_response(view=self)
                        await interaction.followup.send(embed=success_embed, ephemeral=True)
                    else:
                        error_text = await response.text()
                        error_title = i18n.get("commands.webhook.delete.failed.title", locale=self.locale)
                        error_desc = i18n.get("commands.webhook.delete.failed.description", locale=self.locale, status=response.status, error=error_text[:100])
                        error_embed = ModdyResponse.error(error_title, error_desc)
                        await interaction.followup.send(embed=error_embed, ephemeral=True)
        except Exception as e:
            error_title = i18n.get("commands.webhook.errors.generic.title", locale=self.locale)
            error_desc = i18n.get("commands.webhook.errors.generic.description", locale=self.locale, error=str(e)[:100])
            error_embed = ModdyResponse.error(error_title, error_desc)
            await interaction.followup.send(embed=error_embed, ephemeral=True)

    async def show_edit_modal(self, interaction: discord.Interaction):
        """Shows modal to edit webhook"""
        modal = EditWebhookModal(self.webhook_data, self, self.locale)
        await interaction.response.send_modal(modal)

    async def show_send_modal(self, interaction: discord.Interaction):
        """Shows modal to send a message via webhook"""
        modal = SendMessageModal(self.webhook_data, self.locale)
        await interaction.response.send_modal(modal)

    async def refresh_webhook(self, interaction: discord.Interaction):
        """Refreshes webhook information"""
        await interaction.response.defer()

        webhook_url = self.webhook_data.get('url')

        if not webhook_url:
            error_title = i18n.get("commands.webhook.errors.url_not_found.title", locale=self.locale)
            error_desc = i18n.get("commands.webhook.errors.url_not_found.description", locale=self.locale)
            await interaction.followup.send(
                embed=ModdyResponse.error(error_title, error_desc),
                ephemeral=True
            )
            return

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(webhook_url) as response:
                    if response.status == 200:
                        self.webhook_data = await response.json()
                        self.build_view()
                        await interaction.edit_original_response(view=self)

                        success_title = i18n.get("commands.webhook.refresh.success.title", locale=self.locale)
                        success_desc = i18n.get("commands.webhook.refresh.success.description", locale=self.locale)
                        await interaction.followup.send(
                            embed=ModdyResponse.success(success_title, success_desc),
                            ephemeral=True
                        )
                    else:
                        error_title = i18n.get("commands.webhook.refresh.failed.title", locale=self.locale)
                        error_desc = i18n.get("commands.webhook.refresh.failed.description", locale=self.locale, status=response.status)
                        error_embed = ModdyResponse.error(error_title, error_desc)
                        await interaction.followup.send(embed=error_embed, ephemeral=True)
        except Exception as e:
            error_title = i18n.get("commands.webhook.errors.generic.title", locale=self.locale)
            error_desc = i18n.get("commands.webhook.errors.generic.description", locale=self.locale, error=str(e)[:100])
            error_embed = ModdyResponse.error(error_title, error_desc)
            await interaction.followup.send(embed=error_embed, ephemeral=True)


class EditWebhookModal(BaseModal):
    """Modal to edit webhook name and avatar"""

    def __init__(self, webhook_data: dict, view: WebhookView, locale: str):
        self.locale = locale
        modal_title = i18n.get("commands.webhook.edit.modal_title", locale=locale)
        super().__init__(title=modal_title)
        self.webhook_data = webhook_data
        self.view = view

        # Add name input
        self.name_input = ui.TextInput(
            label=i18n.get("commands.webhook.edit.name_label", locale=locale),
            placeholder=i18n.get("commands.webhook.edit.name_placeholder", locale=locale),
            default=webhook_data.get('name', ''),
            max_length=80,
            required=True
        )
        self.add_item(self.name_input)

    async def on_submit(self, interaction: discord.Interaction):
        """Handle modal submission"""
        await interaction.response.defer(ephemeral=True)

        webhook_url = self.webhook_data.get('url')
        new_name = self.name_input.value

        if not webhook_url:
            error_title = i18n.get("commands.webhook.errors.url_not_found.title", locale=self.locale)
            error_desc = i18n.get("commands.webhook.errors.url_not_found.description", locale=self.locale)
            await interaction.followup.send(
                embed=ModdyResponse.error(error_title, error_desc),
                ephemeral=True
            )
            return

        try:
            async with aiohttp.ClientSession() as session:
                async with session.patch(webhook_url, json={"name": new_name}) as response:
                    if response.status == 200:
                        updated_data = await response.json()
                        self.view.webhook_data = updated_data
                        self.view.build_view()

                        await interaction.edit_original_response(view=self.view)

                        success_title = i18n.get("commands.webhook.edit.success.title", locale=self.locale)
                        success_desc = i18n.get("commands.webhook.edit.success.description", locale=self.locale, name=new_name)
                        success_embed = ModdyResponse.success(success_title, success_desc)
                        await interaction.followup.send(embed=success_embed, ephemeral=True)
                    else:
                        error_text = await response.text()
                        error_title = i18n.get("commands.webhook.edit.failed.title", locale=self.locale)
                        error_desc = i18n.get("commands.webhook.edit.failed.description", locale=self.locale, status=response.status, error=error_text[:100])
                        error_embed = ModdyResponse.error(error_title, error_desc)
                        await interaction.followup.send(embed=error_embed, ephemeral=True)
        except Exception as e:
            error_title = i18n.get("commands.webhook.errors.generic.title", locale=self.locale)
            error_desc = i18n.get("commands.webhook.errors.generic.description", locale=self.locale, error=str(e)[:100])
            error_embed = ModdyResponse.error(error_title, error_desc)
            await interaction.followup.send(embed=error_embed, ephemeral=True)


class SendMessageModal(BaseModal):
    """Modal to send a message through the webhook"""

    def __init__(self, webhook_data: dict, locale: str):
        self.locale = locale
        modal_title = i18n.get("commands.webhook.send.modal_title", locale=locale)
        super().__init__(title=modal_title)
        self.webhook_data = webhook_data

        # Message content input
        self.message_input = ui.TextInput(
            label=i18n.get("commands.webhook.send.message_label", locale=locale),
            placeholder=i18n.get("commands.webhook.send.message_placeholder", locale=locale),
            style=discord.TextStyle.paragraph,
            max_length=2000,
            required=True
        )
        self.add_item(self.message_input)

        # Username override (optional)
        self.username_input = ui.TextInput(
            label=i18n.get("commands.webhook.send.username_label", locale=locale),
            placeholder=i18n.get("commands.webhook.send.username_placeholder", locale=locale),
            max_length=80,
            required=False
        )
        self.add_item(self.username_input)

    async def on_submit(self, interaction: discord.Interaction):
        """Handle modal submission"""
        await interaction.response.defer(ephemeral=True)

        webhook_url = self.webhook_data.get('url')
        message_content = self.message_input.value
        username = self.username_input.value or None

        if not webhook_url:
            error_title = i18n.get("commands.webhook.errors.url_not_found.title", locale=self.locale)
            error_desc = i18n.get("commands.webhook.errors.url_not_found.description", locale=self.locale)
            await interaction.followup.send(
                embed=ModdyResponse.error(error_title, error_desc),
                ephemeral=True
            )
            return

        # Build payload
        payload = {"content": message_content}
        if username:
            payload["username"] = username

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(webhook_url, json=payload) as response:
                    if response.status in [200, 204]:
                        success_title = i18n.get("commands.webhook.send.success.title", locale=self.locale)
                        success_desc = i18n.get("commands.webhook.send.success.description", locale=self.locale)
                        success_embed = ModdyResponse.success(success_title, success_desc)
                        await interaction.followup.send(embed=success_embed, ephemeral=True)
                    else:
                        error_text = await response.text()
                        error_title = i18n.get("commands.webhook.send.failed.title", locale=self.locale)
                        error_desc = i18n.get("commands.webhook.send.failed.description", locale=self.locale, status=response.status, error=error_text[:100])
                        error_embed = ModdyResponse.error(error_title, error_desc)
                        await interaction.followup.send(embed=error_embed, ephemeral=True)
        except Exception as e:
            error_title = i18n.get("commands.webhook.errors.generic.title", locale=self.locale)
            error_desc = i18n.get("commands.webhook.errors.generic.description", locale=self.locale, error=str(e)[:100])
            error_embed = ModdyResponse.error(error_title, error_desc)
            await interaction.followup.send(embed=error_embed, ephemeral=True)


class Webhook(commands.Cog):
    """Webhook management system"""

    def __init__(self, bot):
        self.bot = bot

    def extract_webhook_info(self, webhook_input: str) -> tuple[Optional[str], Optional[str]]:
        """
        Extracts webhook ID and token from a URL or token string

        Returns:
            tuple: (webhook_id, webhook_token) or (None, None) if invalid
        """
        # Pattern for full webhook URL
        url_pattern = r'https?://discord\.com/api/webhooks/(\d+)/([a-zA-Z0-9_-]+)'

        # Try to match full URL
        match = re.match(url_pattern, webhook_input)
        if match:
            return match.group(1), match.group(2)

        # Pattern for ID/Token format
        id_token_pattern = r'^(\d+)/([a-zA-Z0-9_-]+)$'
        match = re.match(id_token_pattern, webhook_input)
        if match:
            return match.group(1), match.group(2)

        return None, None

    async def fetch_webhook_data(self, webhook_id: str, webhook_token: str) -> Optional[dict]:
        """Fetches webhook data from Discord API"""
        webhook_url = f"https://discord.com/api/webhooks/{webhook_id}/{webhook_token}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(webhook_url) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        return None
        except Exception:
            return None

    @app_commands.command(name="webhook", description="Inspect and manage Discord webhooks")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(
        webhook="Webhook URL or ID/Token",
        incognito="Make response visible only to you"
    )
    @add_incognito_option()
    async def webhook_command(
        self,
        interaction: discord.Interaction,
        webhook: str,
        incognito: Optional[bool] = None
    ):
        """Main webhook management command"""

        # Get the user's locale from Discord
        locale = i18n.get_user_locale(interaction)

        # Get the ephemeral mode
        ephemeral = get_incognito_setting(interaction)

        # Show loading message
        loading_msg = i18n.get("commands.webhook.loading", locale=locale)
        await interaction.response.send_message(
            content=loading_msg,
            ephemeral=ephemeral
        )

        # Extract webhook info
        webhook_id, webhook_token = self.extract_webhook_info(webhook)

        if not webhook_id or not webhook_token:
            error_title = i18n.get("commands.webhook.errors.invalid_webhook.title", locale=locale)
            error_desc = i18n.get("commands.webhook.errors.invalid_webhook.description", locale=locale)
            error_embed = ModdyResponse.error(error_title, error_desc)
            await interaction.edit_original_response(content=None, embed=error_embed)
            return

        # Fetch webhook data
        webhook_data = await self.fetch_webhook_data(webhook_id, webhook_token)

        if not webhook_data:
            error_title = i18n.get("commands.webhook.errors.not_found.title", locale=locale)
            error_desc = i18n.get("commands.webhook.errors.not_found.description", locale=locale)
            error_embed = ModdyResponse.error(error_title, error_desc)
            await interaction.edit_original_response(content=None, embed=error_embed)
            return

        # Create the view with webhook management
        view = WebhookView(webhook_data, interaction.user, locale)

        await interaction.edit_original_response(content=None, embed=None, view=view)


async def setup(bot):
    await bot.add_cog(Webhook(bot))
