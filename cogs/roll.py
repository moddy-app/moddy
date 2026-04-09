"""
Roll command for Moddy
Roll a random dice with Components V2
"""

import discord
from discord import app_commands, ui
from discord.ext import commands
from typing import Optional
import random

from cogs.error_handler import BaseView
from utils.incognito import add_incognito_option, get_incognito_setting
from utils.i18n import i18n


class RollView(BaseView):
    """View to display dice roll result using Components V2.

    Non-interactive (no buttons) — only needs BaseView inheritance for the
    centralized error handler. ``timeout=None`` is inherited from BaseView.
    """

    def __init__(self, result: int, max_value: int, locale: str):
        super().__init__()
        self.result = result
        self.max_value = max_value
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
        title = i18n.get("commands.roll.view.title", locale=self.locale)
        container.add_item(ui.TextDisplay(title))

        # Add separator
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Add result
        result_text = i18n.get("commands.roll.view.result", locale=self.locale, result=self.result)
        container.add_item(ui.TextDisplay(result_text))

        # Add range info (grayed out text)
        range_text = i18n.get("commands.roll.view.range", locale=self.locale, max=self.max_value)
        container.add_item(ui.TextDisplay(range_text))

        # Add container to view
        self.add_item(container)


class Roll(commands.Cog):
    """Roll command system"""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="roll",
        description="Roll a random dice"
    )
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(
        max="Maximum dice value (optional, defaults to 6)",
        incognito="Make response visible only to you"
    )
    @add_incognito_option()
    async def roll_command(
        self,
        interaction: discord.Interaction,
        max: Optional[int] = 6,
        incognito: Optional[bool] = None
    ):
        """Roll a random dice"""
        # Get the ephemeral mode
        ephemeral = get_incognito_setting(interaction)

        # Get the user's locale
        locale = i18n.get_user_locale(interaction)

        # Validate max value
        if max < 1:
            max = 6
        elif max > 1000000:
            max = 1000000

        # Roll the dice
        result = random.randint(1, max)

        # Create the view
        view = RollView(result, max, locale)

        # Send response with Components V2
        await interaction.response.send_message(
            view=view,
            ephemeral=ephemeral
        )


async def setup(bot):
    await bot.add_cog(Roll(bot))
