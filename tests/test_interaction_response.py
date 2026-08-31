"""Interaction delivery guarantees — `utils/interaction_response.py`.

The contract these tests defend is a single sentence: **an unexpected error
always reaches the user as Moddy's own card, never as Discord's "the
application did not respond"**. That failure mode is what this module exists
to make impossible, so the interesting cases here are all failure cases —
a dead interaction token (10062), a refused followup, a channel that will
not accept a message.

Everything is pure Python against stubs; no gateway, no database, no bot.

    pytest tests/test_interaction_response.py -q
"""

import asyncio
from types import SimpleNamespace

import discord
import pytest

from utils.interaction_response import (
    deliver,
    deliver_out_of_band,
    is_already_acknowledged,
    is_expired_interaction,
    safe_defer,
)


# --------------------------------------------------------------------------- #
# Stubs
# --------------------------------------------------------------------------- #

def _http_error(code: int) -> discord.HTTPException:
    """A `discord.HTTPException` carrying a Discord error code."""
    response = SimpleNamespace(status=404, reason="Not Found")
    return discord.HTTPException(response, {"code": code, "message": "boom"})


#: The one that used to end the story: the 3-second window elapsed, so every
#: call on this interaction token fails and nothing can be delivered through it.
UNKNOWN_INTERACTION = 10062
ALREADY_ACKNOWLEDGED = 40060


class FakeResponse:
    def __init__(self, *, done=False, defer_error=None, send_error=None):
        self._done = done
        self.defer_error = defer_error
        self.send_error = send_error
        self.defer_calls = []
        self.send_calls = []

    def is_done(self):
        return self._done

    async def defer(self, *, ephemeral=True, thinking=True):
        self.defer_calls.append({"ephemeral": ephemeral, "thinking": thinking})
        if self.defer_error:
            raise self.defer_error
        self._done = True

    async def send_message(self, *, content=None, view=None, ephemeral=True):
        self.send_calls.append({"content": content, "view": view, "ephemeral": ephemeral})
        if self.send_error:
            raise self.send_error
        self._done = True


class FakeFollowup:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    async def send(self, *, content=None, view=None, ephemeral=True, wait=False):
        self.calls.append({"content": content, "view": view, "ephemeral": ephemeral})
        if self.error:
            raise self.error


class FakeChannel:
    def __init__(self, error=None):
        self.error = error
        self.calls = []
        self.name = "general"

    async def send(self, *, content=None, view=None, allowed_mentions=None):
        self.calls.append({"content": content, "view": view})
        if self.error:
            raise self.error


class FakeInteraction:
    def __init__(self, *, response=None, followup=None, channel=None,
                 edit_error=None, itype=None):
        self.response = response or FakeResponse()
        self.followup = followup or FakeFollowup()
        self.channel = channel if channel is not None else FakeChannel()
        self.user = SimpleNamespace(id=42, mention="<@42>")
        self.guild_id = 7
        self.command = SimpleNamespace(qualified_name="stripe trial")
        self.type = itype or discord.InteractionType.application_command
        self.edit_error = edit_error
        self.edit_calls = []

    async def edit_original_response(self, *, content=None, view=None):
        self.edit_calls.append({"content": content, "view": view})
        if self.edit_error:
            raise self.edit_error


# --------------------------------------------------------------------------- #
# Error classification
# --------------------------------------------------------------------------- #

def test_expired_interaction_is_recognised():
    assert is_expired_interaction(_http_error(UNKNOWN_INTERACTION))
    assert not is_expired_interaction(_http_error(ALREADY_ACKNOWLEDGED))
    assert not is_expired_interaction(ValueError("not an HTTP error"))


def test_already_acknowledged_is_recognised():
    assert is_already_acknowledged(_http_error(ALREADY_ACKNOWLEDGED))
    assert not is_already_acknowledged(_http_error(UNKNOWN_INTERACTION))


# --------------------------------------------------------------------------- #
# safe_defer
# --------------------------------------------------------------------------- #

def test_safe_defer_acknowledges():
    interaction = FakeInteraction()
    assert asyncio.run(safe_defer(interaction)) is True
    assert interaction.response.defer_calls == [{"ephemeral": True, "thinking": True}]


def test_safe_defer_is_a_noop_when_already_done():
    interaction = FakeInteraction(response=FakeResponse(done=True))
    assert asyncio.run(safe_defer(interaction)) is True
    assert interaction.response.defer_calls == []


def test_safe_defer_reports_a_dead_token_instead_of_raising():
    """The exact `/manage stripe trial` failure: 10062 raised by defer itself."""
    interaction = FakeInteraction(
        response=FakeResponse(defer_error=_http_error(UNKNOWN_INTERACTION)))
    assert asyncio.run(safe_defer(interaction)) is False


def test_safe_defer_treats_a_double_acknowledgement_as_success():
    interaction = FakeInteraction(
        response=FakeResponse(defer_error=_http_error(ALREADY_ACKNOWLEDGED)))
    assert asyncio.run(safe_defer(interaction)) is True


def test_safe_defer_never_raises_on_an_unexpected_failure():
    interaction = FakeInteraction(response=FakeResponse(defer_error=RuntimeError("nope")))
    assert asyncio.run(safe_defer(interaction)) is False


def test_thinking_defaults_to_the_interaction_type():
    """A slash command needs the placeholder; a component re-rendering in
    place must not get one, or it leaves a stray "thinking…" beside the panel."""
    slash = FakeInteraction(itype=discord.InteractionType.application_command)
    asyncio.run(safe_defer(slash))
    assert slash.response.defer_calls[0]["thinking"] is True

    component = FakeInteraction(itype=discord.InteractionType.component)
    asyncio.run(safe_defer(component))
    assert component.response.defer_calls[0]["thinking"] is False


def test_explicit_thinking_wins_over_the_default():
    component = FakeInteraction(itype=discord.InteractionType.component)
    asyncio.run(safe_defer(component, thinking=True))
    assert component.response.defer_calls[0]["thinking"] is True


# --------------------------------------------------------------------------- #
# deliver — transport cascade
# --------------------------------------------------------------------------- #

def test_unacknowledged_interaction_answers_directly():
    interaction = FakeInteraction()
    assert asyncio.run(deliver(interaction, content="hi")) is True
    assert len(interaction.response.send_calls) == 1
    assert interaction.followup.calls == []


def test_acknowledged_interaction_goes_through_followup():
    interaction = FakeInteraction(response=FakeResponse(done=True))
    assert asyncio.run(deliver(interaction, content="hi")) is True
    assert len(interaction.followup.calls) == 1


def test_a_refused_followup_falls_back_to_editing():
    interaction = FakeInteraction(
        response=FakeResponse(done=True),
        followup=FakeFollowup(error=_http_error(50027)),  # invalid webhook token
    )
    assert asyncio.run(deliver(interaction, content="hi")) is True
    assert len(interaction.edit_calls) == 1


def test_a_dead_token_still_reaches_the_user_in_channel():
    """The whole point: no interaction transport can work, so post in-channel
    rather than leave the user on Discord's own failure message."""
    dead = _http_error(UNKNOWN_INTERACTION)
    channel = FakeChannel()
    interaction = FakeInteraction(
        response=FakeResponse(done=True, send_error=dead),
        followup=FakeFollowup(error=dead),
        edit_error=dead,
        channel=channel,
    )
    assert asyncio.run(deliver(interaction, content="boom")) is True
    assert len(channel.calls) == 1
    # The invoker is mentioned so the card is not mistaken for a stray message.
    assert "<@42>" in channel.calls[0]["content"]


def test_a_dead_token_short_circuits_the_other_transports():
    """Once 10062 comes back, retrying the same token is pointless."""
    dead = _http_error(UNKNOWN_INTERACTION)
    interaction = FakeInteraction(
        response=FakeResponse(done=True),
        followup=FakeFollowup(error=dead),
        edit_error=dead,
    )
    asyncio.run(deliver(interaction, content="boom"))
    assert interaction.edit_calls == []
    assert interaction.response.send_calls == []


def test_channel_fallback_can_be_refused():
    dead = _http_error(UNKNOWN_INTERACTION)
    interaction = FakeInteraction(
        response=FakeResponse(done=True, send_error=dead),
        followup=FakeFollowup(error=dead),
        edit_error=dead,
    )
    assert asyncio.run(deliver(interaction, content="boom",
                               allow_channel_fallback=False)) is False


def test_deliver_never_raises_when_every_transport_fails():
    """A second exception on the error path would silence the first."""
    dead = _http_error(UNKNOWN_INTERACTION)
    interaction = FakeInteraction(
        response=FakeResponse(done=True, send_error=dead),
        followup=FakeFollowup(error=dead),
        edit_error=dead,
        channel=FakeChannel(error=discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"), "no")),
    )
    assert asyncio.run(deliver(interaction, content="boom")) is False


def test_deliver_survives_a_non_http_failure():
    """A transport blowing up in an unforeseen way is just another dead
    transport — the cascade moves on instead of propagating."""
    interaction = FakeInteraction(response=FakeResponse(send_error=RuntimeError("nope")))
    assert asyncio.run(deliver(interaction, content="boom")) is True
    assert len(interaction.followup.calls) == 1


def test_out_of_band_delivery_without_a_channel():
    """Nothing left to try — the caller learns it, rather than believing the
    user was reached."""
    interaction = FakeInteraction()
    interaction.channel = None
    assert asyncio.run(deliver_out_of_band(interaction, content="boom")) is False


# --------------------------------------------------------------------------- #
# The incognito preference: the lookup that used to sit in front of the window
# --------------------------------------------------------------------------- #

class FakeDB:
    def __init__(self, value=None, error=None, delay=0.0):
        self.value = value
        self.error = error
        self.delay = delay
        self.calls = 0

    async def get_attribute(self, entity, entity_id, key):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.value


def _bot(db):
    return SimpleNamespace(db=db)


@pytest.fixture(autouse=True)
def _clear_incognito_cache():
    from utils import incognito
    incognito._cache.clear()
    yield
    incognito._cache.clear()


def test_incognito_preference_is_cached():
    """The read used to happen on every command, in front of the 3s window."""
    from utils.incognito import resolve_incognito
    db = FakeDB(value=False)
    assert asyncio.run(resolve_incognito(_bot(db), 1)) is False
    assert asyncio.run(resolve_incognito(_bot(db), 1)) is False
    assert db.calls == 1


def test_incognito_falls_back_to_private_without_a_preference():
    from utils.incognito import resolve_incognito
    assert asyncio.run(resolve_incognito(_bot(FakeDB(value=None)), 1)) is True


def test_incognito_default_is_honoured():
    from utils.incognito import resolve_incognito
    assert asyncio.run(resolve_incognito(_bot(FakeDB(value=None)), 1, default=False)) is False


def test_a_slow_lookup_cannot_eat_the_window():
    """A hung database degrades the visibility of one reply, never the reply."""
    from utils import incognito
    db = FakeDB(value=False, delay=5.0)
    original = incognito._LOOKUP_TIMEOUT
    incognito._LOOKUP_TIMEOUT = 0.01
    try:
        assert asyncio.run(incognito.resolve_incognito(_bot(db), 1)) is True
    finally:
        incognito._LOOKUP_TIMEOUT = original
    # A timeout is not cached: the next command gets a fresh attempt.
    assert 1 not in incognito._cache


def test_a_failing_lookup_never_raises():
    from utils.incognito import resolve_incognito
    db = FakeDB(error=RuntimeError("database is down"))
    assert asyncio.run(resolve_incognito(_bot(db), 1)) is True


def test_no_database_means_the_default():
    from utils.incognito import resolve_incognito
    assert asyncio.run(resolve_incognito(SimpleNamespace(db=None), 1)) is True


def test_invalidation_forces_a_fresh_read():
    from utils.incognito import invalidate_incognito, resolve_incognito
    db = FakeDB(value=False)
    asyncio.run(resolve_incognito(_bot(db), 1))
    invalidate_incognito(1)
    asyncio.run(resolve_incognito(_bot(db), 1))
    assert db.calls == 2
