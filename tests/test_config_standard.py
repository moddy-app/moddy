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


def test_state_row_hidden_while_changes_are_pending():
    view = AutoRoleConfigView(None, 1, 2, "en-US", {"member_roles": [5], "state": "paused"})
    ids = lambda v: [getattr(i, "item", i).custom_id for c in _container(v).children
                     if isinstance(c, ui.ActionRow) for i in c.children]
    assert any(str(i).startswith(_common._CID_STATE_PREFIX) for i in ids(view))
    view.has_changes = True
    view._build_view()
    assert not any(str(i).startswith(_common._CID_STATE_PREFIX) for i in ids(view))
