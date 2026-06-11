"""
Linear Tracker Cog
Detects MDY-{number} references in team messages and replies with Linear issue link buttons.
"""

import re
import discord
from discord.ext import commands
from discord.ui import LayoutView
import logging

logger = logging.getLogger('moddy.cogs.linear_tracker')

TEAM_GUILD_ID = 1394001780148535387
LINEAR_BASE_URL = "https://linear.app/moddyapp/issue/"
MDY_PATTERN = re.compile(r'\bMDY-(\d{1,7})\b', re.IGNORECASE)
MAX_BUTTONS_PER_ROW = 5


class LinearTracker(commands.Cog):
    """Detects Linear issue references in team messages and replies with link buttons."""

    def __init__(self, bot):
        self.bot = bot

    async def _is_team_member(self, user_id: int) -> bool:
        """Returns True if the user is a Moddy team member."""
        if self.bot.is_developer(user_id):
            return True
        if not self.bot.db:
            return False
        user_data = await self.bot.db.get_user(user_id)
        return bool(user_data['attributes'].get('TEAM'))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Only process messages in the team guild
        if not message.guild or message.guild.id != TEAM_GUILD_ID:
            return
        if message.author.bot:
            return

        matches = MDY_PATTERN.findall(message.content)
        if not matches:
            return

        # Deduplicate while preserving order
        seen: set = set()
        ticket_numbers = []
        for num in matches:
            if num not in seen:
                seen.add(num)
                ticket_numbers.append(num)

        try:
            if not await self._is_team_member(message.author.id):
                return
        except Exception as e:
            logger.error(f"Error checking team membership for {message.author.id}: {e}")
            return

        # Build view with link buttons (max 5 per ActionRow, up to 25 total)
        view = LayoutView()
        for i in range(0, min(len(ticket_numbers), 25), MAX_BUTTONS_PER_ROW):
            row = discord.ui.ActionRow()
            for num in ticket_numbers[i:i + MAX_BUTTONS_PER_ROW]:
                row.add_item(discord.ui.Button(
                    label=f"MDY-{num}",
                    url=f"{LINEAR_BASE_URL}MDY-{num}",
                    style=discord.ButtonStyle.link
                ))
            view.add_item(row)

        try:
            await message.reply(view=view, mention_author=False)
        except discord.HTTPException as e:
            logger.error(f"Failed to reply with Linear links for message {message.id}: {e}")


async def setup(bot):
    await bot.add_cog(LinearTracker(bot))
