"""
Moderator Commands (mod. prefix)
Commands for moderation staff (Manager, Supervisor_Mod, Moderator)
"""

import discord
from discord.ext import commands
from typing import Optional
import logging
from datetime import datetime, timezone

from utils.staff_permissions import staff_permissions, CommandType
from database import db
from config import COLORS
from utils.components_v2 import create_error_message, create_success_message, create_info_message, create_warning_message, EMOJIS
from utils.staff_base import StaffBaseCog

logger = logging.getLogger('moddy.moderator_commands')


class ModeratorCommands(StaffBaseCog):
    """Moderator commands (mod. prefix)"""

    def __init__(self, bot):
        super().__init__(bot)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen for moderator commands with new syntax"""
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

        # Only handle moderator commands in this cog
        if command_type != CommandType.MODERATOR:
            return

        # Check permissions
        allowed, reason = await staff_permissions.check_command_permission(
            message.author.id, command_type, command_name
        )

        if not allowed:
            view = create_error_message("Permission Denied", reason)
            await message.reply(view=view, mention_author=False)
            return

        # Route to appropriate command
        if command_name == "blacklist":
            await self.handle_blacklist_command(message, args)
        elif command_name == "unblacklist":
            await self.handle_unblacklist_command(message, args)
        else:
            view = create_error_message("Unknown Command", f"Moderator command `{command_name}` not found.")
            await message.reply(view=view, mention_author=False)

    async def handle_blacklist_command(self, message: discord.Message, args: str):
        """
        Handle mod.blacklist command - Blacklist a user
        Usage: <@1373916203814490194> mod.blacklist @user [reason]
        """
        parts = args.split(maxsplit=1)
        if not parts or not message.mentions:
            view = create_error_message(
                "Invalid Usage",
                "**Usage:** `<@1373916203814490194> mod.blacklist @user [reason]`\n\nMention a user to blacklist."
            )
            await message.reply(view=view, mention_author=False)
            return

        target_user = message.mentions[0]
        reason = parts[1] if len(parts) > 1 else "No reason provided"

        # Can't blacklist staff
        user_data = await db.get_user(target_user.id)
        if user_data['attributes'].get('TEAM') or self.bot.is_developer(target_user.id):
            view = create_error_message(
                "Cannot Blacklist Staff",
                "You cannot blacklist staff members."
            )
            await message.reply(view=view, mention_author=False)
            return

        # Check if already blacklisted
        if user_data['attributes'].get('BLACKLISTED'):
            view = create_warning_message(
                "Already Blacklisted",
                f"{target_user.mention} is already blacklisted."
            )
            await message.reply(view=view, mention_author=False)
            return

        # Blacklist the user
        await db.set_attribute(
            'user', target_user.id, 'BLACKLISTED', True,
            message.author.id, reason
        )

        # Create success view
        fields = [
            {'name': 'User', 'value': f"{target_user} (`{target_user.id}`)"},
            {'name': 'Moderator', 'value': message.author.mention},
            {'name': 'Reason', 'value': reason}
        ]

        view = create_success_message(
            "User Blacklisted",
            f"{target_user.mention} has been blacklisted.",
            fields=fields
        )

        await self.reply_and_track(message, view=view, mention_author=False)

        logger.info(f"User {target_user} ({target_user.id}) blacklisted by {message.author} ({message.author.id})")

    async def handle_unblacklist_command(self, message: discord.Message, args: str):
        """
        Handle mod.unblacklist command - Remove user from blacklist
        Usage: <@1373916203814490194> mod.unblacklist @user [reason]
        """
        parts = args.split(maxsplit=1)
        if not parts or not message.mentions:
            view = create_error_message(
                "Invalid Usage",
                "**Usage:** `<@1373916203814490194> mod.unblacklist @user [reason]`\n\nMention a user to unblacklist."
            )
            await message.reply(view=view, mention_author=False)
            return

        target_user = message.mentions[0]
        reason = parts[1] if len(parts) > 1 else "No reason provided"

        # Check if blacklisted
        user_data = await db.get_user(target_user.id)
        if not user_data['attributes'].get('BLACKLISTED'):
            view = create_warning_message(
                "Not Blacklisted",
                f"{target_user.mention} is not blacklisted."
            )
            await message.reply(view=view, mention_author=False)
            return

        # Remove from blacklist
        await db.set_attribute(
            'user', target_user.id, 'BLACKLISTED', None,
            message.author.id, reason
        )

        # Create success view
        fields = [
            {'name': 'User', 'value': f"{target_user} (`{target_user.id}`)"},
            {'name': 'Moderator', 'value': message.author.mention},
            {'name': 'Reason', 'value': reason}
        ]

        view = create_success_message(
            "User Unblacklisted",
            f"{target_user.mention} has been removed from the blacklist.",
            fields=fields
        )

        await self.reply_and_track(message, view=view, mention_author=False)

        logger.info(f"User {target_user} ({target_user.id}) unblacklisted by {message.author} ({message.author.id})")


async def setup(bot):
    await bot.add_cog(ModeratorCommands(bot))
