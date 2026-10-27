"""Tests for AltGuard inviter attribution (invite tracker + token payload).

    pytest tests/test_altguard_invites.py -q
"""

from types import SimpleNamespace

import discord
import pytest

from services import altguard_client
from services.altguard_client import CONSENT_VERSION, AltGuardClient
from services.altguard_invites import InviteTracker


# --------------------------------------------------------------- fixtures

def invite(code, uses, inviter_id=None, guild=None):
    inviter = SimpleNamespace(id=inviter_id) if inviter_id else None
    return SimpleNamespace(code=code, uses=uses, inviter=inviter, guild=guild)


class FakeGuild:
    def __init__(self, guild_id=1, manage_guild=True):
        self.id = guild_id
        self.me = SimpleNamespace(
            guild_permissions=SimpleNamespace(manage_guild=manage_guild))
        self.current = []
        self.error = None

    async def invites(self):
        if self.error:
            raise self.error
        return list(self.current)


def joiner(guild, user_id=42):
    return SimpleNamespace(id=user_id, guild=guild)


def forbidden():
    return discord.Forbidden(SimpleNamespace(status=403, reason="no"), "missing")


# --------------------------------------------------------------- client

class CapturingClient(AltGuardClient):
    def __init__(self):
        super().__init__(bot=None)
        self.sent = None

    async def _post(self, path, payload):
        self.sent = (path, payload)
        return {"authorization_url": "u", "expires_at": "t"}


async def test_payload_has_inviter_as_string():
    client = CapturingClient()
    await client.create_verification_token(
        discord_user_id=42, guild_id=1, inviter_id=777)
    assert client.sent[0] == "/altguard/token"
    assert client.sent[1]["inviter_id"] == "777"


@pytest.mark.parametrize("inviter", [None, 0, 42])
async def test_payload_omits_inviter(inviter):
    client = CapturingClient()
    await client.create_verification_token(
        discord_user_id=42, guild_id=1, inviter_id=inviter)
    assert "inviter_id" not in client.sent[1]


async def test_consent_version():
    assert CONSENT_VERSION == "2026-10-27"
    client = CapturingClient()
    await client.create_verification_token(discord_user_id=42, guild_id=1)
    assert client.sent[1]["consent_version"] == "2026-10-27"


def test_parse_verdict_keeps_unknown_reasons():
    out = altguard_client.parse_verdict({
        "verification_id": "v", "verdict": "flagged", "guild_id": "1",
        "discord_user_id": "2", "reasons": ["brand_new_label"], "enforced": True,
    })
    assert out["reasons"] == ["brand_new_label"]


# --------------------------------------------------------------- tracker

async def test_cold_cache_returns_none_and_stores_baseline():
    guild = FakeGuild()
    guild.current = [invite("a", 5, 100)]
    tracker = InviteTracker()
    assert await tracker.resolve_inviter(joiner(guild)) is None
    assert tracker.is_warm(guild.id)
    # The baseline was stored: a later increment is attributed.
    guild.current = [invite("a", 6, 100)]
    assert await tracker.resolve_inviter(joiner(guild)) == 100


async def test_single_increment():
    guild = FakeGuild()
    guild.current = [invite("a", 1, 100), invite("b", 3, 200)]
    tracker = InviteTracker()
    await tracker.warm(guild)
    guild.current = [invite("a", 1, 100), invite("b", 4, 200)]
    assert await tracker.resolve_inviter(joiner(guild)) == 200
    # Cache refreshed: the same state attributes nobody the second time.
    assert await tracker.resolve_inviter(joiner(guild, 43)) is None


async def test_two_increments_are_ambiguous():
    guild = FakeGuild()
    guild.current = [invite("a", 1, 100), invite("b", 3, 200)]
    tracker = InviteTracker()
    await tracker.warm(guild)
    guild.current = [invite("a", 2, 100), invite("b", 4, 200)]
    assert await tracker.resolve_inviter(joiner(guild)) is None


async def test_vanity_or_vanished_invite_is_none():
    guild = FakeGuild()
    guild.current = [invite("a", 1, 100)]
    tracker = InviteTracker()
    await tracker.warm(guild)
    guild.current = [invite("a", 1, 100)]  # no counter moved (vanity)
    assert await tracker.resolve_inviter(joiner(guild)) is None
    guild.current = []  # single-use invite consumed and deleted
    assert await tracker.resolve_inviter(joiner(guild)) is None


async def test_forbidden_returns_none_and_drops_cache():
    guild = FakeGuild()
    guild.current = [invite("a", 1, 100)]
    tracker = InviteTracker()
    await tracker.warm(guild)
    guild.error = forbidden()
    assert await tracker.resolve_inviter(joiner(guild)) is None
    assert not tracker.is_warm(guild.id)
    assert await tracker.warm(guild, force=True) is False


async def test_missing_manage_guild_is_not_tracked():
    guild = FakeGuild(manage_guild=False)
    tracker = InviteTracker()
    assert await tracker.warm(guild) is False
    assert await tracker.resolve_inviter(joiner(guild)) is None
    assert not tracker.is_warm(guild.id)


async def test_self_invite_is_none():
    guild = FakeGuild()
    guild.current = [invite("a", 1, 42)]
    tracker = InviteTracker()
    await tracker.warm(guild)
    guild.current = [invite("a", 2, 42)]
    assert await tracker.resolve_inviter(joiner(guild, 42)) is None


async def test_unknown_inviter_is_none():
    guild = FakeGuild()
    guild.current = [invite("a", 1, None)]
    tracker = InviteTracker()
    await tracker.warm(guild)
    guild.current = [invite("a", 2, None)]
    assert await tracker.resolve_inviter(joiner(guild)) is None


async def test_invite_create_and_delete_update_cache():
    guild = FakeGuild()
    tracker = InviteTracker()
    await tracker.warm(guild)

    tracker.on_invite_create(invite("new", None, 300, guild))
    assert tracker._uses[guild.id] == {"new": 0}

    # Created then used: the increment is attributed to its creator.
    guild.current = [invite("new", 1, 300)]
    assert await tracker.resolve_inviter(joiner(guild)) == 300

    tracker.on_invite_delete(invite("new", 1, 300, guild))
    assert tracker._uses[guild.id] == {}


async def test_events_ignored_for_untracked_guild():
    guild = FakeGuild()
    tracker = InviteTracker()
    tracker.on_invite_create(invite("x", 0, 1, guild))
    assert not tracker.is_warm(guild.id)
