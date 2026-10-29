"""`t.config` — the /config panel for a staffer, which nobody else can touch.

    pytest tests/test_staff_config.py -q
"""

import json
import pathlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.configs import _common
from utils import staff_config_views as scv


def _interaction(*, manage_guild=False, ephemeral=True, has_message=True, user_id=2):
    response = MagicMock()
    response.is_done = MagicMock(return_value=False)
    response.defer = AsyncMock()
    response.send_message = AsyncMock()
    interaction = MagicMock()
    interaction.response = response
    interaction.followup.send = AsyncMock()
    interaction.guild_id = 1
    interaction.locale = "en-US"
    interaction.user = SimpleNamespace(
        id=user_id, guild_permissions=SimpleNamespace(manage_guild=manage_guild))
    interaction.message = (SimpleNamespace(flags=SimpleNamespace(ephemeral=ephemeral))
                           if has_message else None)
    return interaction


@pytest.fixture
def staff(monkeypatch):
    """Make ``can_configure_as_staff`` answer ``staff.allowed`` and count calls."""
    state = SimpleNamespace(allowed=True, calls=0)

    async def fake(bot, user_id):
        state.calls += 1
        return state.allowed

    monkeypatch.setattr(scv, "can_configure_as_staff", fake)
    return state


# --------------------------------------------------------------------------- #
# check_guild_perms — who may click in a config panel
# --------------------------------------------------------------------------- #

async def test_manage_server_needs_no_staff_lookup(staff):
    assert await _common.check_guild_perms(_interaction(manage_guild=True)) is True
    assert staff.calls == 0


async def test_staffer_passes_in_a_private_panel(staff):
    interaction = _interaction(ephemeral=True)
    assert await _common.check_guild_perms(interaction) is True
    interaction.response.send_message.assert_not_awaited()


async def test_staffer_is_refused_on_a_public_panel(staff):
    """A /config panel posted publicly stays Manage Server only, staff or not."""
    interaction = _interaction(ephemeral=False)
    assert await _common.check_guild_perms(interaction) is False
    interaction.response.send_message.assert_awaited_once()
    assert staff.calls == 0


async def test_non_staff_is_refused_even_in_a_private_panel(staff):
    staff.allowed = False
    interaction = _interaction(ephemeral=True)
    assert await _common.check_guild_perms(interaction) is False
    interaction.response.send_message.assert_awaited_once()


async def test_no_message_is_refused(staff):
    assert await _common.check_guild_perms(_interaction(has_message=False)) is False


# --------------------------------------------------------------------------- #
# The launcher card
# --------------------------------------------------------------------------- #

async def test_launcher_refuses_non_staff(staff, monkeypatch):
    staff.allowed = False
    built = AsyncMock()
    monkeypatch.setattr(scv, "build_staff_config_panel", built)
    interaction = _interaction()
    await scv.StaffConfigLauncherView().on_open(interaction)
    interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    built.assert_not_awaited()
    (args, kwargs), = interaction.followup.send.await_args_list
    assert kwargs["ephemeral"] is True and "view" not in kwargs


async def test_launcher_opens_the_panel_privately_for_staff(staff, monkeypatch):
    panel = object()
    built = AsyncMock(return_value=panel)
    monkeypatch.setattr(scv, "build_staff_config_panel", built)
    interaction = _interaction()
    await scv.StaffConfigLauncherView().on_open(interaction)
    built.assert_awaited_once()
    interaction.followup.send.assert_awaited_once_with(view=panel, ephemeral=True)


def test_launcher_custom_id_is_stable():
    ids = [getattr(i, "custom_id", None) for i in scv.StaffConfigLauncherView().walk_children()]
    assert "moddy:staffconfig:launcher:open" in ids


# --------------------------------------------------------------------------- #
# The staff check itself
# --------------------------------------------------------------------------- #

async def test_staff_check_fails_closed(monkeypatch):
    from utils import staff_permissions as sp

    class Boom:
        async def check_command_permission(self, *a):
            raise RuntimeError("db down")

    scv._staff_cache.clear()
    monkeypatch.setattr(sp, "staff_permissions", Boom())
    assert await scv.can_configure_as_staff(None, 42) is False


async def test_staff_check_asks_for_team_config_and_is_cached(monkeypatch):
    from utils import staff_permissions as sp

    seen = []

    class Perms:
        async def check_command_permission(self, user_id, command_type, name):
            seen.append((user_id, command_type, name))
            return True, ""

    scv._staff_cache.clear()
    monkeypatch.setattr(sp, "staff_permissions", Perms())
    assert await scv.can_configure_as_staff(None, 7) is True
    assert await scv.can_configure_as_staff(None, 7) is True
    assert seen == [(7, sp.CommandType.TEAM, "config")]
    scv._staff_cache.clear()


# --------------------------------------------------------------------------- #
# i18n
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("locale", ["fr", "en-US", "es-ES", "pt-BR", "de"])
def test_strings_exist_in_every_locale(locale):
    path = pathlib.Path(__file__).resolve().parent.parent / "locales" / f"{locale}.json"
    block = json.loads(path.read_text(encoding="utf-8"))["staff"]["team"]["config"]
    for key in ("title", "description", "staff_only", "open", "not_staff",
                "failed_title", "guild_only"):
        assert block.get(key), f"{locale}: staff.team.config.{key} missing"
