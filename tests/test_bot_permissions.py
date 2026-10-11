"""Per-module bot permissions: baseline merge, all-at-once reporting, no admin."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from modules.module_manager import ModuleBase
from utils.bot_permissions import (
    BASELINE_BOT_PERMISSIONS,
    channel_map,
    channel_validation_error,
    format_missing,
    missing_channel_permissions,
    missing_guild_permissions,
)

LOCALES = ["fr", "en-US", "es-ES", "pt-BR", "de"]


def _module_classes():
    import importlib
    import inspect
    import pkgutil
    import modules

    found = {}
    for info in pkgutil.iter_modules(modules.__path__):
        if info.name in ("module_manager",) or info.name.startswith("_"):
            continue
        mod = importlib.import_module(f"modules.{info.name}")
        for _, cls in inspect.getmembers(mod, inspect.isclass):
            if issubclass(cls, ModuleBase) and cls is not ModuleBase and cls.__module__ == mod.__name__:
                found[cls.MODULE_ID] = cls
    return found


MODULES = _module_classes()


def _guild(held=(), overwrites=None):
    perms = discord.Permissions.none()
    for key in held:
        setattr(perms, key, True)
    me = SimpleNamespace(guild_permissions=perms)
    channels = {}
    for cid, ch_held in (overwrites or {}).items():
        cp = discord.Permissions.none()
        for key in ch_held:
            setattr(cp, key, True)
        channels[cid] = SimpleNamespace(
            id=cid, mention=f"<#{cid}>", permissions_for=lambda m, cp=cp: cp)
    return SimpleNamespace(
        me=me, id=1, get_channel_or_thread=lambda cid: channels.get(cid))


def test_modules_discovered():
    assert len(MODULES) >= 15


@pytest.mark.parametrize("module_id", sorted(MODULES))
def test_no_module_requires_administrator(module_id):
    cls = MODULES[module_id]
    assert "administrator" not in cls.required_permissions()
    for key in cls.required_permissions():
        assert hasattr(discord.Permissions, key), key


@pytest.mark.parametrize("module_id", sorted(MODULES))
def test_baseline_included_for_message_sending_modules(module_id):
    cls = MODULES[module_id]
    required = cls.required_permissions()
    if cls.SENDS_MESSAGES:
        for key in BASELINE_BOT_PERMISSIONS:
            assert (key in required) != (key in cls.BASELINE_OPTIONAL)
    for key in cls.REQUIRED_BOT_PERMISSIONS:
        assert key in required
    assert len(required) == len(set(required))


def test_baseline_covers_sending_and_emojis():
    assert {"view_channel", "send_messages", "use_external_emojis",
            "read_message_history"} <= set(BASELINE_BOT_PERMISSIONS)


def test_non_sending_modules_opt_out():
    assert "send_messages" not in MODULES["auto_role"].required_permissions()
    assert "send_messages" in MODULES["starboard"].required_permissions()
    assert "send_messages" in MODULES["tickets"].required_permissions()
    assert {"manage_channels", "manage_roles"} <= set(MODULES["tickets"].required_permissions())


def test_all_missing_guild_permissions_reported_together():
    cls = MODULES["interserver"]
    guild = _guild(held=["view_channel"])
    missing = missing_guild_permissions(guild, cls.required_permissions())
    expected = [k for k in cls.required_permissions() if k != "view_channel"]
    assert missing == expected
    assert "send_messages" in missing and "manage_webhooks" in missing


def test_granting_one_permission_never_reveals_a_hidden_one():
    """Everything missing is listed from the first check: fixing the listed
    permissions leaves nothing to discover on the next attempt."""
    for cls in MODULES.values():
        required = cls.required_permissions()
        first = missing_guild_permissions(_guild(), required)
        assert first == required
        assert missing_guild_permissions(_guild(held=first), required) == []


def test_channel_level_gaps_reported_together_and_deduplicated():
    guild = _guild(overwrites={10: ["view_channel"], 11: ["view_channel", "send_messages"]})
    reqs = {10: ["view_channel", "send_messages", "embed_links"],
            11: ["view_channel", "send_messages"], 99: ["send_messages"]}
    assert missing_channel_permissions(guild, reqs) == {10: ["send_messages", "embed_links"]}
    assert missing_channel_permissions(guild, reqs, skip=["send_messages"]) == {10: ["embed_links"]}


def test_channel_validation_error_lists_every_gap():
    guild = _guild(overwrites={10: []})
    channel = guild.get_channel_or_thread(10)
    msg = channel_validation_error(
        channel, guild, ["view_channel", "send_messages", "embed_links"], "en-US")
    assert msg and "<#10>" in msg
    assert msg.count("•") == 3


def test_channel_validation_ok_when_everything_held():
    guild = _guild(overwrites={10: ["view_channel", "send_messages"]})
    assert channel_validation_error(
        guild.get_channel_or_thread(10), guild, ["view_channel", "send_messages"], "fr") is None


def test_channel_map_merges_and_skips_empty():
    assert channel_map((5, ["a"]), (5, ["b", "a"]), (None, ["x"]), ("bad", ["y"])) == {5: ["a", "b"]}


def test_module_channel_requirements_read_config():
    assert "send_messages" in MODULES["interserver"].channel_requirements({"channel_id": 7})[7]
    assert 7 in MODULES["interserver"].channel_requirements({"channel_id": 7})
    assert MODULES["interserver"].channel_requirements({}) == {}
    assert set(MODULES["altguard"].channel_requirements(
        {"channel_id": 1, "log_channel_id": 2})) == {1, 2}
    assert 3 in MODULES["bump_reminder"].channel_requirements(
        {"reminders": [{"channel_id": 3}]})
    assert 4 in MODULES["welcome_channel"].channel_requirements(
        {"messages": [{"channel_id": 4}]})


def _interaction(guild):
    resp = SimpleNamespace(is_done=lambda: True)
    followup = SimpleNamespace(send=AsyncMock())
    return SimpleNamespace(
        guild=guild, client=SimpleNamespace(user=SimpleNamespace(id=42)),
        response=resp, followup=followup, locale="en-US", user=SimpleNamespace(id=1))


def test_check_bot_perms_single_message_with_every_gap_and_invite():
    from modules.configs._common import check_bot_perms

    cls = MODULES["altguard"]
    # Guild-wide Manage Roles is missing AND channel 10 denies sending.
    held = [k for k in cls.required_permissions() if k != "manage_roles"]
    guild = _guild(held=held, overwrites={10: ["view_channel"]})
    inter = _interaction(guild)
    ok = asyncio.run(check_bot_perms(
        inter, cls.required_permissions(), module_name="AltGuard",
        followup=True, channel_requirements={10: ["view_channel", "send_messages"]}))
    assert ok is False
    inter.followup.send.assert_awaited_once()
    view = inter.followup.send.await_args.kwargs["view"]
    text = json.dumps([getattr(c, "content", "") for c in view.walk_children()]
                      if hasattr(view, "walk_children") else str(view))
    assert "<#10>" in text
    link = next(c for c in view.walk_children() if getattr(c, "url", None))
    value = int(link.url.split("permissions=")[1].split("&")[0])
    invite = discord.Permissions(value)
    for key in ("manage_roles", "send_messages"):
        assert getattr(invite, key)
    assert invite.send_messages and invite.manage_roles and not invite.administrator


def test_check_bot_perms_passes_when_nothing_missing():
    from modules.configs._common import check_bot_perms

    cls = MODULES["auto_role"]
    guild = _guild(held=cls.required_permissions())
    inter = _interaction(guild)
    assert asyncio.run(check_bot_perms(inter, cls.required_permissions())) is True
    inter.followup.send.assert_not_called()


def test_format_missing_has_channel_sections():
    out = format_missing(["manage_roles"], {10: ["send_messages"]}, "en-US")
    assert out.count("•") == 2 and "<#10>" in out


@pytest.mark.parametrize("locale", LOCALES)
def test_locale_keys_and_permission_labels(locale):
    data = json.loads(Path(f"locales/{locale}.json").read_text(encoding="utf-8"))
    errors = data["modules"]["config"]["errors"]
    assert "{channel}" in errors["missing_bot_perms"]["in_channel"]
    assert "{permissions}" in errors["channel_missing_perms"]
    assert "{channel}" in errors["channel_missing_perms"]
    assert "{permissions}" in errors["guild_missing_perms"]
    labels = data["modules"]["logs"]["permissions"]
    used = set()
    for cls in MODULES.values():
        used.update(cls.required_permissions())
        for chan in ("view_channel", "send_messages", "embed_links", "manage_channels",
                     "manage_webhooks", "add_reactions", "manage_messages"):
            used.add(chan)
    for key in used:
        assert key in labels, (locale, key)
