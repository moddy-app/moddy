"""
Avatar command for Moddy
Displays a user's avatar with Components V2
"""

import discord
from discord import app_commands, ui
from discord.ext import commands
from typing import Optional
import aiohttp
import io
from PIL import Image

from utils.incognito import add_incognito_option, get_incognito_setting
from utils.i18n import i18n


class AvatarView(ui.LayoutView):
    """View to display user avatar using Components V2"""

    def __init__(self, user: discord.User, locale: str):
        super().__init__(timeout=180)
        self.user = user
        self.locale = locale

        # Build the view
        self.build_view()

    def build_view(self):
        """Builds the Components V2 view"""
        # Clear existing items
        self.clear_items()

        # Create main container
        container = ui.Container()

        # Add title
        title = i18n.get("commands.avatar.view.title", locale=self.locale, user=self.user.display_name)
        container.add_item(ui.TextDisplay(title))

        # Add MediaGallery with avatar
        avatar_filename = f"avatar_{self.user.id}.png"
        container.add_item(
            ui.MediaGallery(
                discord.MediaGalleryItem(
                    media=f"attachment://{avatar_filename}",
                ),
            )
        )

        # Add container to view
        self.add_item(container)

    async def download_avatar(self) -> discord.File:
        """Download the user's avatar and return as discord.File"""
        avatar_url = self.user.display_avatar.replace(size=1024, format="png").url

        async with aiohttp.ClientSession() as session:
            async with session.get(avatar_url) as resp:
                if resp.status == 200:
                    data = await resp.read()

                    # Open the image with Pillow
                    with Image.open(io.BytesIO(data)) as img:
                        # Resize the image
                        img = img.resize((239, 239), Image.Resampling.LANCZOS)

                        # Save the resized image to a BytesIO object
                        resized_avatar_bytes = io.BytesIO()
                        img.save(resized_avatar_bytes, format='PNG')
                        resized_avatar_bytes.seek(0)

                    avatar_filename = f"avatar_{self.user.id}.png"
                    return discord.File(resized_avatar_bytes, filename=avatar_filename)
                return None


class Avatar(commands.Cog):
    """Avatar command system"""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="avatar",
        description="Display a user's avatar"
    )
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

        # Create the view
        view = AvatarView(user, locale)

        # Download the avatar
        avatar_file = await view.download_avatar()

        # Send response with Components V2
        # Note: Components V2 cannot be used with embeds
        await interaction.response.send_message(
            view=view,
            file=avatar_file,
            ephemeral=ephemeral
        )


async def setup(bot):
    await bot.add_cog(Avatar(bot))
