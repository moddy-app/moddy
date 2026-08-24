"""Sanction expiration — Discord reversal + the DM the subject receives.

Everything here is offline: Discord objects are duck-typed stubs, no gateway
call and no database are involved.

    pytest tests/test_expiration_notifications.py -q
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import discord
import pytest
from discord import ui

from services.expiration_notifier import ExpirationNotifier
from utils.expiration_views import build_expiration_dm_view
from utils.invites import create_guild_invite


# --------------------------------------------------------------------------- #
# Stubs
# --------------------------------------------------------------------------- #

class FakeChannel:
    def __init__(self, cid=1, can_invite=True, fails=False):
        self.id = cid
        self._can_invite = can_invite
        self._fails = fails
        self.invite_calls = []

    def permissions_for(self, _me):
        return SimpleNamespace(create_instant_invite=self._can_invite)

    async def create_invite(self, **kwargs):
        if self._fails:
            raise discord.HTTPException(SimpleNamespace(status=403, reason=""), "nope")
        self.invite_calls.append(kwargs)
        return SimpleNamespace(url=f"https://discord.gg/inv{self.id}")


class FakeGuild:
    def __init__(self, gid=42, *, can_ban=True, channels=None, unban_error=None,
                 preferred_locale="fr"):
        self.id = gid
        self.name = "Test Guild"
        self.preferred_locale = preferred_locale
        self.me = SimpleNamespace(guild_permissions=SimpleNamespace(ban_members=can_ban))
        self.rules_channel = None
        self.system_channel = None
        self.text_channels = channels if channels is not None else [FakeChannel()]
        self._unban_error = unban_error
        self.unbanned = []

    async def unban(self, obj, reason=None):
        if self._unban_error is not None:
            raise self._unban_error
        self.unbanned.append((obj.id, reason))


class FakeUser:
    def __init__(self, uid=7, *, error=None):
        self.id = uid
        self._error = error
        self.sent = []

    async def send(self, **kwargs):
        if self._error is not None:
            raise self._error
        self.sent.append(kwargs)


class FakeBot:
    def __init__(self, guild=None, user=None):
        self._guild = guild
        self._user = user
        # Every DM goes through the notification system now. With no database
        # the service records nothing and sends the view as-is, which is
        # exactly what these tests assert on.
        from notifications import NotificationService
        self.notifications = NotificationService(self)
        self.db = None

    def get_guild(self, gid):
        return self._guild if self._guild and self._guild.id == gid else None

    def get_user(self, uid):
        return self._user if self._user and self._user.id == uid else None

    async def fetch_user(self, uid):
        return self.get_user(uid)


def row(action="ban", **over):
    base = {
        "action": action,
        "case_type": "guild",
        "reference": "A7F2K9",
        "subject_type": "discord_user",
        "subject_id": "7",
        "scope_type": "discord_guild",
        "scope_id": "42",
        "expires_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }
    base.update(over)
    return base


def rendered(view) -> str:
    """Every bit of text the view renders, flattened."""
    out = []

    def walk(item):
        if isinstance(item, ui.TextDisplay):
            out.append(item.content)
        for child in getattr(item, "children", []) or []:
            walk(child)

    for child in view.children:
        walk(child)
    return "\n".join(out)


def buttons(view):
    found = []

    def walk(item):
        if isinstance(item, ui.Button):
            found.append(item)
        for child in getattr(item, "children", []) or []:
            walk(child)

    for child in view.children:
        walk(child)
    return found


# --------------------------------------------------------------------------- #
# Invite helper
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_invite_uses_the_first_channel_moddy_may_invite_from():
    blocked, ok = FakeChannel(1, can_invite=False), FakeChannel(2)
    url = await create_guild_invite(FakeGuild(channels=[blocked, ok]), reason="r",
                                    max_age=10, max_uses=1)
    assert url == "https://discord.gg/inv2"
    assert ok.invite_calls[0]["max_age"] == 10


@pytest.mark.asyncio
async def test_invite_returns_none_when_no_channel_works():
    guild = FakeGuild(channels=[FakeChannel(1, can_invite=False), FakeChannel(2, fails=True)])
    assert await create_guild_invite(guild, reason="r") is None


@pytest.mark.asyncio
async def test_invite_never_raises_without_a_guild():
    assert await create_guild_invite(None, reason="r") is None


# --------------------------------------------------------------------------- #
# The DM view
# --------------------------------------------------------------------------- #

def test_expired_ban_dm_carries_the_invite_button():
    view = build_expiration_dm_view(
        locale="en-US", action="ban", guild_name="Test Guild", guild_id=42,
        reference="A7F2K9", expired_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        invite_url="https://discord.gg/inv2",
    )
    link = [b for b in buttons(view) if b.style is discord.ButtonStyle.link]
    assert [b.url for b in link] == ["https://discord.gg/inv2"]
    text = rendered(view)
    assert "A7F2K9" in text and "Test Guild" in text


@pytest.mark.parametrize("action", ["mute", "warn"])
def test_non_ban_expirations_get_no_invite(action):
    view = build_expiration_dm_view(
        locale="en-US", action=action, guild_name="Test Guild", guild_id=42,
        reference="A7F2K9",
    )
    assert buttons(view) == []


def test_unknown_action_falls_back_to_generic_wording():
    view = build_expiration_dm_view(
        locale="en-US", action="restrict", guild_name="G", guild_id=1,
    )
    text = rendered(view)
    assert "[" not in text.split("\n")[0]  # no raw i18n key leaked as a title


def test_the_dm_is_written_in_the_guild_locale():
    fr = rendered(build_expiration_dm_view(
        locale="fr", action="mute", guild_name="G", guild_id=1))
    en = rendered(build_expiration_dm_view(
        locale="en-US", action="mute", guild_name="G", guild_id=1))
    assert fr != en


# --------------------------------------------------------------------------- #
# The notifier
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_expired_ban_is_lifted_then_the_member_is_dmed_with_an_invite():
    guild, user = FakeGuild(), FakeUser()
    notifier = ExpirationNotifier(FakeBot(guild, user))

    assert await notifier.handle(row("ban")) is True
    assert guild.unbanned and guild.unbanned[0][0] == 7
    link = [b for b in buttons(user.sent[0]["view"]) if b.style is discord.ButtonStyle.link]
    assert link and link[0].url.startswith("https://discord.gg/")


@pytest.mark.asyncio
async def test_expired_mute_dms_without_touching_the_ban_list():
    guild, user = FakeGuild(), FakeUser()
    notifier = ExpirationNotifier(FakeBot(guild, user))

    assert await notifier.handle(row("mute")) is True
    assert guild.unbanned == []
    assert buttons(user.sent[0]["view"]) == []


@pytest.mark.asyncio
async def test_an_already_lifted_ban_still_notifies():
    guild = FakeGuild(unban_error=discord.NotFound(SimpleNamespace(status=404, reason=""), "gone"))
    user = FakeUser()
    notifier = ExpirationNotifier(FakeBot(guild, user))

    assert await notifier.handle(row("ban")) is True


@pytest.mark.asyncio
async def test_a_ban_that_cannot_be_lifted_promises_nothing():
    guild, user = FakeGuild(can_ban=False), FakeUser()
    notifier = ExpirationNotifier(FakeBot(guild, user))

    assert await notifier.handle(row("ban")) is False
    assert user.sent == []


@pytest.mark.asyncio
async def test_closed_dms_are_not_an_error():
    guild = FakeGuild()
    user = FakeUser(error=discord.Forbidden(SimpleNamespace(status=403, reason=""), "closed"))
    notifier = ExpirationNotifier(FakeBot(guild, user))

    assert await notifier.handle(row("mute")) is False


@pytest.mark.asyncio
async def test_a_guild_moddy_left_is_skipped():
    notifier = ExpirationNotifier(FakeBot(None, FakeUser()))
    assert await notifier.handle(row("ban")) is False


@pytest.mark.asyncio
async def test_global_sanctions_are_not_dmed_here():
    guild, user = FakeGuild(), FakeUser()
    notifier = ExpirationNotifier(FakeBot(guild, user))

    assert await notifier.handle(row("ban", case_type="global",
                                     scope_type="platform", scope_id=None)) is False
    assert user.sent == []


@pytest.mark.asyncio
async def test_process_survives_a_failing_row_and_counts_the_dms():
    guild, user = FakeGuild(), FakeUser()
    notifier = ExpirationNotifier(FakeBot(guild, user))

    sent = await notifier.process([
        row("mute"),
        row("mute", subject_id="not-an-id"),
        row("warn"),
    ])
    assert sent == 2
