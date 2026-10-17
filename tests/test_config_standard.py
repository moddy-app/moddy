import discord
"""/config UI standard: every module screen opens with a breadcrumb and an accent,
and the state row shows up on a saved module only while nothing is pending."""

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


def _outside_ids(view):
    """custom_ids of the action rows placed after the container."""
    return [getattr(i, "item", i).custom_id for c in view.children
            if isinstance(c, ui.ActionRow) for i in c.children]


def test_manage_button_outside_container_and_hidden_while_changes_are_pending():
    view = AutoRoleConfigView(None, 1, 2, "en-US", {"member_roles": [5], "state": "paused"})
    inside = [getattr(i, "custom_id", None) for i in _container(view).walk_children()]
    assert not any(str(i).startswith(_common._CID_STATE_PREFIX) for i in inside)
    assert not any(str(i).startswith(_common._CID_MANAGE_PREFIX) for i in inside)
    assert "moddy:config:manage:auto_role" in _outside_ids(view)
    view.has_changes = True
    view._build_view()
    assert "moddy:config:manage:auto_role" not in _outside_ids(view)


def test_manage_panel_state_select_and_delete():
    view = _common.build_manage_view("automod_ai", "simulation", "en-US", module_name="Automod",
                                     supports_simulation=True)
    container = _container(view)
    selects = [getattr(c, 'item', c) for c in container.walk_children()
               if isinstance(getattr(c, 'item', c), ui.Select)]
    assert len(selects) == 1
    options = {o.value: o.default for o in selects[0].options}
    assert options == {"active": False, "simulation": True, "paused": False}
    ids = [getattr(getattr(c, 'item', c), "custom_id", None) for c in container.walk_children()]
    assert "moddy:config:manage:automod_ai:delete" in ids
    assert container.accent_colour == discord.Colour(0xFEE75C)
    # No simulation option for a module that does not support it, no delete when not deletable.
    plain = _common.build_manage_view("tickets", "active", "en-US", module_name="Tickets",
                                      deletable=False)
    plain_ids = [getattr(getattr(c, 'item', c), "custom_id", None)
                 for c in _container(plain).walk_children()]
    assert "moddy:config:manage:tickets:delete" not in plain_ids
    sel = [getattr(c, 'item', c) for c in _container(plain).walk_children()
           if isinstance(getattr(c, 'item', c), ui.Select)][0]
    assert [o.value for o in sel.options] == ["active", "paused"]
    confirm = _common.build_manage_view("auto_role", "active", "en-US", module_name="Auto Role",
                                        confirm_delete=True)
    assert "moddy:config:manage:auto_role:delete_yes" in _outside_ids(confirm)


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


def test_voice_panel_legacy_paused_config_is_paused():
    view = VoiceTranscriptionConfigView(
        None, 1, 2, "en-US", {"mode": "button", "enabled": False})
    assert _container(view).accent_colour == discord.Colour(0x99AAB5)
    assert "moddy:config:manage:voice_transcription" in _outside_ids(view)


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
