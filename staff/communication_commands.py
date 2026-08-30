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
from utils.components_v2 import create_error_message, create_info_message
from utils.emojis import EMOJIS
from utils.staff_logger import staff_logger
from staff.base import StaffCommandsCog
from cogs.error_handler import ErrorView, report_error

logger = logging.getLogger('moddy.communication_commands')


class CommunicationCommands(StaffCommandsCog):
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
            await self.reply_with_tracking(message, view)
            return

        # `com.send` and `com.beta` live in the new framework, which answers
        # them from its own router. Without this check the migrated commands
        # would also get an "unknown command" reply from here.
        router = self.bot.get_cog("StaffCommandsRouter")
        if router is not None and router.is_migrated(command_type.value, command_name):
            return

        # A message listener has no global error handler behind it: an
        # unexpected failure here would vanish into discord.py's dispatch log
        # and leave the author with no answer at all. Route it through the same
        # central pipeline every other command uses, and show the error code.
        try:
            if command_name == "help":
                await self.handle_help_command(message, args)
            else:
                view = create_error_message(
                    "Unknown Command",
                    f"Communication command `{command_name}` not found.\n\nCommunication commands are in development."
                )
                await self.reply_with_tracking(message, view)
        except Exception as exc:
            error_code = await report_error(
                self.bot, exc, source=f"Staff:com.{command_name}",
                user=message.author, guild=message.guild, channel=message.channel,
                error_type="Staff Command Error",
            )
            view = ErrorView(error_code) if error_code else create_error_message(
                "Error", "An unexpected error occurred."
            )
            try:
                await self.reply_with_tracking(message, view)
            except discord.HTTPException as send_error:
                logger.error("CRITICAL: could not show the error card for com.%s: %s",
                             command_name, send_error)

    async def handle_help_command(self, message: discord.Message, args: str):
        """
        Handle com.help command - Show available communication commands
        Usage: <@&1386452009678278818> com.help
        """
        # Log the command
        if staff_logger:
            await staff_logger.log_command("com", "help", message.author)

        view = create_info_message(
            "Communication Commands",
            "Communication command system is in development.\n\nAvailable commands will be added soon.",
            footer=f"Requested by {message.author}"
        )

        await self.reply_with_tracking(message, view)


async def setup(bot):
    await bot.add_cog(CommunicationCommands(bot))
