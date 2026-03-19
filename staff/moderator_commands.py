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
from utils.components_v2 import create_error_message, create_success_message, create_info_message, create_warning_message
from utils.emojis import EMOJIS
from utils.staff_logger import staff_logger
from staff.base import StaffCommandsCog

logger = logging.getLogger('moddy.moderator_commands')


class ModeratorCommands(StaffCommandsCog):
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
        if command_name == "interserver_info":
            await self.handle_interserver_info_command(message, args)
        elif command_name == "interserver_delete":
            await self.handle_interserver_delete_command(message, args)
        else:
            view = create_error_message(
                "Unknown Command",
                f"Moderator command `{command_name}` not found.\n\n"
                "**Note:** Blacklist commands have been replaced by the case system.\n"
                "Use `<@1373916203814490194> mod.case` commands instead."
            )
            await message.reply(view=view, mention_author=False)

    # OLD BLACKLIST COMMANDS REMOVED
    # These have been replaced by the unified case management system
    # Use: <@1373916203814490194> mod.case create @user

    async def handle_interserver_info_command(self, message: discord.Message, args: str):
        """
        Handle mod.interserver_info command - Get info about an inter-server message
        Usage: <@1373916203814490194> mod.interserver_info [moddy_id]
        """
        # Log the command
        if staff_logger:
            await staff_logger.log_command("mod", "interserver_info", message.author, args=args)

        moddy_id = args.strip().upper()
        if not moddy_id:
            view = create_error_message(
                "Invalid Usage",
                "**Usage:** `<@1373916203814490194> mod.interserver_info [moddy_id]`\n\nProvide a Moddy message ID."
            )
            await message.reply(view=view, mention_author=False)
            return

        # Get message info
        msg_data = await db.get_interserver_message(moddy_id)
        if not msg_data:
            view = create_error_message(
                "Message Not Found",
                f"No inter-server message found with ID `{moddy_id}`."
            )
            await message.reply(view=view, mention_author=False)
            return

        # Fetch author info
        author = self.bot.get_user(msg_data['author_id']) or await self.bot.fetch_user(msg_data['author_id'])

        # Format relayed messages info
        relayed_count = len(msg_data.get('relayed_messages', []))
        relayed_info = f"{relayed_count} servers"

        # Format timestamp
        timestamp = msg_data.get('timestamp', msg_data.get('created_at'))
        if timestamp:
            timestamp_str = f"<t:{int(timestamp.timestamp())}:F>"
        else:
            timestamp_str = "Unknown"

        # Create info view
        fields = [
            {'name': 'Moddy ID', 'value': f"`{msg_data['moddy_id']}`"},
            {'name': 'Author', 'value': f"{author.mention} (`{author.id}`)"},
            {'name': 'Original Server ID', 'value': f"`{msg_data['original_guild_id']}`"},
            {'name': 'Original Channel ID', 'value': f"`{msg_data['original_channel_id']}`"},
            {'name': 'Original Message ID', 'value': f"`{msg_data['original_message_id']}`"},
            {'name': 'Content', 'value': (msg_data['content'][:500] + '...' if len(msg_data['content']) > 500 else msg_data['content']) or "*No content*"},
            {'name': 'Timestamp', 'value': timestamp_str},
            {'name': 'Status', 'value': msg_data['status']},
            {'name': 'Moddy Team Message', 'value': "✅ Yes" if msg_data.get('is_moddy_team') else "❌ No"},
            {'name': 'Relayed To', 'value': relayed_info}
        ]

        view = create_info_message(
            "Inter-Server Message Info",
            f"Information about message `{moddy_id}`",
            fields=fields
        )

        await self.reply_with_tracking(message, view)

    async def handle_interserver_delete_command(self, message: discord.Message, args: str):
        """
        Handle mod.interserver_delete command - Delete an inter-server message
        Usage: <@1373916203814490194> mod.interserver_delete [moddy_id]
        """
        # Log the command
        if staff_logger:
            await staff_logger.log_command("mod", "interserver_delete", message.author, args=args)

        moddy_id = args.strip().upper()
        if not moddy_id:
            view = create_error_message(
                "Invalid Usage",
                "**Usage:** `<@1373916203814490194> mod.interserver_delete [moddy_id]`\n\nProvide a Moddy message ID."
            )
            await message.reply(view=view, mention_author=False)
            return

        # Get message info
        msg_data = await db.get_interserver_message(moddy_id)
        if not msg_data:
            view = create_error_message(
                "Message Not Found",
                f"No inter-server message found with ID `{moddy_id}`."
            )
            await message.reply(view=view, mention_author=False)
            return

        if msg_data['status'] == 'deleted':
            view = create_warning_message(
                "Already Deleted",
                f"Message `{moddy_id}` is already deleted."
            )
            await message.reply(view=view, mention_author=False)
            return

        # Delete all relayed messages
        deleted_count = 0
        relayed_messages = msg_data.get('relayed_messages', [])
        for relayed in relayed_messages:
            try:
                guild = self.bot.get_guild(relayed['guild_id'])
                if not guild:
                    continue

                channel = guild.get_channel(relayed['channel_id'])
                if not channel:
                    continue

                # Delete the message
                msg = await channel.fetch_message(relayed['message_id'])
                await msg.delete()
                deleted_count += 1
            except discord.NotFound:
                # Message already deleted
                pass
            except Exception as e:
                logger.error(f"Error deleting relayed message {relayed['message_id']}: {e}")

        # Delete original message if possible
        try:
            guild = self.bot.get_guild(msg_data['original_guild_id'])
            if guild:
                channel = guild.get_channel(msg_data['original_channel_id'])
                if channel:
                    original_msg = await channel.fetch_message(msg_data['original_message_id'])
                    await original_msg.delete()
        except:
            pass

        # Mark as deleted in DB
        await db.delete_interserver_message(moddy_id)

        # Log the action
        if staff_logger:
            await staff_logger.log_action(
                "Inter-Server Message Deleted",
                message.author,
                f"Deleted inter-server message {moddy_id}",
                additional_info={"Deleted Count": f"{deleted_count} messages"}
            )

        # Create success view
        fields = [
            {'name': 'Moddy ID', 'value': f"`{moddy_id}`"},
            {'name': 'Deleted By', 'value': message.author.mention},
            {'name': 'Messages Deleted', 'value': f"{deleted_count} relayed messages"}
        ]

        view = create_success_message(
            "Message Deleted",
            f"Inter-server message `{moddy_id}` has been deleted from all servers.",
            fields=fields
        )

        await self.reply_with_tracking(message, view)

        logger.info(f"Inter-server message {moddy_id} deleted by {message.author} ({message.author.id})")

    # OLD INTERSERVER BLACKLIST COMMANDS REMOVED
    # These have been replaced by the unified case management system
    # Use: <@1373916203814490194> mod.case create @user (and select interserver blacklist)


async def setup(bot):
    await bot.add_cog(ModeratorCommands(bot))
