"""
Avatar command for Moddy
Displays a user's avatar with Components V2
"""

import discord
from discord import app_commands, ui
from cogs.error_handler import BaseView
from discord.ext import commands
from typing import Optional
import aiohttp

from utils.incognito import add_incognito_option, get_incognito_setting
from utils.i18n import i18n


class AvatarView(BaseView):
    """View to display user avatar using Components V2.

    Non-interactive (no buttons) — timeout=None is inherited from BaseView.
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
        avatar_hash = self.user_data.get("avatar")
        username = self.user_data.get("username", "Unknown")

        if not avatar_hash:
            # User has no custom avatar
            container.add_item(ui.TextDisplay(f"### <:face:1439042029198770289> Avatar de **{username}**"))
            container.add_item(ui.TextDisplay(i18n.get("commands.user.errors.no_avatar", locale=self.locale)))
            self.add_item(container)
            return

        # Build avatar URL with proper extension (GIF for animated, PNG otherwise)
        extension = "gif" if avatar_hash.startswith("a_") else "png"
        avatar_url_base = f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.{extension}"

        # Avatar displayed in gallery is 256px
        avatar_url_display = f"{avatar_url_base}?size=256"

        # Add title
        container.add_item(ui.TextDisplay(f"### <:face:1439042029198770289> Avatar de **{username}**"))

        # Add MediaGallery with avatar URL (256px)
        container.add_item(
            ui.MediaGallery(
                discord.MediaGalleryItem(media=avatar_url_display)
            )
        )

        # Add download links with different sizes
        container.add_item(
            ui.TextDisplay(f"**Download:** [256]({avatar_url_base}?size=256) • [512]({avatar_url_base}?size=512) • [1024]({avatar_url_base}?size=1024) • [2048]({avatar_url_base}?size=2048)")
        )

        # Add container to view
        self.add_item(container)


class Avatar(commands.Cog):
    """Avatar command system"""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="avatar",
        description="Display a user's avatar"
    )
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(
        user="The user whose avatar you want to see",
        incognito="Make response visible only to you"
    )
    @add_incognito_option()
    async def avatar_command(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        incognito: Optional[bool] = None
    ):
        """Display a user's avatar"""
        # Get the ephemeral mode
        ephemeral = get_incognito_setting(interaction)

        # Get the user's locale
        locale = i18n.get_user_locale(interaction)

        # Send loading message
        loading_msg = i18n.get("commands.user.loading", locale=locale)
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
                    error_msg = i18n.get("commands.user.errors.not_found", locale=locale)
                    await interaction.edit_original_response(content=error_msg)
                    return

                user_data = await resp.json()

        # Create the view with user data
        view = AvatarView(user_data, locale)

        # Send response with Components V2
        # Note: Components V2 cannot be used with embeds
        await interaction.edit_original_response(
            content=None,
            view=view
        )


async def setup(bot):
    await bot.add_cog(Avatar(bot))
