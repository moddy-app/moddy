"""Interaction-latency guard rails: expired-interaction handling, loop monitor,
bounded sanction lookups and the cached incognito preference."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from cogs import error_handler
from utils import global_sanctions, incognito, loop_monitor


def _not_found(code: int) -> discord.NotFound:
    response = MagicMock(status=404, reason="Not Found")
    return discord.NotFound(response, {"code": code, "message": "x"})


def _interaction(age: float = 0.0):
    it = MagicMock()
    it.created_at = discord.utils.utcnow() - timedelta(seconds=age)
    it.type = discord.InteractionType.component
    it.data = {"custom_id": "moddy:test"}
    it.user = SimpleNamespace(id=1)
    it.guild_id = 2
    return it


class TestExpiredInteraction:
    @pytest.mark.parametrize("code,kind", [
        (10062, "unknown_interaction"), (10008, "unknown_message"), (10015, "unknown_webhook")])
    def test_kinds(self, code, kind):
        assert error_handler.expired_interaction_kind(_not_found(code)) == kind

    def test_other_not_found_is_a_real_error(self):
        assert error_handler.expired_interaction_kind(_not_found(10003)) is None
        assert error_handler.expired_interaction_kind(ValueError("x")) is None

    def test_unwraps_original_and_cause(self):
        wrapper = discord.app_commands.CommandInvokeError(MagicMock(), _not_found(10062))
        assert error_handler.expired_interaction_kind(wrapper) == "unknown_interaction"
        try:
            try:
                raise _not_found(10008)
            except discord.NotFound as e:
                raise RuntimeError("boom") from e
        except RuntimeError as outer:
            assert error_handler.expired_interaction_kind(outer) == "unknown_message"

    @pytest.mark.asyncio
    async def test_expired_errors_are_no_longer_swallowed(self):
        """10062/10008/10015 go through the full error path (see test_interaction_guard.py)."""
        view = error_handler.BaseView()
        it = _interaction()
        it.response.is_done.return_value = False
        it.response.send_message = AsyncMock()
        it.client.get_cog.return_value = None      # no tracker: still reported + user answered
        before = error_handler._EXPIRED_COUNTS.get("unknown_interaction", 0)
        await view.on_error(it, _not_found(10062), MagicMock(custom_id="c"))
        assert error_handler._EXPIRED_COUNTS["unknown_interaction"] == before + 1
        it.response.send_message.assert_awaited_once()


class TestLoopMonitor:
    def test_late_interaction_counted(self):
        before = dict(loop_monitor.stats())
        age = loop_monitor.observe_interaction_age(_interaction(age=2.5))
        assert age >= 2.5
        after = loop_monitor.stats()
        assert after["late_interactions"] == before["late_interactions"] + 1

    def test_fresh_interaction_not_late(self):
        before = loop_monitor.stats()["late_interactions"]
        loop_monitor.observe_interaction_age(_interaction(age=0.1))
        assert loop_monitor.stats()["late_interactions"] == before


class TestBoundedSanctionLookup:
    @pytest.mark.asyncio
    async def test_slow_db_fails_open_within_budget_and_fills_cache(self):
        gate = asyncio.Event()

        async def slow(subject_type, subject_id):
            await gate.wait()
            return []

        bot = SimpleNamespace(db=SimpleNamespace(list_active_global_actions=slow))
        loop = asyncio.get_running_loop()
        start = loop.time()
        level = await global_sanctions.get_level(bot, "user", 9001, budget=0.05)
        assert level == global_sanctions.GlobalLevel.NONE
        assert loop.time() - start < 0.5
        gate.set()
        await asyncio.sleep(0.05)  # background query finished
        assert ("user", "9001") in bot._global_sanction_cache

    @pytest.mark.asyncio
    async def test_concurrent_lookups_share_one_query(self):
        calls = 0

        async def fetch(subject_type, subject_id):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.02)
            return []

        bot = SimpleNamespace(db=SimpleNamespace(list_active_global_actions=fetch))
        await asyncio.gather(*(global_sanctions.get_level(bot, "user", 9002) for _ in range(5)))
        assert calls == 1

    @pytest.mark.asyncio
    async def test_db_error_fails_open(self):
        async def boom(subject_type, subject_id):
            raise RuntimeError("db down")

        bot = SimpleNamespace(db=SimpleNamespace(list_active_global_actions=boom))
        assert await global_sanctions.get_level(bot, "user", 9003, budget=0.5) \
            == global_sanctions.GlobalLevel.NONE


class TestIncognitoPreference:
    @pytest.mark.asyncio
    async def test_cached_and_default(self):
        incognito.invalidate_incognito_cache()
        get = AsyncMock(return_value=False)
        bot = SimpleNamespace(db=SimpleNamespace(get_attribute=get))
        assert await incognito.resolve_default_incognito(bot, 5) is False
        assert await incognito.resolve_default_incognito(bot, 5) is False
        assert get.await_count == 1

    @pytest.mark.asyncio
    async def test_timeout_falls_back_to_default(self, monkeypatch):
        incognito.invalidate_incognito_cache()
        monkeypatch.setattr(incognito, "_PREFERENCE_LOOKUP_TIMEOUT", 0.02)

        async def slow(*a):
            await asyncio.sleep(1)

        bot = SimpleNamespace(db=SimpleNamespace(get_attribute=slow))
        assert await incognito.resolve_default_incognito(bot, 6, default=True) is True
