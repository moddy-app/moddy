"""Interaction guard + error delivery chain.

* the guard defers each interaction type correctly and never double-responds;
* ``InteractionResponse`` shims redirect feature calls after an auto-defer;
* ``send_modal`` after an auto-defer raises ``InteractionTooSlow``;
* expired-interaction errors (10062 / 10008 / 10015) take the FULL error path;
* delivery chain order + DM fallback + per-user throttle;
* the discord.py upgrade guard (a moved attribute must fail here).
"""
from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import ui
from discord.webhook.async_ import async_context

from cogs import error_handler
from utils import interaction_guard as guard
from utils import loop_monitor

PING, COMMAND, COMPONENT, AUTOCOMPLETE, MODAL = 1, 2, 3, 4, 5


@pytest.fixture(autouse=True)
def _installed():
    guard.install()
    yield


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #

class FakeAdapter:
    """Records interaction callbacks instead of calling Discord."""

    def __init__(self, fail=None):
        self.calls = []          # (callback type, flags)
        self.fail = fail or {}   # callback type -> exception to raise

    async def create_interaction_response(self, interaction_id, token, *, session, proxy,
                                          proxy_auth, params):
        payload = params.payload
        rtype = payload["type"]
        flags = (payload.get("data") or {}).get("flags")
        self.calls.append((rtype, flags))
        if rtype in self.fail:
            raise self.fail[rtype]
        return {"interaction": {"id": str(interaction_id)}}


def _http_error(cls, status, code):
    return cls(MagicMock(status=status, reason="x"), {"code": code, "message": "x"})


@pytest.fixture
def adapter():
    fake = FakeAdapter()
    token = async_context.set(fake)
    yield fake
    async_context.reset(token)


def make_interaction(itype=COMPONENT, age=0.0, *, with_message=False, custom_id="moddy:test:btn",
                     dispatched=True):
    state = MagicMock()
    state._get_client.return_value = SimpleNamespace(user=None, get_cog=lambda name: None)
    state._command_tree = None
    state.allowed_mentions = None
    data = {
        "id": str(discord.utils.time_snowflake(discord.utils.utcnow() - timedelta(seconds=age))),
        "type": itype,
        "token": f"tok-{itype}-{age}-{id(state)}",
        "version": 1,
        "application_id": "1",
        "attachment_size_limit": 1,
        "user": {"id": "42", "username": "u", "discriminator": "0", "avatar": None,
                 "global_name": "U"},
        "data": ({"name": "ping"} if itype == COMMAND else {"custom_id": custom_id}),
    }
    interaction = discord.Interaction(data=data, state=state)
    if with_message:
        interaction.message = MagicMock()
    if dispatched:
        guard.mark_dispatched(interaction)
    return interaction


async def _fire(interaction):
    """Run the deadline callback now and let the guard task finish."""
    guard._on_deadline(interaction)
    state = guard.get_state(interaction)
    if state is not None:
        await asyncio.wait_for(state.ready.wait(), 2)
    return state


# --------------------------------------------------------------------------- #
# What the guard sends per interaction type
# --------------------------------------------------------------------------- #

class TestGuardAcknowledges:
    async def test_component_gets_invisible_defer(self, adapter):
        it = make_interaction(COMPONENT, with_message=True)
        state = await _fire(it)
        assert adapter.calls == [(discord.InteractionResponseType.deferred_message_update.value, None)]
        assert (state.status, state.mode) == ("deferred", "update")
        assert it.response.is_done()

    async def test_command_gets_ephemeral_thinking(self, adapter):
        it = make_interaction(COMMAND)
        state = await _fire(it)
        assert adapter.calls == [(discord.InteractionResponseType.deferred_channel_message.value, 64)]
        assert state.mode == "thinking"

    async def test_modal_from_component_message_is_invisible_defer(self, adapter):
        it = make_interaction(MODAL, with_message=True)
        state = await _fire(it)
        assert adapter.calls == [(discord.InteractionResponseType.deferred_message_update.value, None)]
        assert state.mode == "update"

    async def test_bare_modal_gets_ephemeral_thinking(self, adapter):
        it = make_interaction(MODAL, with_message=False)
        state = await _fire(it)
        assert adapter.calls == [(discord.InteractionResponseType.deferred_channel_message.value, 64)]
        assert state.mode == "thinking"

    async def test_autocomplete_and_ping_are_never_guarded(self, adapter):
        assert guard.schedule(make_interaction(AUTOCOMPLETE)) is None
        assert guard.schedule(make_interaction(PING)) is None

    async def test_timer_is_armed_at_construction_and_fires_on_late_interaction(self, adapter):
        it = make_interaction(COMMAND, age=2.5)     # already past the deadline
        await asyncio.sleep(0.1)
        assert guard.get_state(it) is not None
        assert len(adapter.calls) == 1

    async def test_young_interaction_waits_for_the_deadline(self, adapter):
        handle = guard.schedule(make_interaction(COMMAND, age=0.0))
        try:
            assert handle is not None
            assert 1.5 < handle.when() - asyncio.get_running_loop().time() <= guard.DEADLINE_SECONDS
        finally:
            handle.cancel()

    async def test_nothing_happens_when_the_feature_already_responded(self, adapter):
        it = make_interaction(COMPONENT)
        it.response._response_type = discord.InteractionResponseType.channel_message
        guard._on_deadline(it)
        await asyncio.sleep(0.05)
        assert adapter.calls == [] and guard.get_state(it) is None

    async def test_unclaimed_component_gets_a_visible_notice_not_silence(self, adapter):
        it = make_interaction(COMPONENT, with_message=True, dispatched=False)
        state = await _fire(it)
        assert state.status == "answered"
        rtype, flags = adapter.calls[0]
        assert rtype == discord.InteractionResponseType.channel_message.value and flags & 64

    async def test_guard_runs_once(self, adapter):
        it = make_interaction(COMPONENT)
        await _fire(it)
        guard._on_deadline(it)
        await asyncio.sleep(0.02)
        assert len(adapter.calls) == 1


class TestGuardRaces:
    async def test_guard_loses_race_to_the_feature(self, adapter):
        adapter.fail[discord.InteractionResponseType.deferred_message_update.value] = \
            _http_error(discord.HTTPException, 400, 40060)
        it = make_interaction(COMPONENT)
        state = await _fire(it)
        assert state.status == "lost"
        assert not state.took_over

    async def test_dead_interaction_goes_to_full_error_path(self, adapter, monkeypatch):
        adapter.fail[discord.InteractionResponseType.deferred_message_update.value] = \
            _http_error(discord.NotFound, 404, 10062)
        reported = AsyncMock()
        monkeypatch.setattr(error_handler, "report_interaction_error", reported)
        it = make_interaction(COMPONENT)
        state = await _fire(it)
        assert state.status == "dead"
        reported.assert_awaited_once()
        assert isinstance(reported.await_args.args[1], discord.NotFound)
        assert "InteractionGuard" in reported.await_args.kwargs["command"]


# --------------------------------------------------------------------------- #
# Shims: feature code keeps working after the guard deferred
# --------------------------------------------------------------------------- #

class _Message:
    def __init__(self):
        self.deleted = False

    async def delete(self):
        self.deleted = True


class _Followup:
    def __init__(self):
        self.sent = []
        self.message = _Message()

    async def send(self, *args, **kwargs):
        self.sent.append((args, kwargs))
        return self.message


def _edit_recorder(monkeypatch):
    calls = []

    async def fake_edit(self, *, content=discord.utils.MISSING, embeds=discord.utils.MISSING,
                        embed=discord.utils.MISSING, attachments=discord.utils.MISSING,
                        view=discord.utils.MISSING, allowed_mentions=None, poll=discord.utils.MISSING):
        calls.append({"content": content, "view": view, "allowed_mentions": allowed_mentions})
        return "edited"

    monkeypatch.setattr(discord.Interaction, "edit_original_response", fake_edit)
    monkeypatch.setattr(guard, "_EDIT_PARAMS", None)
    return calls


class TestShimsAfterAutoDefer:
    async def _deferred(self, adapter, itype=COMPONENT):
        it = make_interaction(itype, with_message=itype != COMMAND)
        it._cs_followup = _Followup()
        await _fire(it)
        adapter.calls.clear()
        return it

    async def test_send_message_becomes_followup_with_same_kwargs(self, adapter):
        it = await self._deferred(adapter)
        view = ui.LayoutView()
        result = await it.response.send_message(view=view, ephemeral=True, silent=True)
        (args, kwargs), = it.followup.sent
        assert kwargs == {"view": view, "ephemeral": True, "silent": True, "wait": True}
        assert result is it.followup.message
        assert adapter.calls == []          # no second callback to Discord

    async def test_send_message_delete_after_is_honoured(self, adapter):
        it = await self._deferred(adapter)
        await it.response.send_message("bye", delete_after=0.01)
        assert "delete_after" not in it.followup.sent[0][1]   # Webhook.send does not know it
        await asyncio.sleep(0.1)
        assert it.followup.message.deleted

    async def test_send_message_content_positional_is_kept(self, adapter):
        it = await self._deferred(adapter, COMMAND)
        await it.response.send_message("hello", ephemeral=False, allowed_mentions=None)
        (args, kwargs), = it.followup.sent
        assert args == ("hello",) and kwargs["ephemeral"] is False

    async def test_edit_message_becomes_edit_original_response(self, adapter, monkeypatch):
        calls = _edit_recorder(monkeypatch)
        it = await self._deferred(adapter)
        view = ui.LayoutView()
        await it.response.edit_message(content=None, view=view, suppress_embeds=True)
        assert calls == [{"content": None, "view": view, "allowed_mentions": None}]
        assert adapter.calls == []

    async def test_edit_message_delete_after_is_honoured(self, adapter, monkeypatch):
        _edit_recorder(monkeypatch)
        deleted = []

        async def fake_delete(self):
            deleted.append(True)

        monkeypatch.setattr(discord.Interaction, "delete_original_response", fake_delete)
        it = await self._deferred(adapter)
        await it.response.edit_message(content="x", delete_after=0.01)
        await asyncio.sleep(0.1)
        assert deleted == [True]

    async def test_defer_is_a_noop(self, adapter):
        it = await self._deferred(adapter)
        assert await it.response.defer(ephemeral=True, thinking=True) is None
        assert await it.response.defer() is None
        assert adapter.calls == []

    async def test_ephemeral_thinking_request_keeps_the_first_followup_private(self, adapter):
        it = await self._deferred(adapter)          # invisible component defer
        await it.response.defer(ephemeral=True, thinking=True)
        state = guard.get_state(it)
        assert state.force_ephemeral
        sent = []

        async def original(self, *a, **kw):
            sent.append(kw)

        wrapped = guard._wrap_webhook_send(original)
        hook = SimpleNamespace(token=it.token)
        await wrapped(hook, "text")
        await wrapped(hook, "again")
        assert sent[0]["ephemeral"] is True and "ephemeral" not in sent[1]

    async def test_send_modal_after_auto_defer_raises_clear_error(self, adapter):
        it = await self._deferred(adapter)
        with pytest.raises(guard.InteractionTooSlow) as info:
            await it.response.send_modal(MagicMock())
        assert "modal" in str(info.value).lower()
        assert adapter.calls == []

    async def test_is_done_reflects_the_guard_defer(self, adapter):
        it = await self._deferred(adapter)
        assert it.response.is_done()

    async def test_feature_calls_are_untouched_without_the_guard(self, adapter):
        it = make_interaction(COMPONENT)
        it._cs_followup = _Followup()
        await it.response.send_message("direct")
        assert adapter.calls == [(discord.InteractionResponseType.channel_message.value, None)]
        assert it.followup.sent == []

    async def test_feature_losing_the_race_is_redirected(self, adapter):
        """Feature's send_message gets 40060 because the guard answered first."""
        it = make_interaction(COMPONENT)
        it._cs_followup = _Followup()
        state = guard.GuardState(status="lost")     # unknown when the call starts
        state.ready.set()
        it.extras[guard.GUARD_KEY] = state
        rtype = discord.InteractionResponseType.channel_message.value
        adapter.fail[rtype] = _http_error(discord.HTTPException, 400, 40060)
        original_call = adapter.create_interaction_response

        async def guard_wins_meanwhile(*a, **kw):
            state.status = "deferred"               # the guard's defer lands first
            return await original_call(*a, **kw)

        adapter.create_interaction_response = guard_wins_meanwhile
        result = await it.response.send_message("late")
        assert result is it.followup.message
        assert it.followup.sent[0][0] == ("late",)

    async def test_modal_when_guard_lost_the_race_is_not_blocked(self, adapter):
        it = make_interaction(COMPONENT)
        state = guard.GuardState(status="lost")
        state.ready.set()
        it.extras[guard.GUARD_KEY] = state
        modal = MagicMock()
        modal.to_dict.return_value = {"title": "t", "custom_id": "m", "components": []}
        modal.is_finished.return_value = True
        await it.response.send_modal(modal)
        assert adapter.calls[0][0] == discord.InteractionResponseType.modal.value


# --------------------------------------------------------------------------- #
# Error handler: expired interactions take the FULL path again
# --------------------------------------------------------------------------- #

def _not_found(code):
    return _http_error(discord.NotFound, 404, code)


def _tracker_bot(dm_delivered=True):
    bot = MagicMock()
    bot.db = None
    tracker = error_handler.ErrorTracker(bot)
    tracker.store_error_db = AsyncMock()
    tracker.send_error_log = AsyncMock()
    bot.get_cog.return_value = tracker
    bot.notifications.send_dm = AsyncMock(
        return_value=SimpleNamespace(delivered=dm_delivered, error=None))
    return bot, tracker


def _mock_interaction(bot, *, age=0.5, send_error=None, followup_error=None, edit_error=None):
    it = MagicMock()
    it.id = 777
    it.client = bot
    it.extras = {}
    it.created_at = discord.utils.utcnow() - timedelta(seconds=age)
    it.type = discord.InteractionType.component
    it.locale = "fr"
    it.user = SimpleNamespace(id=1234, __str__=lambda s: "u")
    it.guild = None
    it.guild_id = None
    it.channel = None
    it.response.is_done.return_value = False
    it.response.send_message = AsyncMock(side_effect=send_error)
    it.followup.send = AsyncMock(side_effect=followup_error)
    it.edit_original_response = AsyncMock(side_effect=edit_error)
    return it


@pytest.fixture(autouse=True)
def _reset_dm_throttle():
    error_handler._DM_LAST.clear()
    yield
    error_handler._DM_LAST.clear()


class TestFullPathRestored:
    @pytest.mark.parametrize("code", [10062, 10008, 10015])
    async def test_view_on_error_reports_everything(self, code):
        bot, tracker = _tracker_bot()
        it = _mock_interaction(bot)
        view = error_handler.BaseView()
        await view.on_error(it, _not_found(code), MagicMock(custom_id="c"))
        tracker.store_error_db.assert_awaited_once()
        tracker.send_error_log.assert_awaited_once()
        code_, details = tracker.send_error_log.await_args.args[:2]
        assert len(code_) == 8
        assert details["user_reached"] == "response"
        assert details["interaction_context"]["expired_kind"] in {
            "unknown_interaction", "unknown_message", "unknown_webhook"}
        assert "interaction_age" in details["interaction_context"]
        it.response.send_message.assert_awaited_once()

    async def test_modal_on_error_reports_everything(self):
        bot, tracker = _tracker_bot()
        it = _mock_interaction(bot)
        modal = error_handler.BaseModal(title="t")
        await modal.on_error(it, _not_found(10062))
        tracker.store_error_db.assert_awaited_once()
        tracker.send_error_log.assert_awaited_once()

    async def test_app_command_error_reports_everything(self):
        bot, tracker = _tracker_bot()
        it = _mock_interaction(bot)
        it.command = SimpleNamespace(name="ping", qualified_name="ping")
        err = discord.app_commands.CommandInvokeError(it.command, _not_found(10062))
        await tracker.on_app_command_error(it, err)
        tracker.store_error_db.assert_awaited_once()
        tracker.send_error_log.assert_awaited_once()

    async def test_component_error_reports_everything(self):
        bot, tracker = _tracker_bot()
        it = _mock_interaction(bot)
        await error_handler.report_component_error(it, _not_found(10008), "Thing")
        tracker.send_error_log.assert_awaited_once()

    async def test_token_detector_routes_expired_to_full_path(self):
        from cogs import token_detector
        bot, tracker = _tracker_bot()
        it = _mock_interaction(bot)
        await token_detector._route_error(it, _not_found(10062), "Ctx")
        tracker.store_error_db.assert_awaited_once()

    async def test_same_expired_interaction_is_reported_once(self):
        bot, tracker = _tracker_bot()
        it = _mock_interaction(bot)
        assert await error_handler.report_interaction_error(
            it, _not_found(10062), command="a", error_type="t")
        assert await error_handler.report_interaction_error(
            it, _not_found(10062), command="b", error_type="t") is None
        assert tracker.send_error_log.await_count == 1

    async def test_unknown_command_tells_the_user(self):
        bot, tracker = _tracker_bot()
        it = _mock_interaction(bot)
        it.data = {"name": "gone"}
        await tracker.on_app_command_error(it, discord.app_commands.CommandNotFound("gone", []))
        it.response.send_message.assert_awaited_once()


# --------------------------------------------------------------------------- #
# Delivery chain + DM fallback
# --------------------------------------------------------------------------- #

class TestDeliveryChain:
    async def test_order_response_then_followup_then_edit(self):
        bot, _ = _tracker_bot()
        boom = discord.HTTPException(MagicMock(status=500, reason="x"), "x")
        it = _mock_interaction(bot, send_error=boom, followup_error=boom)
        reached = await error_handler.deliver_view(it, ui.LayoutView())
        assert reached == "edit_original"
        it.response.send_message.assert_awaited_once()
        it.followup.send.assert_awaited_once()

    async def test_already_answered_skips_the_initial_response(self):
        bot, _ = _tracker_bot()
        it = _mock_interaction(bot)
        it.response.is_done.return_value = True
        assert await error_handler.deliver_view(it, ui.LayoutView()) == "followup"
        it.response.send_message.assert_not_awaited()

    async def test_dead_token_goes_straight_to_the_dm(self):
        bot, _ = _tracker_bot()
        it = _mock_interaction(bot, send_error=_not_found(10062))
        _ = await error_handler.report_interaction_error(
            it, ValueError("boom"), command="c", error_type="t")
        it.followup.send.assert_not_awaited()          # token known dead after 10062
        it.edit_original_response.assert_not_awaited()
        bot.notifications.send_dm.assert_awaited_once()

    async def test_old_token_skips_http_and_dms(self):
        bot, tracker = _tracker_bot()
        it = _mock_interaction(bot, age=16 * 60)
        await error_handler.report_interaction_error(
            it, ValueError("boom"), command="c", error_type="t")
        it.response.send_message.assert_not_awaited()
        bot.notifications.send_dm.assert_awaited_once()
        assert tracker.send_error_log.await_args.args[1]["user_reached"] == "dm"

    async def test_dm_uses_notification_system_with_localized_card_and_code(self):
        bot, tracker = _tracker_bot()
        it = _mock_interaction(bot, age=16 * 60)
        code = await error_handler.report_interaction_error(
            it, ValueError("boom"), command="c", error_type="t")
        kwargs = bot.notifications.send_dm.await_args.kwargs
        assert kwargs["variables"] == {"error_code": code}
        assert kwargs["source"].is_official
        assert kwargs["locale"] == "fr"
        assert isinstance(kwargs["view"], error_handler.ErrorView) and kwargs["view"].dm
        assert "{error_code}" in kwargs["content"].body       # placeholder stays in the template

    async def test_dm_is_throttled_per_user(self):
        bot, tracker = _tracker_bot()
        outcomes = []
        for _ in range(3):
            it = _mock_interaction(bot, age=16 * 60)
            await error_handler.report_interaction_error(
                it, ValueError("boom"), command="c", error_type="t")
            outcomes.append(tracker.send_error_log.await_args.args[1]["user_reached"])
        assert outcomes == ["dm", "dm_throttled", "dm_throttled"]
        assert bot.notifications.send_dm.await_count == 1

    async def test_throttle_is_per_user(self):
        bot, _ = _tracker_bot()
        assert await error_handler.dm_error_fallback(bot, SimpleNamespace(id=1), "A", "en-US") == "dm"
        assert await error_handler.dm_error_fallback(bot, SimpleNamespace(id=2), "B", "en-US") == "dm"
        assert await error_handler.dm_error_fallback(bot, SimpleNamespace(id=1), "C", "en-US") == "dm_throttled"

    async def test_dm_failure_is_recorded_not_raised(self):
        bot, tracker = _tracker_bot(dm_delivered=False)
        it = _mock_interaction(bot, age=16 * 60)
        await error_handler.report_interaction_error(
            it, ValueError("boom"), command="c", error_type="t")
        assert tracker.send_error_log.await_args.args[1]["user_reached"] == "dm_failed"

    async def test_no_notification_service_is_reported_as_unavailable(self):
        bot = SimpleNamespace(notifications=None)
        assert await error_handler.dm_error_fallback(bot, SimpleNamespace(id=9), "A", "en-US") \
            == "dm_unavailable"

    async def test_too_slow_error_uses_the_too_slow_card(self):
        bot, tracker = _tracker_bot()
        it = _mock_interaction(bot)
        await error_handler.report_interaction_error(
            it, guard.InteractionTooSlow(it), command="c", error_type="t")
        view = it.response.send_message.await_args.kwargs["view"]
        assert view.too_slow
        tracker.store_error_db.assert_awaited_once()      # still reported to the owner


class TestErrorViewLocalization:
    @pytest.mark.parametrize("locale", ["fr", "en-US", "es-ES", "pt-BR", "de"])
    def test_every_variant_is_translated(self, locale):
        for kwargs in ({}, {"too_slow": True}, {"dm": True}, {"too_slow": True, "dm": True}):
            view = error_handler.ErrorView("ABCD1234", locale, **kwargs)
            texts = [c.content for c in view.walk_children() if isinstance(c, ui.TextDisplay)]
            assert texts and all("[errors." not in t for t in texts), (locale, kwargs, texts)
            assert any("ABCD1234" in t for t in texts)

    @pytest.mark.parametrize("prefix", ["errors.inactive", "errors.still_working",
                                        "errors.command_unavailable"])
    @pytest.mark.parametrize("locale", ["fr", "en-US", "es-ES", "pt-BR", "de"])
    def test_notice_cards(self, prefix, locale):
        view = error_handler.InfoNoticeView(prefix, locale)
        texts = [c.content for c in view.walk_children() if isinstance(c, ui.TextDisplay)]
        assert all("[errors." not in t for t in texts)

    def test_new_keys_exist_in_all_locales(self):
        root = Path(__file__).resolve().parent.parent / "locales"
        keys = None
        for name in ("fr", "en-US", "es-ES", "pt-BR", "de"):
            errors = json.loads((root / f"{name}.json").read_text(encoding="utf-8"))["errors"]
            flat = {f"{k}.{k2}" for k in ("card", "too_slow", "dm_fallback", "inactive",
                                          "still_working", "command_unavailable")
                    for k2 in errors[k]}
            keys = flat if keys is None else keys
            assert flat == keys, name


# --------------------------------------------------------------------------- #
# Ops notices + loop monitor
# --------------------------------------------------------------------------- #

class TestOpsNotices:
    async def test_notice_is_throttled_per_key_and_counts_repeats(self):
        bot, tracker = _tracker_bot()
        assert await tracker.send_ops_notice(key="k", kind="X", title="t", message="m", interval=60)
        assert not await tracker.send_ops_notice(key="k", kind="X", title="t", message="m", interval=60)
        assert tracker.send_error_log.await_count == 1
        tracker._ops_last["k"] -= 120
        assert await tracker.send_ops_notice(key="k", kind="X", title="t", message="m", interval=60)
        assert "+1 similar" in tracker.send_error_log.await_args.args[1]["message"]

    async def test_global_cap_per_minute(self):
        bot, tracker = _tracker_bot()
        sent = [await tracker.send_ops_notice(key=f"k{i}", kind="X", title="t", message="m")
                for i in range(10)]
        assert sum(sent) == 6

    async def test_guard_activation_is_announced_to_the_owner(self, adapter):
        bot, tracker = _tracker_bot()
        it = make_interaction(COMMAND)
        it._client = bot
        await _fire(it)
        await asyncio.sleep(0.05)
        tracker.send_error_log.assert_awaited_once()
        assert tracker.send_error_log.await_args.args[1]["type"] == "InteractionGuard"


class TestLoopMonitorNotice:
    async def test_long_stall_notifies_with_stack_and_is_throttled(self, monkeypatch):
        got = []

        async def notifier(**notice):
            got.append(notice)

        monkeypatch.setattr(loop_monitor, "_last_notified", -1e9)
        loop_monitor.set_notifier(notifier)
        try:
            import time
            loop_monitor._state["stall_stack"] = "File x.py, line 1\n  blocking()"
            loop_monitor._state["stall_stack_at"] = time.monotonic()
            await loop_monitor._notify_stall(3.2)
            await loop_monitor._notify_stall(3.2)      # throttled
        finally:
            loop_monitor.set_notifier(None)
        assert len(got) == 1
        assert "blocking()" in got[0]["stack"]
        assert got[0]["key"] == "loop-stall" and "3.2" in got[0]["message"]

    async def test_no_notifier_is_a_noop(self):
        loop_monitor.set_notifier(None)
        await loop_monitor._notify_stall(5.0)


# --------------------------------------------------------------------------- #
# discord.py upgrade guard
# --------------------------------------------------------------------------- #

def test_every_patched_attribute_exists_and_is_patched():
    import importlib
    for mod_name, cls_name, attr, _ in guard.PATCH_TARGETS:
        cls = getattr(importlib.import_module(mod_name), cls_name)
        assert getattr(cls.__dict__[attr], guard.PATCH_MARK, False), f"{cls_name}.{attr}"


def test_install_is_idempotent():
    before = discord.InteractionResponse.send_message
    guard.install()
    guard.install()
    assert discord.InteractionResponse.send_message is before


def test_discord_internals_the_guard_relies_on():
    assert "extras" in discord.Interaction.__slots__
    assert "_parent" in discord.InteractionResponse.__slots__
    assert "_response_type" in discord.InteractionResponse.__slots__
    import inspect
    params = inspect.signature(discord.Interaction.edit_original_response).parameters
    for name in ("content", "view", "attachments", "allowed_mentions", "embed", "embeds"):
        assert name in params
    assert inspect.iscoroutinefunction(discord.InteractionResponse.send_modal)
    assert hasattr(discord.MessageFlags, "loading")
    assert discord.InteractionResponded.__mro__[1] is discord.ClientException
    # follow-up must accept everything send_message does (kwargs are forwarded as-is)
    send_params = set(inspect.signature(discord.Webhook.send).parameters)
    for name in ("content", "view", "ephemeral", "file", "files", "allowed_mentions",
                 "suppress_embeds", "silent", "wait"):
        assert name in send_params


def test_guard_coexists_with_the_icon_theme_patches():
    from utils import icon_theme
    icon_theme.install()
    for cls_name, method in (("InteractionResponse", "send_message"),
                             ("InteractionResponse", "edit_message")):
        fn = getattr(discord.InteractionResponse, method)
        assert getattr(fn, "__icon_theme_patched__", False) or getattr(fn, guard.PATCH_MARK, False)


def test_modal_budget_leaves_room_before_the_guard_deadline():
    import datetime
    from types import SimpleNamespace
    from utils import interaction_guard as g

    def at(age):
        created = discord.utils.utcnow() - datetime.timedelta(seconds=age)
        return SimpleNamespace(created_at=created)

    fresh = g.modal_budget(at(0.0), 5.0)
    assert fresh <= g.DEADLINE_SECONDS - g.MODAL_MARGIN_SECONDS + 0.05
    assert g.modal_budget(at(0.0), 0.5) == 0.5          # capped
    assert g.modal_budget(at(2.5), 1.5) == 0.0          # already past the deadline
    assert g.modal_budget(object(), 1.5) == 1.5         # stub without created_at
