"""Generic module state (active / paused / simulation) and its /config helpers."""

import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from modules.module_manager import (
    ModuleBase, ModuleManager, normalize_state, STATE_ACTIVE, STATE_PAUSED,
    STATE_SIMULATION, STATUS_NOT_CONFIGURED,
)
from modules.configs import _common


class DemoModule(ModuleBase):
    MODULE_ID = "demo"
    MODULE_NAME = "Demo"
    SUPPORTS_SIMULATION = True

    async def load_config(self, config_data):
        self.config = config_data
        self.enabled = bool(config_data.get("channel"))
        return True

    async def validate_config(self, config_data):
        return True, None

    def get_default_config(self):
        return {}

    async def on_enable(self):
        self.calls.append("enable")

    async def on_disable(self):
        self.calls.append("disable")

    async def on_state_change(self, old, new):
        self.calls.append((old, new))

    calls: list = []


class PlainModule(DemoModule):
    MODULE_ID = "plain"
    SUPPORTS_SIMULATION = False


class FakeDB:
    def __init__(self, modules):
        self.modules = modules

    async def get_guild(self, gid):
        return {"data": {"modules": self.modules}}

    async def update_guild_data(self, gid, path, value):
        self.modules[path.split(".", 1)[1]] = value


def make_manager(modules):
    bot = SimpleNamespace(db=FakeDB(modules), module_slash_commands={"demo": [object()]},
                          resync_module_commands=AsyncMock())
    mgr = ModuleManager(bot)
    mgr.register_module(DemoModule)
    mgr.register_module(PlainModule)
    DemoModule.calls = []
    return mgr, bot


def test_normalize_state():
    assert normalize_state(None) == STATE_ACTIVE
    assert normalize_state("garbage") == STATE_ACTIVE
    assert normalize_state("paused") == STATE_PAUSED
    assert normalize_state("simulation") == STATE_ACTIVE
    assert normalize_state("simulation", True) == STATE_SIMULATION


def test_supported_states():
    assert DemoModule.supported_states() == ["active", "simulation", "paused"]
    assert PlainModule.supported_states() == ["active", "paused"]


async def test_load_applies_state_even_on_self_reload():
    m = DemoModule(None, 1)
    await m.load_config({"channel": 1, "state": "paused"})
    assert m.is_paused and not m.enabled
    await m.load_config({"channel": 1})
    assert m.state == STATE_ACTIVE and m.enabled
    await m.load_config({"channel": 1, "state": "simulation"})
    assert m.is_simulation and m.enabled


async def test_set_state_pause_resume_persists_and_resyncs():
    mgr, bot = make_manager({"demo": {"channel": 5}})
    await mgr.load_guild_modules(1)
    assert mgr.active_modules[1]["demo"].enabled

    ok, err = await mgr.set_module_state(1, "demo", "paused")
    assert ok and err is None
    assert bot.db.modules["demo"] == {"channel": 5, "state": "paused"}
    assert not mgr.active_modules[1]["demo"].enabled
    assert "disable" in DemoModule.calls
    bot.resync_module_commands.assert_awaited_with(1)
    assert await mgr.get_module_state(1, "demo") == "paused"

    ok, _ = await mgr.set_module_state(1, "demo", "active")
    assert ok and mgr.active_modules[1]["demo"].enabled
    assert DemoModule.calls.count("enable") >= 2  # boot + resume


async def test_set_state_errors():
    mgr, _ = make_manager({"demo": {"channel": 5}})
    assert await mgr.set_module_state(1, "nope", "paused") == (False, "unknown_module")
    assert await mgr.set_module_state(1, "plain", "simulation") == (False, "invalid_state")
    assert await mgr.set_module_state(1, "plain", "paused") == (False, "not_configured")


async def test_simulation_keeps_module_enabled_and_notifies():
    mgr, _ = make_manager({"demo": {"channel": 5}})
    await mgr.load_guild_modules(1)
    await mgr.set_module_state(1, "demo", "simulation")
    assert mgr.active_modules[1]["demo"].enabled  # commands stay visible
    assert ("active", "simulation") in DemoModule.calls


async def test_save_config_keeps_stored_state():
    mgr, bot = make_manager({"demo": {"channel": 5, "state": "paused"}})
    mgr.bot.db.get_guild = FakeDB(bot.db.modules).get_guild
    # global-sanction lookup is irrelevant here
    mgr._blocked_as_new_module = AsyncMock(return_value=None)
    ok, _ = await mgr.save_module_config(1, "demo", {"channel": 9})
    assert ok
    assert bot.db.modules["demo"] == {"channel": 9, "state": "paused"}
    assert not mgr.active_modules[1]["demo"].enabled


async def test_external_push_applies_state():
    mgr, bot = make_manager({"demo": {"channel": 5}})
    await mgr.load_guild_modules(1)
    bot.db.modules["demo"] = {"channel": 5, "state": "paused"}  # dashboard write
    result = await mgr.reload_module(1, "demo")
    assert result["ok"] and result["state"] == "paused" and not result["enabled"]
    assert ("active", "paused") in DemoModule.calls
    bot.resync_module_commands.assert_awaited()


async def test_statuses():
    mgr, _ = make_manager({"demo": {"channel": 5, "state": "simulation"}})
    st = await mgr.get_module_statuses(1)
    assert st["demo"] == "simulation"
    assert st["plain"] == STATUS_NOT_CONFIGURED


# --- helpers ---------------------------------------------------------------

def test_state_accent():
    from config import COLORS
    assert _common.state_accent(None).value == COLORS["primary"]
    assert _common.state_accent("paused").value == COLORS["neutral"]
    assert _common.state_accent("simulation").value == COLORS["warning"]


def test_breadcrumb_text():
    crumb = _common.breadcrumb("en-US", "Automod", "Sanctions", "Doxxing")
    assert crumb.content == "-# Config › Automod › Sanctions › Doxxing"


def test_state_row_hidden_without_config():
    assert _common.module_state_row("demo", None, "en-US") == []


def test_state_row_buttons():
    def ids(state, sim):
        items = _common.module_state_row("demo", state, "en-US", supports_simulation=sim)
        return [b.custom_id for b in items[1].children]

    assert ids("active", False) == ["moddy:config:state:demo:paused"]
    assert ids("paused", False) == ["moddy:config:state:demo:active"]
    assert ids("active", True) == ["moddy:config:state:demo:simulation",
                                   "moddy:config:state:demo:paused"]
    assert ids("simulation", True) == ["moddy:config:state:demo:active",
                                       "moddy:config:state:demo:paused"]


def test_state_button_template_matches_own_custom_id():
    btn = _common.ModuleStateButton("auto_restore_roles", "paused")
    m = re.fullmatch(_common.ModuleStateButton.__discord_ui_compiled_template__,
                     btn.item.custom_id)
    assert m and m["module"] == "auto_restore_roles" and m["target"] == "paused"


async def test_button_callback_sets_state(monkeypatch):
    mgr = SimpleNamespace(set_module_state=AsyncMock(return_value=(True, None)),
                          get_module_config=AsyncMock(return_value={"channel": 1}))
    bot = SimpleNamespace(module_manager=mgr)
    resp = MagicMock(is_done=MagicMock(return_value=False), defer=AsyncMock(),
                     edit_message=AsyncMock())
    inter = MagicMock(client=bot, response=resp, guild_id=1, locale="en-US",
                      user=SimpleNamespace(id=2, guild_permissions=SimpleNamespace(manage_guild=True)))
    inter.edit_original_response = AsyncMock()
    import cogs.config as cfg
    monkeypatch.setattr(cfg, "build_module_config_view", AsyncMock(return_value=MagicMock()))
    resp.is_done = MagicMock(side_effect=[False, True])
    await _common.ModuleStateButton("demo", "paused").callback(inter)
    mgr.set_module_state.assert_awaited_once_with(1, "demo", "paused", actor_id=2)
    inter.edit_original_response.assert_awaited_once()


def test_locales_have_state_keys():
    import json
    for loc in ("fr", "en-US", "es-ES", "pt-BR", "de"):
        c = json.load(open(f"locales/{loc}.json", encoding="utf-8"))["modules"]["config"]
        assert {"active", "paused", "simulation", "not_configured"} <= set(c["state"])
        assert c["breadcrumb"]["root"]
        assert set(c["state"]["buttons"]) == {"pause", "resume", "activate", "simulation"}
