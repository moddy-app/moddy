"""Config panels acknowledge the interaction before slow work (Discord's 3 s rule)."""

import asyncio
import pathlib
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from modules.configs import _common


def _interaction(done=False, manage_guild=True):
    response = MagicMock()
    response.is_done = MagicMock(return_value=done)
    response.defer = AsyncMock()
    response.edit_message = AsyncMock()
    response.send_message = AsyncMock()
    interaction = MagicMock()
    interaction.response = response
    interaction.followup.send = AsyncMock()
    interaction.edit_original_response = AsyncMock()
    interaction.guild_id = 1
    interaction.locale = "en-US"
    interaction.user = SimpleNamespace(
        id=2, guild_permissions=SimpleNamespace(manage_guild=manage_guild))
    return interaction


@pytest.mark.asyncio
async def test_ack_defers_once_and_is_noop_when_answered():
    fresh = _interaction()
    await _common.ack(fresh)
    fresh.response.defer.assert_awaited_once()

    answered = _interaction(done=True)
    await _common.ack(answered)
    answered.response.defer.assert_not_awaited()


@pytest.mark.asyncio
async def test_show_edits_through_the_right_path():
    view = MagicMock()
    fresh = _interaction()
    await _common.show(fresh, view)
    fresh.response.edit_message.assert_awaited_once_with(view=view)

    deferred = _interaction(done=True)
    await _common.show(deferred, view)
    deferred.edit_original_response.assert_awaited_once_with(view=view)
    deferred.response.edit_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_permission_refusal_works_after_a_defer():
    deferred = _interaction(done=True, manage_guild=False)
    assert await _common.check_guild_perms(deferred) is False
    deferred.followup.send.assert_awaited_once()
    deferred.response.send_message.assert_not_awaited()

    fresh = _interaction(manage_guild=False)
    assert await _common.check_guild_perms(fresh) is False
    fresh.response.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_load_before_modal_returns_the_value_or_asks_to_retry(monkeypatch):
    async def quick():
        return {"x": 1}

    ok, value = await _common.load_before_modal(_interaction(), quick())
    assert ok and value == {"x": 1}

    monkeypatch.setattr(_common, "MODAL_LOAD_TIMEOUT", 0.01)

    async def slow():
        await asyncio.sleep(1)

    stuck = _interaction()
    ok, value = await _common.load_before_modal(stuck, slow())
    assert not ok and value is None
    stuck.response.send_message.assert_awaited_once()  # first response, no modal


def test_panels_never_edit_through_response_after_slow_work():
    """Panels go through ack()/show(); a raw edit_message would skip the defer path."""
    root = pathlib.Path(__file__).resolve().parent.parent / "modules" / "configs"
    offenders = []
    for path in root.glob("*.py"):
        if path.name == "_common.py":
            continue
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(r"response\.(edit_message|defer)\(", line):
                offenders.append(f"{path.name}:{number}")
    assert not offenders, f"use ack()/show() from _common: {offenders}"
