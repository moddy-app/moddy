"""Automod ``use_precedents``: per-server switch for the learned precedents.

Covers the server's own precedents only (``automod_precedents``). The Moddy team's
learned references / terms are global and are NOT behind this switch.

    pytest tests/test_automod_use_precedents.py -q
"""

from __future__ import annotations

import types

import pytest

from automod.presets import blank_config, preset_config
from modules.automod_ai import AutomodModule
from modules.configs import automod_ai_common as common


class _Precedents:
    def __init__(self):
        self.asked = []

    def make_provider(self, guild_id):
        self.asked.append(guild_id)
        return lambda *a, **k: None


def _module(config=None):
    bot = types.SimpleNamespace(precedents=_Precedents())
    module = AutomodModule(bot, 99)
    return module, bot


async def _loaded(config):
    module, bot = _module()
    assert await module.load_config(config)
    return module, bot


async def test_precedents_are_used_by_default():
    module, bot = await _loaded({"notify_channel_id": 1})
    assert module.use_precedents is True
    assert module.make_precedents_provider() is not None
    assert bot.precedents.asked == [99]


async def test_an_explicit_false_switches_them_off():
    module, bot = await _loaded({"notify_channel_id": 1, "use_precedents": False})
    assert module.use_precedents is False
    assert module.make_precedents_provider() is None
    assert bot.precedents.asked == []


@pytest.mark.parametrize("raw", [True, None, "false", 0, "no"])
async def test_only_a_real_false_counts(raw):
    """A typo must not silently change moderation: anything but ``false`` keeps it on."""
    module, _ = await _loaded({"notify_channel_id": 1, "use_precedents": raw})
    assert module.use_precedents is True


async def test_the_default_config_carries_the_key():
    module, _ = _module()
    assert module.get_default_config()["use_precedents"] is True
    assert blank_config()["use_precedents"] is True


async def test_a_non_boolean_is_refused_by_the_module_validation():
    class Guild:
        def get_channel(self, cid):
            return None

    module, bot = _module()
    bot.get_guild = lambda gid: Guild()
    ok, error = await module.validate_config({"use_precedents": "false"})
    assert not ok and "précédents" in error
    ok, error = await module.validate_config({"use_precedents": False})
    assert ok, error


def test_the_panel_round_trips_the_switch():
    """The /config panel rebuilds the config from ``normalize_cfg``: without the key
    there, every panel save would silently switch precedents back on."""
    cfg = common.normalize_cfg({"notify_channel_id": 1, "use_precedents": False})
    assert cfg["use_precedents"] is False
    assert common.normalize_cfg({"notify_channel_id": 1})["use_precedents"] is True
    assert common.normalize_cfg(None)["use_precedents"] is True

    proj = common.project(cfg, "advanced")
    assert proj["use_precedents"] is False

    other = common.normalize_cfg({"notify_channel_id": 1})
    common.apply_projection(other, proj, "advanced")
    assert other["use_precedents"] is False


def test_a_preset_keeps_an_existing_choice():
    base = common.normalize_cfg({"notify_channel_id": 1, "use_precedents": False})
    assert preset_config("balanced", 1, base=base)["use_precedents"] is False
