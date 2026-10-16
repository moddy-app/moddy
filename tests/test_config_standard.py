"""/config UI standard: every module screen opens with a breadcrumb and an accent,
and the Manage button (state + deletion) sits outside the container, on a saved
module only, while nothing is pending."""

import pytest
from discord import ui

from modules.configs import _common
from modules.configs.altguard_config import AltGuardConfigView
from modules.configs.auto_restore_roles_config import AutoRestoreRolesConfigView
from modules.configs.auto_role_config import AutoRoleConfigView
from modules.configs.adaptive_slowmode_config import (
    AdaptiveSlowmodeChannelConfigView, AdaptiveSlowmodeConfigView,
)
from modules.configs.interserver_config import InterServerConfigView
from modules.configs.starboard_config import StarboardConfigView
from modules.configs.voice_transcription_config import VoiceTranscriptionConfigView
from modules.configs.bump_reminder_config import BumpReminderConfigView
from modules.configs.welcome_channel_config import WelcomeChannelConfigView
from modules.configs.welcome_dm_config import WelcomeDmConfigView
from modules.configs.tickets_config import TicketsConfigView, TicketsSettingsView
from modules.configs.logs_config import LogsConfigView, LogsOptionsView
from modules.configs.server_settings_config import ServerSettingsConfigView
from modules.configs.social_notifications_config import SocialNotificationsConfigView

SHELLS = [
    AltGuardConfigView, AutoRestoreRolesConfigView, AutoRoleConfigView,
    AdaptiveSlowmodeConfigView, AdaptiveSlowmodeChannelConfigView,
    InterServerConfigView, StarboardConfigView, VoiceTranscriptionConfigView,
    BumpReminderConfigView, WelcomeChannelConfigView, WelcomeDmConfigView,
    TicketsConfigView, TicketsSettingsView, LogsConfigView, LogsOptionsView,
    SocialNotificationsConfigView, ServerSettingsConfigView,
]


def _container(view):
    return next(c for c in view.children if isinstance(c, ui.Container))


@pytest.mark.parametrize("cls", SHELLS, ids=lambda c: c.__name__)
def test_screen_starts_with_breadcrumb_and_has_accent(cls):
    try:
        view = cls()
    except TypeError:
        pytest.skip("needs constructor arguments")
    container = _container(view)
    first = container.children[0]
    assert isinstance(first, ui.TextDisplay) and first.content.startswith("-# ")
    assert container.accent_colour is not None


_MANAGE = _common._CID_MANAGE_PREFIX


def _outside_ids(view):
    return [getattr(i, "item", i).custom_id for c in view.children
            if isinstance(c, ui.ActionRow) for i in c.children]


def test_manage_button_outside_container_and_hidden_while_changes_are_pending():
    view = AutoRoleConfigView(None, 1, 2, "en-US", {"member_roles": [5], "state": "paused"})
    inside = [getattr(c, "custom_id", None) for c in _container(view).walk_children()]
    assert not any(str(i).startswith(("moddy:config:", _MANAGE)) for i in inside)
    assert f"{_MANAGE}:auto_role:open" in _outside_ids(view)
    view.has_changes = True
    view._build_view()
    assert not any(str(i).startswith(_MANAGE) for i in _outside_ids(view))


def test_manage_button_hidden_without_config():
    view = AutoRoleConfigView(None, 1, 2, "en-US", None)
    assert not any(str(i).startswith(_MANAGE) for i in _outside_ids(view))


# --- voice transcription: legacy `enabled` flag -> generic state ------------

def test_legacy_enabled_false_becomes_paused():
    from modules.voice_transcription import migrate_legacy_enabled
    assert migrate_legacy_enabled({"mode": "button", "enabled": False}) == {
        "mode": "button", "state": "paused"}
    assert migrate_legacy_enabled({"mode": "auto", "enabled": True}) == {"mode": "auto"}
    # An explicit stored state wins over the legacy flag.
    assert migrate_legacy_enabled({"enabled": False, "state": "active"})["state"] == "active"


@pytest.mark.asyncio
async def test_voice_module_loads_legacy_config_as_paused():
    from modules.voice_transcription import VoiceTranscriptionModule
    module = VoiceTranscriptionModule(None, 1)
    config = {"mode": "button", "enabled": False}
    assert await module.load_config(config)
    module.apply_state(module.config)
    assert module.state == "paused" and module.enabled is False


@pytest.mark.asyncio
async def test_manage_panel_shows_legacy_paused_voice_config_as_paused():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from modules.voice_transcription import VoiceTranscriptionModule
    mgr = SimpleNamespace(
        registered_modules={"voice_transcription": VoiceTranscriptionModule},
        get_module_config=AsyncMock(return_value={"mode": "button", "enabled": False}))
    view = await _common.build_manage_view(SimpleNamespace(module_manager=mgr), 1, "en-US",
                                           "voice_transcription")
    text = "\n".join(c.content for c in view.walk_children() if isinstance(c, ui.TextDisplay))
    assert "Paused" in text
    select = next(i for i in view.walk_children()
                  if getattr(i, "custom_id", "") == f"{_MANAGE}:voice_transcription:state")
    assert [o.value for o in select.item.options if o.default] == ["paused"]


# --- social notifications: pause --------------------------------------------

@pytest.mark.asyncio
async def test_social_dispatch_skips_paused_guilds():
    from unittest.mock import AsyncMock, MagicMock
    from cogs.social_notifications import SocialNotifications

    channel = MagicMock()
    channel.send = AsyncMock()
    import discord
    channel.__class__ = discord.TextChannel
    guild = MagicMock(id=1)
    guild.get_channel.return_value = channel
    bot = MagicMock()
    bot.get_guild.return_value = guild
    bot.db.get_target_followers = AsyncMock(return_value=[{"guild_id": 1, "channel_id": 2}])
    bot.module_manager.get_module_state = AsyncMock(return_value="paused")
    cog = SocialNotifications.__new__(SocialNotifications)
    cog.bot = bot
    await cog._dispatch_event({"platform": "x", "target_id": "t"})
    channel.send.assert_not_called()
