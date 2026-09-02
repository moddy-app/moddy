"""Deleting a staff command deletes everything the bot said back.

A staffer cleaning up after themselves in somebody else's channel expects the
channel to actually be clean. Three cases used to leave a message behind, and
each one has a test here:

- a command that answers twice (the first reply was overwritten in the map);
- a command that answers *after* its message was deleted (a window, a long
  sync — the deletion had already been handled and forgotten);
- a command deleted before it ever answered.
"""

import asyncio
from types import SimpleNamespace

import pytest

from staff.base import MAX_TRACKED, StaffCommandsCog


class FakeMessage:
    """Just enough of a message to be replied to and deleted."""

    _next_id = 100

    def __init__(self, channel, author_id=7):
        FakeMessage._next_id += 1
        self.id = FakeMessage._next_id
        self.channel = channel
        self.author = SimpleNamespace(id=author_id, bot=False)
        self.deleted = False

    async def delete(self):
        self.deleted = True
        self.channel.sent.pop(self.id, None)

    async def reply(self, view=None, content=None, mention_author=False):
        return self.channel._new_bot_message()


class FakeChannel:
    def __init__(self):
        self.sent = {}

    def _new_bot_message(self):
        message = FakeMessage(self)
        self.sent[message.id] = message
        return message

    async def fetch_message(self, message_id):
        import discord

        message = self.sent.get(message_id)
        if message is None:
            raise discord.NotFound(SimpleNamespace(status=404, reason=""), "gone")
        return message


class Cog(StaffCommandsCog):
    """The base cog with no bot behind it — only the bookkeeping is tested."""

    def __init__(self):
        super().__init__(bot=SimpleNamespace(user=SimpleNamespace(id=1)))


def run(coro):
    """Own loop per call — nothing here shares state across awaits, and
    borrowing whatever loop another test left behind is how this file used to
    pass alone and fail in the suite."""
    return asyncio.run(coro)


@pytest.fixture
def channel():
    return FakeChannel()


@pytest.fixture
def cog():
    return Cog()


class TestDeletingACommand:
    def test_a_single_reply_goes_with_it(self, cog, channel):
        command = FakeMessage(channel)
        cog.begin_command(command)
        reply = run(cog.reply_with_tracking(command))

        run(cog.on_message_delete(command))

        assert reply.deleted

    def test_every_reply_goes_with_it(self, cog, channel):
        """The case that shipped broken: `t.role` sends a window card and then
        a report, and only the last one used to be remembered."""
        command = FakeMessage(channel)
        cog.begin_command(command)
        first = run(cog.reply_with_tracking(command))
        second = run(cog.reply_with_tracking(command))
        third = run(cog.reply_with_tracking(command))

        run(cog.on_message_delete(command))

        assert first.deleted and second.deleted and third.deleted

    def test_a_reply_that_lands_after_the_deletion_is_removed(self, cog, channel):
        """A window runs for over a minute. Deleting the command in the middle
        of it must not leave the report behind when it finally arrives."""
        command = FakeMessage(channel)
        cog.begin_command(command)
        early = run(cog.reply_with_tracking(command))

        run(cog.on_message_delete(command))
        late = run(cog.reply_with_tracking(command))

        assert early.deleted
        assert late is None, "a reply to a deleted command must not be returned"
        assert channel.sent == {}, "nothing may be left in the channel"

    def test_a_command_deleted_before_any_reply(self, cog, channel):
        command = FakeMessage(channel)
        cog.begin_command(command)

        run(cog.on_message_delete(command))
        reply = run(cog.reply_with_tracking(command))

        assert reply is None
        assert channel.sent == {}

    def test_an_unrelated_message_is_ignored(self, cog, channel):
        """Only commands are tracked — a random deletion must not be
        remembered, or the cog would grow a copy of the whole server."""
        somebody_else = FakeMessage(channel)

        run(cog.on_message_delete(somebody_else))

        assert not cog.is_cancelled(somebody_else.id)
        assert cog.cancelled_commands == {}

    def test_a_message_sent_outside_reply_can_still_be_tracked(self, cog, channel):
        """`track_response` is public for the commands that post something
        themselves rather than replying."""
        command = FakeMessage(channel)
        cog.begin_command(command)
        posted = channel._new_bot_message()
        cog.track_response(command.id, posted)

        run(cog.on_message_delete(command))

        assert posted.deleted

    def test_tracking_the_same_reply_twice_is_harmless(self, cog, channel):
        command = FakeMessage(channel)
        cog.begin_command(command)
        reply = channel._new_bot_message()
        cog.track_response(command.id, reply)
        cog.track_response(command.id, reply)

        assert cog.command_responses[command.id] == [reply.id]


class TestBounds:
    def test_the_map_does_not_grow_for_ever(self, cog, channel):
        """It used to be a plain dict that nothing ever removed from."""
        for _ in range(MAX_TRACKED + 50):
            cog.begin_command(FakeMessage(channel))

        assert len(cog.command_responses) == MAX_TRACKED

    def test_the_oldest_commands_are_the_ones_forgotten(self, cog, channel):
        first = FakeMessage(channel)
        cog.begin_command(first)
        for _ in range(MAX_TRACKED):
            cog.begin_command(FakeMessage(channel))

        assert first.id not in cog.command_responses
