"""
Staff Base Cog
Base class for all staff command cogs with automatic message deletion
"""

import discord
from discord.ext import commands
import logging
from typing import Optional, Dict

logger = logging.getLogger('moddy.staff_base')


class StaffBaseCog(commands.Cog):
    """
    Base class for all staff command cogs.

    Provides automatic message deletion functionality:
    - When a staff command message is deleted, its response is automatically deleted
    - Works for all staff commands across all cogs
    - No manual tracking required in individual command handlers
    """

    def __init__(self, bot):
        self.bot = bot
        # Store command message -> response message mapping for auto-deletion
        self.command_responses: Dict[int, int] = {}  # {command_msg_id: response_msg_id}

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        """
        Handle message deletion to auto-delete command responses.

        When a user deletes their staff command message, this automatically
        deletes the bot's response to keep channels clean.
        """
        # Check if this message is a command that has a response
        if message.id in self.command_responses:
            response_msg_id = self.command_responses[message.id]
            try:
                # Try to fetch and delete the response message
                response_msg = await message.channel.fetch_message(response_msg_id)
                await response_msg.delete()
                logger.info(f"[{self.__class__.__name__}] Auto-deleted response {response_msg_id} for deleted command {message.id}")
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
                logger.debug(f"[{self.__class__.__name__}] Could not delete response message {response_msg_id}: {e}")
            finally:
                # Clean up the mapping
                del self.command_responses[message.id]

    async def reply_and_track(self, message: discord.Message, **kwargs) -> Optional[discord.Message]:
        """
        Reply to a message and automatically track it for auto-deletion.

        This is a convenience method that:
        1. Sends a reply to the original message
        2. Automatically stores the mapping for auto-deletion
        3. Returns the sent message

        Usage:
            await self.reply_and_track(message, view=some_view, mention_author=False)

        Args:
            message: The original command message
            **kwargs: All arguments to pass to message.reply()

        Returns:
            The sent reply message, or None if sending failed
        """
        try:
            reply_msg = await message.reply(**kwargs)
            # Store for auto-deletion
            self.command_responses[message.id] = reply_msg.id
            logger.debug(f"[{self.__class__.__name__}] Tracking response {reply_msg.id} for command {message.id}")
            return reply_msg
        except Exception as e:
            logger.error(f"[{self.__class__.__name__}] Failed to send reply: {e}")
            return None

    async def send_and_track(self, message: discord.Message, **kwargs) -> Optional[discord.Message]:
        """
        Send a message to the same channel and track it for auto-deletion.

        Similar to reply_and_track but uses channel.send() instead of message.reply().
        Useful for commands that need to send without replying.

        Usage:
            await self.send_and_track(message, view=some_view)

        Args:
            message: The original command message (used to track the mapping)
            **kwargs: All arguments to pass to channel.send()

        Returns:
            The sent message, or None if sending failed
        """
        try:
            sent_msg = await message.channel.send(**kwargs)
            # Store for auto-deletion
            self.command_responses[message.id] = sent_msg.id
            logger.debug(f"[{self.__class__.__name__}] Tracking sent message {sent_msg.id} for command {message.id}")
            return sent_msg
        except Exception as e:
            logger.error(f"[{self.__class__.__name__}] Failed to send message: {e}")
            return None
