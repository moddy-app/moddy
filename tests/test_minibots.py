"""Mini-bots core: ownership, the shared module manager, the runtime, the
transfer to Moddy, the Redis contract, staff/i18n wiring and the RAM budget.

Pure Python: no gateway, no database, no Redis (fakes below).
See docs/features/MINIBOTS.md.
"""

import asyncio
import json
import pathlib
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from minibots import ownership, spec as spec_mod, status as mstatus
from minibots.ownership import APP_KEY, MODDY_APP, can_claim, config_app, stamp
from minibots.spec import MiniBotSpec
from modules.module_manager import (
    EXTERNAL_DELETED, EXTERNAL_UPDATED, ModuleBase, ModuleManager, STATUS_EXTERNAL,
    STATUS_NOT_CONFIGURED,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOCALES = ("fr", "en-US", "es-ES", "pt-BR", "de")
GUILD = 111111111111111111


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #

class PanelModule(ModuleBase):
    """A module with a Discord side (a panel), like tickets / AltGuard."""
    MODULE_ID = "panel"
    MODULE_NAME = "Panel"
    events: list = []

    async def load_config(self, config_data):
        self.config = config_data
        self.enabled = bool(config_data.get("channel"))
        return True

    async def validate_config(self, config_data):
        assert APP_KEY not in config_data  # ownership is never validated
        return True, None

    def get_default_config(self):
        return {}

    async def on_external_config_change(self, action):
        PanelModule.events.append((self.bot.app_tag, action))
        return {"panel": "deleted" if action == EXTERNAL_DELETED else "posted"}


class DefaultModule(PanelModule):
    MODULE_ID = "defaulted"
    DEFAULT_ENABLED = True

    def get_default_config(self):
        return {"channel": 1}


class FakeDB:
    def __init__(self, modules=None, apps=()):
        self.modules = modules if modules is not None else {}
        self.apps = set(apps)

    async def get_guild(self, gid):
        return {"data": {"modules": self.modules}, "attributes": {}}

    async def update_guild_data(self, gid, path, value):
        self.modules[path.split(".", 1)[1]] = value

    async def apps_in_guild(self, gid):
        return set(self.apps)

    async def set_module_app(self, gid, module_id, expected, new):
        config = self.modules.get(module_id)
        if not isinstance(config, dict) or config_app(config) != expected:
            return False
        self.modules[module_id] = stamp(config, new)
        return True


def make_manager(db, app=MODDY_APP):
    bot = SimpleNamespace(db=db, module_slash_commands={}, app_tag=app,
                          resync_module_commands=AsyncMock())
    manager = ModuleManager(bot, app=app)
    manager.register_module(PanelModule)
    manager.register_module(DefaultModule)
    bot.module_manager = manager
    PanelModule.events = []
    return manager, bot


# --------------------------------------------------------------------------- #
# ownership
# --------------------------------------------------------------------------- #

class TestOwnership:
    def test_absent_key_is_moddy(self):
        assert config_app({"channel": 1}) == MODDY_APP
        assert config_app(None) == MODDY_APP
        assert config_app({APP_KEY: ""}) == MODDY_APP

    def test_stamp_keeps_moddy_shape_unchanged(self):
        assert stamp({"a": 1, APP_KEY: "bump"}, MODDY_APP) == {"a": 1}
        assert stamp({"a": 1}, "bump") == {"a": 1, APP_KEY: "bump"}

    def test_empty_config_belongs_to_nobody(self):
        assert not ownership.is_foreign({}, MODDY_APP)
        assert ownership.is_foreign({APP_KEY: "bump", "x": 1}, MODDY_APP)

    @pytest.mark.parametrize("config, present, default, expected", [
        ({APP_KEY: "bump", "x": 1}, {"bump"}, False, True),     # its own
        ({}, {"moddy"}, False, True),                           # empty
        ({}, {"moddy"}, True, False),                           # Moddy runs defaults
        ({}, set(), True, True),                                # Moddy absent
        ({"x": 1}, {"moddy"}, False, False),                    # Moddy's, Moddy here
        ({"x": 1}, set(), False, True),                         # Moddy's, Moddy gone
    ])
    def test_can_claim(self, config, present, default, expected):
        assert can_claim(config, "bump", present, moddy_default_enabled=default) is expected


# --------------------------------------------------------------------------- #
# spec registry
# --------------------------------------------------------------------------- #

class TestSpec:
    def test_rejects_moddy_id_and_bad_intents(self):
        with pytest.raises(ValueError):
            MiniBotSpec(id="moddy", name="x", token_env="X", modules=("a",))
        with pytest.raises(ValueError):
            MiniBotSpec(id="bump", name="x", token_env="X", modules=("a",),
                        intents=frozenset({"members"}))
        with pytest.raises(ValueError):
            MiniBotSpec(id="bump", name="x", token_env="X", modules=("a",),
                        web_surfaces=frozenset({"dashboard"}))

    def test_only_functional_web_surfaces(self):
        assert spec_mod.WEB_SURFACES == {"altguard_verification", "ticket_transcripts"}

    def test_display_name(self):
        spec_mod._register_for_tests(
            MiniBotSpec(id="bump", name="Bump Reminder", token_env="X", modules=("bump_reminder",)))
        try:
            assert spec_mod.app_display_name("bump") == "Bump Reminder"
            assert spec_mod.app_display_name("moddy") == "Moddy"
            assert spec_mod.enabled_specs("other") == ()
        finally:
            spec_mod._register_for_tests(*spec_mod.SPECS)


# --------------------------------------------------------------------------- #
# ModuleManager with an owning app
# --------------------------------------------------------------------------- #

class TestManagerOwnership:
    async def test_moddy_skips_foreign_config(self):
        db = FakeDB({"panel": {"channel": 1, APP_KEY: "bump"}})
        manager, _ = make_manager(db)
        await manager.load_guild_modules(GUILD)
        assert "panel" not in manager.active_modules[GUILD]

    async def test_minibot_loads_its_own(self):
        db = FakeDB({"panel": {"channel": 1, APP_KEY: "bump"}})
        manager, _ = make_manager(db, app="bump")
        await manager.load_guild_modules(GUILD)
        assert manager.active_modules[GUILD]["panel"].enabled

    async def test_foreign_config_blocks_moddy_defaults(self):
        db = FakeDB({"defaulted": {"channel": 1, APP_KEY: "bump"}})
        manager, _ = make_manager(db)
        await manager.load_guild_modules(GUILD)
        assert "defaulted" not in manager.active_modules[GUILD]

    async def test_minibot_defaults_gated_by_moddy_presence(self):
        from minibots.client import MiniBotClient  # noqa: F401 — import must be cheap
        db = FakeDB({}, apps={"moddy", "bump"})
        manager, _ = make_manager(db, app="bump")

        async def gate(guild_id, module_class):
            return can_claim(None, "bump", await db.apps_in_guild(guild_id),
                             moddy_default_enabled=module_class.DEFAULT_ENABLED)
        manager.default_gate = gate
        await manager.load_guild_modules(GUILD)
        assert "defaulted" not in manager.active_modules[GUILD]
        db.apps = {"bump"}
        await manager.unload_guild_modules(GUILD)
        await manager.load_guild_modules(GUILD)
        assert "defaulted" in manager.active_modules[GUILD]

    async def test_statuses_and_foreign_listing(self):
        db = FakeDB({"panel": {"channel": 1, APP_KEY: "bump"}})
        manager, _ = make_manager(db)
        statuses = await manager.get_module_statuses(GUILD)
        assert statuses["panel"] == STATUS_EXTERNAL
        assert await manager.get_foreign_modules(GUILD) == {"panel": "bump"}
        assert await manager.get_module_state(GUILD, "panel") == STATUS_EXTERNAL

    async def test_dashboard_push_on_foreign_is_refused(self):
        db = FakeDB({"panel": {"channel": 1, APP_KEY: "bump"}})
        manager, _ = make_manager(db)
        result = await manager.reload_module(GUILD, "panel")
        assert result == {"ok": False, "error": "foreign_app", "app": "bump"}
        assert PanelModule.events == []  # never touched the mini-bot's panel

    async def test_save_refused_on_foreign_and_keeps_owner(self, monkeypatch):
        db = FakeDB({"panel": {"channel": 1, APP_KEY: "bump"}})
        manager, _ = make_manager(db)
        monkeypatch.setattr(manager, "_blocked_as_new_module", AsyncMock(return_value=None))
        import utils.guild_language as gl
        monkeypatch.setattr(gl, "guild_locale", AsyncMock(return_value="en-US"))
        ok, error = await manager.save_module_config(GUILD, "panel", {"channel": 2})
        assert not ok and "Moddy" in error
        assert db.modules["panel"] == {"channel": 1, APP_KEY: "bump"}

        # The mini-bot saving its own config keeps its stamp.
        mini, _ = make_manager(db, app="bump")
        monkeypatch.setattr(mini, "_blocked_as_new_module", AsyncMock(return_value=None))
        ok, _ = await mini.save_module_config(GUILD, "panel", {"channel": 3, APP_KEY: "evil"})
        assert ok and db.modules["panel"] == {"channel": 3, APP_KEY: "bump"}

    async def test_moddy_save_never_writes_an_app_key(self, monkeypatch):
        db = FakeDB({})
        manager, _ = make_manager(db)
        monkeypatch.setattr(manager, "_blocked_as_new_module", AsyncMock(return_value=None))
        ok, _ = await manager.save_module_config(GUILD, "panel", {"channel": 1})
        assert ok and db.modules["panel"] == {"channel": 1}

    async def test_delete_and_state_refused_on_foreign(self):
        db = FakeDB({"panel": {"channel": 1, APP_KEY: "bump"}})
        manager, _ = make_manager(db)
        assert await manager.delete_module_config(GUILD, "panel") is False
        assert await manager.set_module_state(GUILD, "panel", "paused") == (False, "foreign_app")

    async def test_release_then_adopt(self):
        db = FakeDB({"panel": {"channel": 1, APP_KEY: "bump"}})
        mini, _ = make_manager(db, app="bump")
        moddy, _ = make_manager(db)
        await mini.load_guild_modules(GUILD)

        recap = await mini.release_module(GUILD, "panel", MODDY_APP)
        assert recap["released"] and recap["panel"] == "deleted"
        assert "panel" not in mini.active_modules[GUILD]
        # A lazy reload right after the release does not restart it.
        await mini.load_guild_modules(GUILD)
        assert "panel" not in mini.active_modules[GUILD]

        assert await db.set_module_app(GUILD, "panel", "bump", MODDY_APP)
        recap = await moddy.adopt_module(GUILD, "panel", "bump")
        assert recap["ok"] and recap["panel"] == "posted"
        assert db.modules["panel"] == {"channel": 1}
        assert PanelModule.events == [("bump", EXTERNAL_DELETED), (MODDY_APP, EXTERNAL_UPDATED)]


# --------------------------------------------------------------------------- #
# Transfer service
# --------------------------------------------------------------------------- #

class FakeRedis:
    def __init__(self, listeners=0, ack=None):
        self.listeners = listeners
        self.ack = ack
        self.published = []
        self.keys = {}

    async def publish(self, channel, message):
        self.published.append((channel, json.loads(message)))
        return self.listeners

    async def blpop(self, key, timeout=0):
        return (key, json.dumps(self.ack)) if self.ack is not None else None

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.keys:
            return None
        self.keys[key] = value
        return True

    async def delete(self, *keys):
        for key in keys:
            self.keys.pop(key, None)


class TestTransfer:
    def _service(self, db, redis=None):
        from services.module_transfer import ModuleTransferService
        manager, bot = make_manager(db)
        bot.redis = redis
        bot.tech_logger = SimpleNamespace(log_module_transfer=AsyncMock())
        bot.stats = SimpleNamespace(incr=lambda *a, **k: bot.__dict__.setdefault("counted", []).append(k))
        bot.module_transfer = ModuleTransferService(bot)
        return bot.module_transfer, bot

    async def test_acked_release(self):
        db = FakeDB({"panel": {"channel": 1, APP_KEY: "bump"}})
        redis = FakeRedis(listeners=1, ack={"ok": True, "released": True})
        service, bot = self._service(db, redis)
        result = await service.transfer(GUILD, "panel", actor_id=5)
        assert result.ok and result.release == "acked" and result.from_app == "bump"
        assert db.modules["panel"] == {"channel": 1}
        sent = redis.published[0][1]
        assert sent["type"] == mstatus.CTRL_RELEASE and sent["app"] == "bump"
        # Moddy re-posted its own panel; the mini-bot cleaned up its own.
        assert PanelModule.events == [(MODDY_APP, EXTERNAL_UPDATED)]
        bot.tech_logger.log_module_transfer.assert_awaited()
        assert bot.counted[0]["dims"]["via"] == "config"
        assert not redis.keys  # lock released

    async def test_fallback_when_runtime_silent(self):
        db = FakeDB({"panel": {"channel": 1, APP_KEY: "bump"}})
        service, _ = self._service(db, FakeRedis(listeners=0))
        result = await service.transfer(GUILD, "panel")
        assert result.ok and result.release == "fallback"
        # Moddy took the old panel down itself, then posted its own.
        assert PanelModule.events == [(MODDY_APP, EXTERNAL_DELETED), (MODDY_APP, EXTERNAL_UPDATED)]

    async def test_second_click_is_harmless(self):
        db = FakeDB({"panel": {"channel": 1, APP_KEY: "bump"}})
        service, _ = self._service(db, FakeRedis(listeners=0))
        assert (await service.transfer(GUILD, "panel")).ok
        again = await service.transfer(GUILD, "panel")
        assert not again.ok and again.error == "already_owned"
        assert await service.transfer_app(GUILD, "bump") == []

    async def test_lock_blocks_concurrent_transfer(self):
        db = FakeDB({"panel": {"channel": 1, APP_KEY: "bump"}})
        redis = FakeRedis(listeners=0)
        redis.keys[mstatus.transfer_lock_key(GUILD, "panel")] = "1"
        service, _ = self._service(db, redis)
        result = await service.transfer(GUILD, "panel")
        assert not result.ok and result.error == "in_progress"

    async def test_transfer_app_moves_only_that_app(self):
        db = FakeDB({"panel": {"channel": 1, APP_KEY: "bump"},
                     "defaulted": {"channel": 1, APP_KEY: "other"}})
        service, _ = self._service(db)
        results = await service.transfer_app(GUILD, "bump", via="dm")
        assert [r.module_id for r in results] == ["panel"]
        assert db.modules["defaulted"][APP_KEY] == "other"


# --------------------------------------------------------------------------- #
# Transfer UI
# --------------------------------------------------------------------------- #

class TestTransferUI:
    def test_button_round_trips(self):
        from utils.minibot_transfer import TransferToModdyButton
        button = TransferToModdyButton(GUILD, "bump_reminder")
        cid = button.item.custom_id
        match = TransferToModdyButton.__discord_ui_compiled_template__.fullmatch(cid)
        assert match and int(match["guild"]) == GUILD and match["app"] == "bump_reminder"
        assert len(cid) <= 100

    def test_group_by_app(self):
        from utils.minibot_transfer import group_by_app
        assert group_by_app({"b": "x", "a": "x", "c": "y"}) == {"x": ["a", "b"], "y": ["c"]}

    async def test_no_offer_without_foreign_modules(self):
        from utils.minibot_transfer import offer_transfer
        bot = SimpleNamespace(module_transfer=SimpleNamespace(transferable=AsyncMock(return_value={})),
                              notifications=SimpleNamespace(send_dm=AsyncMock()))
        guild = SimpleNamespace(id=GUILD, name="S")
        assert await offer_transfer(bot, guild) == 0
        bot.notifications.send_dm.assert_not_awaited()

    async def test_one_offer_per_app(self, monkeypatch):
        import utils.minibot_transfer as mt
        import utils.guild_language as gl
        import utils.install_welcome as iw
        monkeypatch.setattr(gl, "guild_locale", AsyncMock(return_value="fr"))
        monkeypatch.setattr(iw, "resolve_installer",
                            AsyncMock(return_value=SimpleNamespace(id=1, bot=False)))
        send = AsyncMock(return_value=SimpleNamespace(delivered=True))
        bot = SimpleNamespace(
            module_transfer=SimpleNamespace(transferable=AsyncMock(
                return_value={"a": "bump", "b": "bump", "c": "music"})),
            notifications=SimpleNamespace(send_dm=send),
            module_manager=SimpleNamespace(registered_modules={}),
        )
        assert await mt.offer_transfer(bot, SimpleNamespace(id=GUILD, name="S")) == 2
        variables = [call.kwargs["variables"]["modules"] for call in send.await_args_list]
        assert variables == ["`a`, `b`", "`c`"]
        content = send.await_args_list[0].kwargs["content"]
        assert "{app}" in content.title and "{modules}" in content.body  # stored as template

    async def test_click_refused_without_manage_server(self, monkeypatch):
        import utils.minibot_transfer as mt
        sent = AsyncMock()
        interaction = SimpleNamespace(
            guild_id=None, permissions=None, user=SimpleNamespace(id=9),
            client=SimpleNamespace(get_guild=lambda gid: None, module_transfer=None),
            response=SimpleNamespace(send_message=sent, defer=AsyncMock()),
            locale="en-US",
        )
        monkeypatch.setattr(mt.i18n, "get_user_locale", lambda i: "en-US")
        await mt.TransferToModdyButton(GUILD, "bump").callback(interaction)
        sent.assert_awaited_once()
        interaction.response.defer.assert_not_awaited()


# --------------------------------------------------------------------------- #
# Runtime + client
# --------------------------------------------------------------------------- #

SPEC = MiniBotSpec(id="bump", name="Bump Reminder", token_env="MINIBOT_TEST_TOKEN",
                   modules=("panel",), intents=frozenset({"guild_messages"}))


class TestClient:
    def test_lean_cache(self):
        from minibots.client import MiniBotClient
        resources = SimpleNamespace(db=None, redis=None, tech_logger=None)
        client = MiniBotClient(SPEC, resources)
        assert client.intents.guilds and client.intents.guild_messages
        assert not client.intents.members and not client.intents.presences
        assert not client.intents.message_content
        assert client._connection.max_messages is None
        assert client._connection.member_cache_flags.value == 0
        assert client._connection._chunk_guilds is False
        assert client.module_manager.app == "bump"


class TestRuntime:
    async def test_no_token_never_connects(self, monkeypatch):
        from minibots.runtime import MiniBotRuntime
        monkeypatch.delenv("MINIBOT_TEST_TOKEN", raising=False)
        runtime = MiniBotRuntime((SPEC,), SimpleNamespace(redis=None, tech_logger=None, version=None))
        await runtime.start()
        assert runtime.apps["bump"].state == mstatus.STATE_NO_TOKEN
        assert runtime.client_for("bump") is None
        await runtime.stop()

    async def test_empty_runtime_starts_and_stops(self):
        from minibots.runtime import MiniBotRuntime
        runtime = MiniBotRuntime((), SimpleNamespace(redis=None, tech_logger=None, version=None))
        await runtime.start()
        await runtime.stop()

    async def test_release_acks_through_redis(self):
        from minibots.runtime import MiniBotRuntime
        pushed = {}

        class R:
            async def rpush(self, key, value):
                pushed[key] = json.loads(value)

            async def expire(self, key, ttl):
                pass

        db = FakeDB({"panel": {"channel": 1, APP_KEY: "bump"}})
        manager, bot = make_manager(db, app="bump")
        client = SimpleNamespace(app_id="bump", module_manager=manager, is_ready=lambda: True)
        runtime = MiniBotRuntime((SPEC,), SimpleNamespace(redis=R(), tech_logger=None, version=None))
        runtime.apps["bump"].client = client
        await runtime.handle_control({"type": "release", "request_id": "r1", "guild_id": str(GUILD),
                                      "module_id": "panel", "app": "bump", "to_app": "moddy"})
        assert pushed[mstatus.ack_key("r1")]["released"] is True

    async def test_release_without_ready_client_does_not_ack(self):
        from minibots.runtime import MiniBotRuntime
        runtime = MiniBotRuntime((SPEC,), SimpleNamespace(redis=None, tech_logger=None, version=None))
        runtime.apps["bump"].client = SimpleNamespace(is_ready=lambda: False)
        await runtime.handle_control({"type": "release", "request_id": "r", "guild_id": "1",
                                      "module_id": "panel", "app": "bump"})  # no redis touched

    async def test_crash_is_supervised(self, monkeypatch):
        import minibots.runtime as rt
        monkeypatch.setenv("MINIBOT_TEST_TOKEN", "x")
        monkeypatch.setattr(rt, "BACKOFF_START", 0)
        starts = []

        class Boom:
            app_id = "bump"

            async def start(self, token):
                starts.append(token)
                if len(starts) == 1:
                    raise RuntimeError("gateway gone")
                await asyncio.sleep(3600)

            def is_closed(self):
                return True

            async def close(self):
                pass

        runtime = rt.MiniBotRuntime((SPEC,), SimpleNamespace(redis=None, tech_logger=None, version=None),
                                    client_factory=lambda *a: Boom())
        await runtime.start()
        for _ in range(50):
            if len(starts) >= 2:
                break
            await asyncio.sleep(0.01)
        assert len(starts) == 2 and runtime.apps["bump"].restarts == 1
        await runtime.stop()


# --------------------------------------------------------------------------- #
# Redis contract
# --------------------------------------------------------------------------- #

class TestStatusContract:
    def test_app_status_round_trip_and_staleness(self):
        status = mstatus.AppStatus(app="bump", state=mstatus.STATE_READY, guilds=3, updated_at=100)
        back = mstatus.AppStatus.from_json(status.to_json())
        assert back == status
        assert back.effective_state(now=150) == mstatus.STATE_READY
        assert back.effective_state(now=100 + mstatus.STALE_AFTER + 1) == mstatus.STATE_SILENT

    def test_control_messages(self):
        raw = mstatus.control_message(mstatus.CTRL_RESTART, app="bump")
        assert mstatus.parse_control(raw) == {"type": "restart", "app": "bump"}
        assert mstatus.parse_control("nope") is None
        assert mstatus.parse_control(json.dumps({"type": "drop_table"})) is None
        with pytest.raises(ValueError):
            mstatus.control_message("leave_guild")

    def test_namespace_is_not_backend_owned(self):
        for key in (mstatus.STATUS_KEY, mstatus.APPS_KEY, mstatus.CONTROL_CHANNEL,
                    mstatus.ack_key("x"), mstatus.transfer_lock_key(1, "m")):
            assert key.startswith("minibots:")


# --------------------------------------------------------------------------- #
# RAM budget: the mini-bots process never pulls Moddy's heavy code
# --------------------------------------------------------------------------- #

def test_minibots_process_imports_stay_light():
    code = (
        "import sys, os; os.environ['MODDY_SERVICE']='minibots';"
        "import minibots.main, minibots.runtime, minibots.client, minibots.resources;"
        "import utils.tech_logger, db.base, modules.module_manager;"
        "heavy=[m for m in ('bot','cogs','onnxruntime','PIL','c2pa','numpy','openai',"
        "'fastapi','uvicorn','staff') if m in sys.modules];"
        "print('HEAVY=' + ','.join(heavy))"
    )
    env = {k: v for k, v in __import__("os").environ.items() if k != "DISCORD_TOKEN"}
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True,
                         text=True, env=env, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().splitlines()[-1] == "HEAVY="


# --------------------------------------------------------------------------- #
# i18n + wiring
# --------------------------------------------------------------------------- #

def _get(d, path):
    for part in path.split("."):
        d = d[part]
    return d


@pytest.mark.parametrize("locale", LOCALES)
def test_i18n_complete(locale):
    data = json.loads((ROOT / "locales" / f"{locale}.json").read_text(encoding="utf-8"))
    reference = json.loads((ROOT / "locales" / "en-US.json").read_text(encoding="utf-8"))

    def leaves(d, prefix=""):
        for k, v in d.items():
            if isinstance(v, dict):
                yield from leaves(v, f"{prefix}{k}.")
            else:
                yield f"{prefix}{k}"

    for root in ("minibots", "staff.dev.minibots", "staff.dev.minibot"):
        for key in leaves(_get(reference, root), root + "."):
            assert isinstance(_get(data, key), str) and _get(data, key), key
    for key in ("modules.config.state.external", "modules.config.external.save_refused",
                "staff.team.server.minibots", "staff.team.server.minibots_present",
                "staff.team.server.minibots_modules"):
        assert _get(data, key), key
    reasons = _get(data, "minibots.transfer.reasons")
    for error in ("unavailable", "unknown_module", "in_progress", "not_configured",
                  "already_owned", "changed", "internal_error", "invalid_config", "not_owned"):
        assert reasons[error]


def test_transfer_button_is_registered_persistent():
    from utils.persistent_views import _collect_persistent_view_classes
    from utils.minibot_transfer import MinibotTransferPersistence
    assert MinibotTransferPersistence in _collect_persistent_view_classes()


def test_tech_log_category_declared():
    import config
    assert config.LOG_WEBHOOK_ENV["minibots"] == "LOG_WEBHOOK_MINIBOTS"


def test_transfer_metric_declared():
    from stats.registry import get
    assert get("minibots.transfer").dimensions == ("from_app", "module", "via")
