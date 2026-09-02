"""Base class for all staff command cogs.

Its one job: **deleting a staff command deletes everything the bot said back.**
A staffer who cleans up after themselves in somebody else's channel expects the
channel to actually be clean, and a leftover card is the bot arguing with them.

Three things were missing, and all three left messages behind:

- **A command answers more than once.** `t.role` sends the window card and then
  the report; the mapping held one message id per command, so the second reply
  overwrote the first and the first stayed forever. Responses are a **list**
  now.
- **A command answers after the deletion.** `t.role` again — its report lands up
  to `WINDOW_SECONDS` after the window card, and a staffer who deleted their
  message in between got the report anyway. A command is marked as running for
  its whole execution, so a deletion during it removes what has been sent and
  what is still to come.
- **Nothing was ever forgotten.** The mapping grew for the life of the process.
  It is bounded now, oldest first.

Usage:

    class MyCog(StaffCommandsCog):
        async def handle_my_command(self, message, args):
            await self.reply_with_tracking(message, view)
"""

import logging
from collections import OrderedDict
from typing import List, Optional

import discord
from discord.ext import commands
from discord.ui import LayoutView

logger = logging.getLogger('moddy.staff_base')

#: How many commands to remember. A staffer deletes their message within
#: seconds or not at all, so this only has to outlive the slowest command.
MAX_TRACKED = 500


class StaffCommandsCog(commands.Cog):
    """Base class for all staff command cogs, with response tracking."""

    def __init__(self, bot):
        self.bot = bot
        #: command message id -> ids of every message the bot sent for it
        self.command_responses: "OrderedDict[int, List[int]]" = OrderedDict()
        #: commands deleted by their author — anything still to be sent for one
        #: of these is deleted on arrival rather than left in the channel
        self.cancelled_commands: "OrderedDict[int, bool]" = OrderedDict()

    # ------------------------------------------------------------------ #
    # Bookkeeping
    # ------------------------------------------------------------------ #
    @staticmethod
    def _bounded(store: OrderedDict, key, value) -> None:
        store[key] = value
        store.move_to_end(key)
        while len(store) > MAX_TRACKED:
            store.popitem(last=False)

    def begin_command(self, message: Optional[discord.Message]) -> None:
        """Start tracking a command, before it sends anything.

        Called by the dispatcher at the top of every message command. Without
        it a command deleted before its first reply would not be known here at
        all, and that reply would survive.
        """
        if message is not None and message.id not in self.command_responses:
            self._bounded(self.command_responses, message.id, [])

    def track_response(self, command_message_id: int,
                       response: Optional[discord.Message]) -> None:
        """Tie one bot message to the command that produced it.

        Public because a command that sends something outside
        :meth:`reply_with_tracking` still has to disappear with its command.
        """
        if response is None:
            return
        responses = self.command_responses.get(command_message_id) or []
        if response.id not in responses:
            responses.append(response.id)
        self._bounded(self.command_responses, command_message_id, responses)

    def is_cancelled(self, command_message_id: int) -> bool:
        """Whether the staffer deleted this command while it was running."""
        return command_message_id in self.cancelled_commands

    # ------------------------------------------------------------------ #
    # The listener
    # ------------------------------------------------------------------ #
    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        """Delete every response to a command whose message was just deleted."""
        responses = self.command_responses.pop(message.id, None)
        if responses is None:
            # Not a command this cog handled — nothing to clean up, and nothing
            # to remember either: only commands are tracked here.
            return

        # Remembered even when there is nothing to delete yet: the command may
        # still be running and about to answer.
        self._bounded(self.cancelled_commands, message.id, True)

        for response_id in responses:
            await self._delete_message(message.channel, response_id)

    async def _delete_message(self, channel, message_id: int) -> None:
        """Best effort, and quiet: a message already gone is the normal case."""
        try:
            response = await channel.fetch_message(message_id)
            await response.delete()
            logger.info("Auto-deleted staff response %s", message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
            logger.debug("Could not delete staff response %s: %s", message_id, e)

    # ------------------------------------------------------------------ #
    # Replying
    # ------------------------------------------------------------------ #
    async def reply_with_tracking(
        self,
        message: discord.Message,
        view: Optional[LayoutView] = None,
        content: Optional[str] = None,
        mention_author: bool = False
    ) -> Optional[discord.Message]:
        """Reply to a command message and track the reply for deletion.

        Returns ``None`` when the command was deleted while it ran: the reply
        is sent and removed again. Discord has no way to *not* send it — the
        command was already in flight — so the next best thing is that it does
        not stay.
        """
        reply_msg = await message.reply(view=view, content=content,
                                        mention_author=mention_author)
        if self.is_cancelled(message.id):
            await self._delete_message(message.channel, reply_msg.id)
            return None
        self.track_response(message.id, reply_msg)
        return reply_msg
