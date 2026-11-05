"""/config "Send a test" button + the quoted list entries.

Covers modules/configs/_test_send.py, the four module senders and the
``quote()`` helper used by every list panel.
"""

import asyncio
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from modules.configs import _test_send
from modules.configs._common import quote
from modules.configs._test_send import (
    TestResult, TestSendButton, TestSendPersistence, confirmation_view,
    test_button_row as button_row,
)

LOCALES = ["fr", "en-US", "es-ES", "pt-BR", "de"]
ROOT = Path(__file__).resolve().parent.parent


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _buttons(view):
    found = []

    def walk(item):
        if isinstance(item, discord.ui.DynamicItem):
            found.append(item)
        for child in getattr(item, "children", []) or []:
            walk(child)
    for child in view.children:
        walk(child)
    return found


# --------------------------------------------------------------------------- #
# The button
# --------------------------------------------------------------------------- #
def test_custom_id_round_trips_through_the_template():
    button = TestSendButton("welcome_channel", "wm_1a2b3c4d")
    cid = button.item.custom_id
    assert cid == "moddy:cfg:test:welcome_channel:wm_1a2b3c4d"
    assert len(cid) <= 100
    match = TestSendButton.__discord_ui_compiled_template__.fullmatch(cid)
    assert match and match["module"] == "welcome_channel" and match["ref"] == "wm_1a2b3c4d"


def test_social_row_id_fits_the_template():
    cid = TestSendButton("social_notifications", "123456789012").item.custom_id
    assert TestSendButton.__discord_ui_compiled_template__.fullmatch(cid)


def test_every_sender_exists():
    for module_id, target in _test_send._SENDERS.items():
        path, _, name = target.partition(":")
        assert asyncio.iscoroutinefunction(getattr(importlib.import_module(path), name)), module_id


def test_confirmation_carries_the_button_and_hint():
    view = confirmation_view("welcome_dm", "wdm_1", "en-US", "Welcome DM added.")
    items = _buttons(view)
    assert len(items) == 1 and items[0].ref == "wdm_1"


def test_confirmation_never_doubles_the_icon():
    from utils.emojis import DONE
    view = confirmation_view("social_notifications", "1", "en-US", f"{DONE} Now following")
    text = view.children[0].children[0].content
    assert text.count(DONE) == 1


def test_no_button_without_an_entry_or_for_an_unknown_module():
    assert button_row("welcome_dm", None, "en-US") is None
    assert button_row("starboard", "x", "en-US") is None
    assert not _buttons(confirmation_view("welcome_dm", None, "en-US", "ok"))


def test_registered_as_persistent():
    from utils.persistent_views import _collect_persistent_view_classes
    from minibots.builtin import COMMON_PERSISTENT_VIEWS
    assert TestSendPersistence in _collect_persistent_view_classes()
    assert "modules.configs._test_send:TestSendPersistence" in COMMON_PERSISTENT_VIEWS
    bot = MagicMock()
    TestSendPersistence.register_persistent(bot)
    bot.add_dynamic_items.assert_called_once_with(TestSendButton)


def test_cooldown_blocks_a_second_click():
    _test_send._last_test.clear()
    button = TestSendButton("welcome_dm", "wdm_x")
    button._send = AsyncMock(return_value=TestResult("sent_dm"))

    def interaction():
        perms = SimpleNamespace(manage_guild=True)
        response = MagicMock()
        response.is_done = MagicMock(return_value=False)
        response.defer = AsyncMock()
        response.send_message = AsyncMock()
        return SimpleNamespace(
            guild_id=1, user=SimpleNamespace(id=5, guild_permissions=perms),
            locale=discord.Locale.american_english, response=response,
            followup=SimpleNamespace(send=AsyncMock()), client=MagicMock(),
        )

    first, second = interaction(), interaction()
    _run(button.callback(first))
    _run(button.callback(second))
    assert button._send.await_count == 1
    first.followup.send.assert_awaited_once()
    second.response.send_message.assert_awaited_once()


# --------------------------------------------------------------------------- #
# Senders
# --------------------------------------------------------------------------- #
def _guild_with_channel():
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 42
    channel.send = AsyncMock()
    guild = MagicMock()
    guild.id = 1
    guild.name = "Guild"
    guild.member_count = 10
    guild.me = None
    guild.get_channel = MagicMock(return_value=channel)
    return guild, channel


def _member():
    return SimpleNamespace(id=5, mention="<@5>", display_name="Tester", name="tester",
                           joined_at=None)


def test_welcome_channel_sends_the_entry_with_a_marker():
    from modules.welcome_channel import send_test
    guild, channel = _guild_with_channel()
    bot = MagicMock()
    bot.module_manager.get_module_config = AsyncMock(return_value={"messages": [
        {"id": "wm_1", "channel_id": 42, "message": "Hi {user}", "enabled": True}]})
    result = _run(send_test(bot, guild, _member(), "wm_1", "en-US"))
    assert result == TestResult("sent_channel", 42)
    view = channel.send.await_args.kwargs["view"]
    assert "Hi <@5>" in view.children[0].children[0].content
    assert "<@5>" in view.children[-1].content  # the "this is a test" line


def test_welcome_channel_unknown_entry():
    from modules.welcome_channel import send_test
    guild, channel = _guild_with_channel()
    bot = MagicMock()
    bot.module_manager.get_module_config = AsyncMock(return_value={"messages": []})
    assert _run(send_test(bot, guild, _member(), "wm_x", "en-US")).status == "not_found"
    channel.send.assert_not_awaited()


def test_welcome_dm_goes_through_notifications_and_reports_closed_dms():
    from modules.welcome_dm import send_test
    guild, _ = _guild_with_channel()
    bot = MagicMock()
    bot.module_manager.get_module_config = AsyncMock(return_value={"messages": [
        {"id": "wdm_1", "message": "Hello", "enabled": True}]})
    bot.notifications.send_dm = AsyncMock(return_value=SimpleNamespace(
        forbidden=True, delivered=False))
    assert _run(send_test(bot, guild, _member(), "wdm_1", "en-US")).status == "dm_closed"
    bot.notifications.send_dm.assert_awaited_once()


def test_social_test_never_pings_roles():
    from modules.social_notifications import send_test
    guild, channel = _guild_with_channel()
    guild.get_channel_or_thread = MagicMock(return_value=channel)
    bot = MagicMock()
    bot.db.list_social_subscriptions = AsyncMock(return_value=[{
        "id": 7, "platform": "twitch", "target_id": "123", "identifier": "streamer",
        "display_name": "Streamer", "channel_id": 42, "mention_role_ids": [99],
        "message": "{author}: {title} {url}", "show_avatar": False, "show_media": False,
    }])
    result = _run(send_test(bot, guild, _member(), "7", "en-US"))
    assert result.status == "sent_channel"
    allowed = channel.send.await_args.kwargs["allowed_mentions"]
    assert allowed.roles is False and allowed.everyone is False


# --------------------------------------------------------------------------- #
# Lists
# --------------------------------------------------------------------------- #
def test_quote_marks_every_line():
    assert quote("**A** → #x\n-# Every 2h") == "> **A** → #x\n> -# Every 2h"


@pytest.mark.parametrize("locale", LOCALES)
def test_i18n_keys_present(locale):
    data = json.loads((ROOT / "locales" / f"{locale}.json").read_text())
    test = data["modules"]["config"]["test"]
    for key in ("button", "hint", "marker", "cooldown"):
        assert test[key]
    for key in ("sent_channel", "sent_dm", "not_found", "channel_missing",
                "no_permission", "dm_closed", "failed"):
        assert test["result"][key]
    assert data["modules"]["social_notifications"]["test"]["sample_title"]
