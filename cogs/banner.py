"""
Banner command for Moddy
Displays a user's banner with Components V2
"""

import discord
from discord import app_commands, ui
from discord.ext import commands
from typing import Optional
import aiohttp

from cogs.error_handler import BaseView
from utils.incognito import add_incognito_option, get_incognito_setting
from utils.i18n import i18n


class BannerView(BaseView):
    """View to display user banner using Components V2.

    Non-interactive (no buttons) — only needs BaseView inheritance for the
    centralized error handler. ``timeout=None`` is inherited from BaseView.
    """

    def __init__(self, user_data: dict, locale: str):
        super().__init__()
        self.user_data = user_data
        self.locale = locale

        # Build the view
        self.build_view()

    def build_view(self):
        """Builds the Components V2 view"""
        # Clear existing items
        self.clear_items()

        # Create main container
        container = ui.Container()

        # Get user info
        user_id = self.user_data.get("id")
        banner_hash = self.user_data.get("banner")
        username = self.user_data.get("username", "Unknown")

        if not banner_hash:
            # User has no custom banner
            container.add_item(ui.TextDisplay(f"### <:banner:1439659080472989726> {i18n.get('commands.banner.view.title', locale=self.locale, username=username)}"))
            container.add_item(ui.TextDisplay(i18n.get("commands.banner.errors.no_banner", locale=self.locale)))
            self.add_item(container)
            return

        # Build banner URL with proper extension (GIF for animated, PNG otherwise)
        extension = "gif" if banner_hash.startswith("a_") else "png"
        banner_url_base = f"https://cdn.discordapp.com/banners/{user_id}/{banner_hash}.{extension}"

        # Banner displayed in gallery is 600px
        banner_url_display = f"{banner_url_base}?size=600"

        # Add title
        container.add_item(ui.TextDisplay(f"### <:banner:1439659080472989726> {i18n.get('commands.banner.view.title', locale=self.locale, username=username)}"))

        # Add MediaGallery with banner URL (600px)
        container.add_item(
            ui.MediaGallery(
                discord.MediaGalleryItem(media=banner_url_display)
            )
        )

        # Add download links with different sizes
        download_text = i18n.get("commands.banner.view.download", locale=self.locale)
        container.add_item(
            ui.TextDisplay(f"**{download_text}** [600]({banner_url_base}?size=600) • [1024]({banner_url_base}?size=1024) • [2048]({banner_url_base}?size=2048) • [4096]({banner_url_base}?size=4096)")
        )

        # Add container to view
        self.add_item(container)


class Banner(commands.Cog):
    """Banner command system"""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="banner",
        description="Display a user's banner"
    )
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(
        user="The user whose banner you want to see",
        incognito="Make response visible only to you"
    )
    @add_incognito_option()
    async def banner_command(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        incognito: Optional[bool] = None
    ):
        """Display a user's banner"""
        # Get the ephemeral mode
        ephemeral = get_incognito_setting(interaction)

        # Get the user's locale
        locale = i18n.get_user_locale(interaction)

        # Send loading message
        loading_msg = i18n.get("commands.banner.loading", locale=locale)
        await interaction.response.send_message(loading_msg, ephemeral=ephemeral)

        # Fetch user data from Discord API
        user_id = str(user.id)
        async with aiohttp.ClientSession() as session:
            headers = {
                "Authorization": f"Bot {self.bot.http.token}",
                "User-Agent": "DiscordBot (Moddy, 1.0)"
            }

            # Get user data
            async with session.get(f"https://discord.com/api/v10/users/{user_id}", headers=headers) as resp:
                if resp.status != 200:
                    error_msg = i18n.get("commands.banner.errors.not_found", locale=locale)
                    await interaction.edit_original_response(content=error_msg)
                    return

                user_data = await resp.json()

        # Create the view with user data
        view = BannerView(user_data, locale)

        # Send response with Components V2
        # Note: Components V2 cannot be used with embeds
        await interaction.edit_original_response(
            content=None,
            view=view
        )


async def setup(bot):
    await bot.add_cog(Banner(bot))
