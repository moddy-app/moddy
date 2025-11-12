"""
Communication Commands (com. prefix)
Commands for communication staff (Manager, Supervisor_Com, Communication)
"""

import discord
from discord.ext import commands
from typing import Optional
import logging
from datetime import datetime, timezone

from utils.staff_permissions import staff_permissions, CommandType
from database import db
from config import COLORS
from utils.components_v2 import create_error_message, create_info_message, EMOJIS
from utils.staff_base import StaffBaseCog

logger = logging.getLogger('moddy.communication_commands')


class CommunicationCommands(StaffBaseCog):
    """Communication commands (com. prefix)"""

    def __init__(self, bot):
        super().__init__(bot)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen for communication commands with new syntax"""
        # Ignore bots
        if message.author.bot:
            return

        # Check if staff permissions system is ready
        if not staff_permissions or not db:
            return

        # Parse command
        parsed = staff_permissions.parse_staff_command(message.content)
        if not parsed:
            return

        command_type, command_name, args = parsed

        # Only handle communication commands in this cog
        if command_type != CommandType.COMMUNICATION:
            return

        # Check permissions
        allowed, reason = await staff_permissions.check_command_permission(
            message.author.id, command_type, command_name
        )

        if not allowed:
            view = create_error_message("Permission Denied", reason)
            await self.reply_and_track(message, view=view, mention_author=False)
            return

        # Route to appropriate command
        if command_name == "help":
            await self.handle_help_command(message, args)
        else:
            view = create_error_message(
                "Unknown Command",
                f"Communication command `{command_name}` not found.\n\nCommunication commands are in development."
            )
            await self.reply_and_track(message, view=view, mention_author=False)

    async def handle_help_command(self, message: discord.Message, args: str):
        """
        Handle com.help command - Show available communication commands
        Usage: <@&1386452009678278818> com.help
        """
        view = create_info_message(
            "Communication Commands",
            "Communication command system is in development.\n\nAvailable commands will be added soon.",
            footer=f"Requested by {message.author}"
        )

        await self.reply_and_track(message, view=view, mention_author=False)


async def setup(bot):
    await bot.add_cog(CommunicationCommands(bot))
