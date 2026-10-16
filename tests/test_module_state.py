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


def _manage_view(state, sim=False, deletable=False, confirm=False):
    return _common.ModuleManageView("demo", state, "en-US", supports_simulation=sim,
                                    deletable=deletable, confirm_delete=confirm)


def _ids(view):
    return [getattr(i, "item", i).custom_id for i in view.walk_children()
            if getattr(getattr(i, "item", i), "custom_id", None)]


def test_manage_button_custom_id():
    btn = _common.module_manage_button("auto_role", "en-US")
    assert btn.item.custom_id == "moddy:config:manage:auto_role:open"
    assert btn.item.style == discord.ButtonStyle.secondary


def test_manage_panel_state_select_options():
    def options(state, sim):
        view = _manage_view(state, sim)
        select = next(i for i in view.walk_children()
                      if getattr(i, "custom_id", "") == "moddy:config:manage:demo:state")
        return [(o.value, o.default) for o in select.item.options]

    assert options("active", False) == [("active", True), ("paused", False)]
    assert options("paused", True) == [("active", False), ("simulation", False), ("paused", True)]


def test_manage_panel_delete_only_when_deletable_and_confirmed():
    assert not any(i.endswith((":delete", ":confirm")) for i in _ids(_manage_view("active")))
    ids = _ids(_manage_view("active", deletable=True))
    assert "moddy:config:manage:demo:delete" in ids and "moddy:config:manage:demo:confirm" not in ids
    ids = _ids(_manage_view("active", deletable=True, confirm=True))
    assert {"moddy:config:manage:demo:confirm", "moddy:config:manage:demo:cancel"} <= set(ids)
    assert "moddy:config:manage:demo:back" in ids


def test_manage_templates_match_own_custom_ids():
    btn = _common.ModuleManageButton("auto_restore_roles", "confirm")
    m = re.fullmatch(_common.ModuleManageButton.__discord_ui_compiled_template__, btn.item.custom_id)
    assert m and m["module"] == "auto_restore_roles" and m["action"] == "confirm"
    sel = _common.ModuleStateSelect("auto_restore_roles", current="active")
    m = re.fullmatch(_common.ModuleStateSelect.__discord_ui_compiled_template__, sel.item.custom_id)
    assert m and m["module"] == "auto_restore_roles"


def _interaction(bot, values=None):
    resp = MagicMock(is_done=MagicMock(side_effect=[False, True, True, True]), defer=AsyncMock(),
                     edit_message=AsyncMock())
    inter = MagicMock(client=bot, response=resp, guild_id=1, locale="en-US",
                      data={"values": values or []},
                      user=SimpleNamespace(id=2, guild_permissions=SimpleNamespace(manage_guild=True)))
    inter.edit_original_response = AsyncMock()
    inter.followup.send = AsyncMock()
    return inter


class _Demo:
    SUPPORTS_SIMULATION = False
    CONFIG_DELETABLE = True
    before_config_delete = AsyncMock()

    @staticmethod
    def stored_state(cfg):
        return cfg.get("state", "active")


async def test_state_select_sets_state_and_rerenders_panel():
    mgr = SimpleNamespace(set_module_state=AsyncMock(return_value=(True, None)),
                          get_module_config=AsyncMock(return_value={"state": "paused"}),
                          registered_modules={"demo": _Demo})
    inter = _interaction(SimpleNamespace(module_manager=mgr), values=["paused"])
    await _common.ModuleStateSelect("demo").callback(inter)
    mgr.set_module_state.assert_awaited_once_with(1, "demo", "paused", actor_id=2)
    view = inter.edit_original_response.await_args.kwargs["view"]
    assert isinstance(view, _common.ModuleManageView)


async def test_confirm_runs_hook_then_deletes(monkeypatch):
    mgr = SimpleNamespace(delete_module_config=AsyncMock(return_value=True),
                          get_module_config=AsyncMock(return_value=None),
                          registered_modules={"demo": _Demo})
    import cogs.config as cfg
    monkeypatch.setattr(cfg, "build_module_config_view", AsyncMock(return_value=MagicMock()))
    inter = _interaction(SimpleNamespace(module_manager=mgr))
    await _common.ModuleManageButton("demo", "confirm").callback(inter)
    _Demo.before_config_delete.assert_awaited_once()
    mgr.delete_module_config.assert_awaited_once_with(1, "demo")
    inter.edit_original_response.assert_awaited_once()


def test_locales_have_manage_keys():
    import json
    for loc in ("fr", "en-US", "es-ES", "pt-BR", "de"):
        c = json.load(open(f"locales/{loc}.json", encoding="utf-8"))["modules"]["config"]
        assert {"active", "paused", "simulation", "not_configured"} <= set(c["state"])
        assert c["breadcrumb"]["root"]
        m = c["manage"]
        assert m["button"] and m["title"] and m["state_placeholder"]
        assert set(m["options"]) == {"active", "paused", "simulation"}
        assert all(len(v) <= 100 for v in m["options"].values())
        assert {"title", "hint", "confirm_title", "confirm_body", "confirm"} <= set(m["delete"])


def test_stored_state_maps_legacy_flags_for_statuses():
    """The /config main list reads legacy on/off flags through stored_state()."""
    from modules.automod_ai import AutomodModule
    from modules.voice_transcription import VoiceTranscriptionModule

    assert AutomodModule.stored_state({"enabled": False, "dry_run": True}) == "paused"
    assert AutomodModule.stored_state({"dry_run": True}) == "simulation"
    assert AutomodModule.stored_state({"state": "active", "dry_run": True}) == "active"
    assert VoiceTranscriptionModule.stored_state({"enabled": False}) == "paused"
    assert VoiceTranscriptionModule.stored_state({}) == "active"
