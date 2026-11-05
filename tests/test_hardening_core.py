"""Chaos-resilience tests for the core (boot, Postgres/Redis outages, background
work, error handler, internal API, notifications).

Nothing here talks to Discord, Postgres or Redis: bots, pools and clients are
stand-ins that fail the way the real ones do.

    pytest tests/test_hardening_core.py -q
"""
import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import discord
import pytest

os.environ.setdefault("DISCORD_TOKEN", "test-token")


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Database layer
# ---------------------------------------------------------------------------

class TestResilientAcquire:
    def test_pool_is_reclassed_and_retries_transient_errors(self, monkeypatch):
        import db.base as base

        monkeypatch.setattr(base, "_ACQUIRE_RETRY_DELAYS", (0.0, 0.0))
        calls = {"n": 0}

        class Ctx:
            async def __aenter__(self):
                calls["n"] += 1
                if calls["n"] < 3:
                    raise ConnectionRefusedError("postgres restarting")
                return "conn"

            async def __aexit__(self, *exc):
                return False

        def fake_acquire(*, timeout=None):
            return Ctx()

        async def go():
            async with base._ResilientAcquire(fake_acquire, None) as conn:
                return conn

        assert run(go()) == "conn"
        assert calls["n"] == 3

    def test_gives_up_after_the_last_retry(self, monkeypatch):
        import db.base as base

        monkeypatch.setattr(base, "_ACQUIRE_RETRY_DELAYS", (0.0,))

        class Ctx:
            async def __aenter__(self):
                raise asyncpg.CannotConnectNowError("starting up")

            async def __aexit__(self, *exc):
                return False

        async def go():
            async with base._ResilientAcquire(lambda *, timeout=None: Ctx(), None):
                pass

        with pytest.raises(asyncpg.CannotConnectNowError):
            run(go())

    def test_non_transient_errors_are_not_retried(self, monkeypatch):
        import db.base as base

        monkeypatch.setattr(base, "_ACQUIRE_RETRY_DELAYS", (0.0, 0.0))
        calls = {"n": 0}

        class Ctx:
            async def __aenter__(self):
                calls["n"] += 1
                raise asyncpg.InvalidPasswordError("bad password")

            async def __aexit__(self, *exc):
                return False

        async def go():
            async with base._ResilientAcquire(lambda *, timeout=None: Ctx(), None):
                pass

        with pytest.raises(asyncpg.InvalidPasswordError):
            run(go())
        assert calls["n"] == 1

    def test_real_pool_class_swap(self):
        import db.base as base

        async def go():
            pool = asyncpg.create_pool("postgresql://x@127.0.0.1:1/x", min_size=0, max_size=1)
            base._install_resilient_acquire(pool)
            return type(pool)

        assert run(go()) is base._ResilientPool


class TestConnectFailureCleanup:
    def test_failed_init_tables_terminates_the_pool(self, monkeypatch):
        import db.base as base

        pool = MagicMock()

        async def fake_create_pool(*a, **k):
            return pool

        monkeypatch.setattr(base.asyncpg, "create_pool", fake_create_pool)
        monkeypatch.setattr(base, "_install_resilient_acquire", lambda p: None)
        database = base.ModdyDatabase("postgresql://x")
        database._init_tables = AsyncMock(side_effect=RuntimeError("boom"))

        with pytest.raises(RuntimeError):
            run(database.connect())
        pool.terminate.assert_called_once()
        assert database.pool is None

    def test_setup_database_keeps_the_global_untouched_on_failure(self, monkeypatch):
        import db as dbpkg

        async def bad_connect(self):
            raise ConnectionRefusedError("down")

        monkeypatch.setattr(dbpkg.ModdyDatabase, "connect", bad_connect)
        before = dbpkg.db
        with pytest.raises(ConnectionRefusedError):
            run(dbpkg.setup_database("postgresql://x"))
        assert dbpkg.db is before

    def test_ping_never_raises(self):
        import db.base as base

        database = base.ModdyDatabase("postgresql://x")
        assert run(database.ping()) is False  # no pool

        class BrokenPool:
            def acquire(self):
                raise asyncpg.InterfaceError("pool is closed")

        database.pool = BrokenPool()
        assert run(database.ping()) is False


# ---------------------------------------------------------------------------
# Bot boot / recovery
# ---------------------------------------------------------------------------

def _fake_bot(**extra):
    from bot import ModdyBot

    fake = SimpleNamespace(
        db=None, db_pool=None, redis=None,
        _bg_tasks=set(), _boot_stage_failures=[],
        _db_dependent_init_done=False, _guild_commands_synced=False,
        gateway=MagicMock(), _dev_team_ids=set(),
        is_closed=lambda: False, is_ready=lambda: True,
    )
    fake.spawn = lambda coro, name=None: ModdyBot.spawn(fake, coro, name=name)
    for key, value in extra.items():
        setattr(fake, key, value)
    return fake


class TestBootStages:
    def test_a_failing_stage_is_recorded_and_boot_continues(self):
        from bot import ModdyBot

        fake = _fake_bot()

        async def go():
            async def broken():
                raise ValueError("translator exploded")

            ok_bad = await ModdyBot._boot_stage(fake, "translator", broken())
            ok_good = await ModdyBot._boot_stage(fake, "fine", asyncio.sleep(0))
            return ok_bad, ok_good

        assert run(go()) == (False, True)
        assert fake._boot_stage_failures == ["translator"]

    def test_sync_stage_twin(self):
        from bot import ModdyBot

        fake = _fake_bot()

        def boom():
            raise RuntimeError("x")

        assert ModdyBot._boot_stage_sync(fake, "tech", boom) is False
        assert ModdyBot._boot_stage_sync(fake, "ok", lambda: None) is True

    def test_spawn_keeps_a_reference_and_logs_the_crash(self, caplog):
        from bot import ModdyBot

        fake = _fake_bot()

        async def go():
            async def crash():
                raise RuntimeError("background boom")

            task = ModdyBot.spawn(fake, crash(), name="crasher")
            assert task in fake._bg_tasks
            await asyncio.sleep(0.01)
            assert task not in fake._bg_tasks

        with caplog.at_level("ERROR"):
            run(go())
        assert "crasher" in caplog.text and "background boom" in caplog.text


class TestDatabaseRecovery:
    def test_down_at_boot_starts_the_background_recovery(self, monkeypatch):
        import bot as botmod
        from bot import ModdyBot

        async def failing(url):
            raise ConnectionRefusedError("postgres is down")

        monkeypatch.setattr(botmod, "setup_database", failing)
        monkeypatch.setattr(botmod, "DATABASE_URL", "postgresql://x")
        started = []
        fake = _fake_bot()
        fake._connect_database = lambda boot=False: ModdyBot._connect_database(fake, boot=boot)
        fake._start_db_recovery = lambda: started.append(True)

        run(ModdyBot.setup_database(fake))
        assert fake.db is None and started == [True]

    def test_recovery_loop_reconnects_and_finishes_the_boot_work(self, monkeypatch):
        from bot import ModdyBot

        attempts = {"n": 0}
        fake = _fake_bot()
        phases = []

        async def connect(boot=False):
            attempts["n"] += 1
            if attempts["n"] < 3:
                return False
            fake.db = object()
            return True

        async def phase():
            phases.append("ready-phases")

        fake._connect_database = connect
        fake._run_ready_phases = phase

        real_sleep = asyncio.sleep

        async def fast_sleep(delay):
            await real_sleep(0)

        monkeypatch.setattr(asyncio, "sleep", fast_sleep)
        run(ModdyBot._db_recovery_loop(fake))
        assert attempts["n"] == 3
        assert phases == ["ready-phases"]

    def test_db_dependent_init_runs_once_even_if_every_step_fails(self):
        from bot import ModdyBot

        db = MagicMock()
        db.reconcile_app_guilds = AsyncMock(side_effect=RuntimeError("down"))
        db.get_stats = AsyncMock(side_effect=RuntimeError("down"))
        fake = _fake_bot(
            db=db, guilds=[], tickets=SimpleNamespace(
                hydrate_open_tickets=AsyncMock(side_effect=RuntimeError("down"))),
            module_manager=SimpleNamespace(load_all_modules=AsyncMock(side_effect=RuntimeError("down"))),
            _dev_team_ids=set(), user=SimpleNamespace(id=1),
        )
        run(ModdyBot._db_dependent_init(fake))
        assert fake._db_dependent_init_done is True


class TestOnReadyIdempotence:
    def test_second_on_ready_does_not_resync_or_reload(self):
        from bot import ModdyBot

        fake = _fake_bot(db=object())

        async def init():
            fake._db_dependent_init_done = True  # what the real one sets first

        fake._db_dependent_init = AsyncMock(side_effect=init)
        fake.sync_all_guild_commands = AsyncMock()

        async def go():
            await ModdyBot._run_ready_phases(fake)
            await ModdyBot._run_ready_phases(fake)

        run(go())
        fake._db_dependent_init.assert_awaited_once()
        fake.sync_all_guild_commands.assert_awaited_once()

    def test_guild_sync_waits_for_the_database(self, monkeypatch):
        import bot as botmod
        from bot import ModdyBot

        monkeypatch.setattr(botmod, "DATABASE_URL", "postgresql://x")
        fake = _fake_bot(db=None)
        fake._db_dependent_init = AsyncMock()
        fake.sync_all_guild_commands = AsyncMock()
        run(ModdyBot._run_ready_phases(fake))
        fake.sync_all_guild_commands.assert_not_awaited()
        assert fake._guild_commands_synced is False


class TestCommandSync:
    def test_sync_is_aborted_not_wrong_when_official_guilds_are_unreadable(self, monkeypatch):
        import bot as botmod
        from bot import ModdyBot

        monkeypatch.setattr(botmod, "DATABASE_URL", "postgresql://x")
        db = MagicMock()
        db.get_guilds_with_attribute = AsyncMock(side_effect=asyncpg.InterfaceError("closed"))
        fake = _fake_bot(db=db)

        with pytest.raises(asyncpg.InterfaceError):
            run(ModdyBot.get_official_guild_ids(fake, strict=True))
        # non strict (old behaviour for other callers) still degrades to empty
        assert run(ModdyBot.get_official_guild_ids(fake)) == set()

    def test_one_guild_failing_does_not_stop_the_others(self):
        from bot import ModdyBot

        good, bad = SimpleNamespace(id=1, name="ok"), SimpleNamespace(id=2, name="bad")
        retried = []
        fake = _fake_bot(guilds=[bad, good], module_slash_commands={})
        fake.get_official_guild_ids = AsyncMock(return_value=set())

        async def sync_one(guild, official_ids):
            return guild is good

        fake._sync_one_guild = sync_one
        fake._retry_failed_guild_syncs = lambda ids: retried.append(list(ids) if ids else ids) or asyncio.sleep(0)
        fake._guild_only_commands = []
        assert run(ModdyBot.sync_all_guild_commands(fake)) is False
        assert retried == [[2]]

    def test_global_sync_failure_schedules_a_retry(self):
        from bot import ModdyBot

        spawned = []
        fake = _fake_bot(_guild_only_commands=[])
        fake.tree = SimpleNamespace(
            walk_commands=lambda: [], sync=AsyncMock(side_effect=discord.HTTPException(
                SimpleNamespace(status=500, reason="x"), "boom")))
        fake.spawn = lambda coro, name=None: (spawned.append(name), coro.close())
        fake._retry_global_sync = lambda: asyncio.sleep(0)
        run(ModdyBot.sync_commands(fake))
        assert spawned == ["retry-global-sync"]


class TestRedis:
    def test_down_at_boot_is_retried_in_the_background(self, monkeypatch):
        import redis.asyncio as aioredis
        from bot import ModdyBot

        class DeadClient:
            async def ping(self):
                raise ConnectionError("redis down")

            async def aclose(self):
                pass

        monkeypatch.setattr(aioredis, "from_url", lambda *a, **k: DeadClient())
        started = []
        fake = _fake_bot()
        fake._connect_redis = lambda: ModdyBot._connect_redis(fake)
        fake._start_redis_recovery = lambda: started.append(True)
        run(ModdyBot._setup_redis(fake))
        assert fake.redis is None and started == [True]

    def test_recovery_installs_the_client_and_starts_the_listeners_once(self, monkeypatch):
        import redis.asyncio as aioredis
        from bot import ModdyBot

        class GoodClient:
            async def ping(self):
                return True

        monkeypatch.setattr(aioredis, "from_url", lambda *a, **k: GoodClient())
        fake = _fake_bot()

        async def idle():
            await asyncio.sleep(10)

        fake._listen_pubsub = idle
        fake._consume_task_stream = idle

        async def go():
            assert await ModdyBot._connect_redis(fake) is True
            first = (fake._pubsub_task, fake._task_stream_task)
            assert await ModdyBot._connect_redis(fake) is True
            assert (fake._pubsub_task, fake._task_stream_task) == first  # not duplicated
            for t in list(fake._bg_tasks):
                t.cancel()

        run(go())
        fake.gateway.rebind.assert_called()

    def test_pubsub_listener_survives_errors_and_resubscribes(self, monkeypatch):
        from bot import ModdyBot

        subscribes = {"n": 0}

        class PubSub:
            async def subscribe(self, *channels):
                subscribes["n"] += 1
                if subscribes["n"] == 1:
                    raise ConnectionError("lost")

            async def listen(self):
                yield {"type": "message", "channel": "moddy:bot", "data": "not json"}
                yield {"type": "message", "channel": "moddy:bot", "data": '{"type": "x"}'}
                raise ConnectionError("dropped")

            async def aclose(self):
                pass

        redis = SimpleNamespace(pubsub=lambda: PubSub())
        handled = []
        fake = _fake_bot(redis=redis)
        fake._handle_bot_event = AsyncMock(side_effect=lambda d: handled.append(d))

        real_sleep = asyncio.sleep
        stop = {"after": 0}

        async def fast_sleep(delay):
            stop["after"] += 1
            if stop["after"] > 3:
                fake.is_closed = lambda: True
            await real_sleep(0)

        monkeypatch.setattr(asyncio, "sleep", fast_sleep)
        run(ModdyBot._listen_pubsub(fake))
        assert subscribes["n"] >= 2          # reconnected after the first failure
        assert handled and handled[0] == {"type": "x"}  # a bad message cost only itself

    def test_gateway_rebind_replaces_the_captured_clients(self):
        # tests/gateway (no __init__) can shadow the real package depending on
        # collection order, so load the real one under an alias.
        import importlib.util
        import pathlib
        import sys

        root = pathlib.Path(__file__).resolve().parent.parent / "gateway"
        spec = importlib.util.spec_from_file_location(
            "gateway_real", root / "__init__.py", submodule_search_locations=[str(root)])
        module = importlib.util.module_from_spec(spec)
        sys.modules["gateway_real"] = module
        try:
            spec.loader.exec_module(module)
        finally:
            sys.modules.pop("gateway_real", None)
        Gateway = module.Gateway

        gw = Gateway.__new__(Gateway)
        gw._quota = SimpleNamespace(_redis=None, _pool=None)
        gw._ratelimiter = SimpleNamespace(_redis=None)
        gw._gw_logger = SimpleNamespace(_redis=None, _pool=None)
        gw.rebind(redis="R", pool="P")
        assert gw._quota._redis == "R" and gw._quota._pool == "P"
        assert gw._ratelimiter._redis == "R"
        assert gw._gw_logger._pool == "P"
        Gateway().rebind(redis="R")  # before start(): no-op


class TestCaseExpiryLoop:
    def test_expirations_failure_does_not_kill_the_loop(self):
        from bot import ModdyBot

        fake = _fake_bot(
            db=SimpleNamespace(expire_due_sanctions=AsyncMock(return_value=[{"id": 1}])),
            expirations=SimpleNamespace(process=AsyncMock(side_effect=RuntimeError("discord 5xx"))),
        )
        # call the undecorated coroutine of the loop
        run(ModdyBot.case_expiry.coro(fake))
        fake.expirations.process.assert_awaited_once()


class TestOnError:
    def test_listener_error_is_logged_and_reported_without_raising(self):
        from bot import ModdyBot

        tracker = SimpleNamespace(send_ops_notice=AsyncMock())
        fake = _fake_bot(get_cog=lambda name: tracker)

        async def go():
            try:
                raise KeyError("listener bug")
            except KeyError:
                await ModdyBot.on_error(fake, "on_message")

        run(go())
        tracker.send_ops_notice.assert_awaited_once()

    def test_on_error_never_raises_even_if_the_notice_fails(self):
        from bot import ModdyBot

        tracker = SimpleNamespace(send_ops_notice=AsyncMock(side_effect=RuntimeError("channel gone")))
        fake = _fake_bot(get_cog=lambda name: tracker)

        async def go():
            try:
                raise KeyError("x")
            except KeyError:
                await ModdyBot.on_error(fake, "on_member_join")

        run(go())


# ---------------------------------------------------------------------------
# main.py
# ---------------------------------------------------------------------------

class TestMain:
    def test_transient_login_errors_are_classified(self):
        import main

        assert main._is_transient_login_error(asyncio.TimeoutError())
        assert main._is_transient_login_error(ConnectionResetError())
        resp = SimpleNamespace(status=503, reason="x")
        assert main._is_transient_login_error(discord.HTTPException(resp, "unavailable"))
        resp429 = SimpleNamespace(status=429, reason="x")
        assert main._is_transient_login_error(discord.HTTPException(resp429, "slow down"))
        assert not main._is_transient_login_error(discord.LoginFailure("bad token"))
        assert not main._is_transient_login_error(ValueError("bug"))

    def test_api_server_crash_is_retried_and_never_propagates(self, monkeypatch):
        import main

        calls = {"n": 0}

        async def flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("address already in use")
            if calls["n"] == 2:
                raise SystemExit(1)  # uvicorn exits like this when the bind fails
            return None

        monkeypatch.setattr(main, "start_api_server", flaky)
        real_sleep = asyncio.sleep

        async def fast(delay):
            await real_sleep(0)

        monkeypatch.setattr(asyncio, "sleep", fast)
        run(main.supervised_api_server())
        assert calls["n"] == 3


# ---------------------------------------------------------------------------
# Persistent views
# ---------------------------------------------------------------------------

class TestPersistentViewRegistry:
    def test_broken_registry_falls_back_instead_of_registering_nothing(self, monkeypatch):
        import utils.persistent_views as pv

        registered = []

        class Good:
            __persistent__ = True

            @classmethod
            def register_persistent(cls, bot):
                registered.append(cls)

        def broken():
            raise ImportError("cogs.moddy has a syntax error")

        monkeypatch.setattr(pv, "_collect_persistent_view_classes", broken)
        monkeypatch.setattr(pv, "_discover_loaded_persistent_classes", lambda: [Good])
        pv.register_all_persistent_views(object())
        assert registered == [Good]

    def test_one_class_raising_does_not_stop_the_others(self, monkeypatch):
        import utils.persistent_views as pv

        order = []

        class Bad:
            __persistent__ = True

            @classmethod
            def register_persistent(cls, bot):
                raise RuntimeError("boom")

        class Good:
            __persistent__ = True

            @classmethod
            def register_persistent(cls, bot):
                order.append("good")

        monkeypatch.setattr(pv, "_collect_persistent_view_classes", lambda: [Bad, Good])
        pv.register_all_persistent_views(object())
        assert order == ["good"]

    def test_discovery_finds_real_persistent_classes(self):
        import utils.persistent_views as pv

        pv._collect_persistent_view_classes()  # imports everything
        found = pv._discover_loaded_persistent_classes()
        assert len(found) > 10


# ---------------------------------------------------------------------------
# Error handler
# ---------------------------------------------------------------------------

class TestErrorHandlerNeverCascades:
    def test_report_interaction_error_swallows_a_failing_tracker(self):
        from cogs import error_handler as eh

        class Boom:
            def generate_error_code(self, e):
                raise RuntimeError("tracker exploded")

        bot = SimpleNamespace(get_cog=lambda name: Boom(), db=None)
        interaction = SimpleNamespace(
            client=bot, user=SimpleNamespace(id=1), guild=None, channel=None, guild_id=None,
            extras={}, response=SimpleNamespace(is_done=lambda: True), id=5,
            created_at=discord.utils.utcnow(), locale="en-US", data={}, type=None,
        )
        assert run(eh.report_interaction_error(
            interaction, ValueError("x"), command="c", error_type="t")) is None

    def test_error_channel_failure_does_not_lose_the_user_card(self):
        from cogs import error_handler as eh

        tracker = eh.ErrorTracker(SimpleNamespace(db=None, get_channel=lambda i: SimpleNamespace(
            send=AsyncMock(side_effect=discord.Forbidden(SimpleNamespace(status=403, reason="x"), "no")))))
        details = {"type": "T", "file": "f", "line": "1", "message": "m"}
        # must not raise
        run(tracker.send_error_log("ABCD", details))

    def test_dead_interaction_and_dead_db_still_return_cleanly(self):
        from cogs import error_handler as eh

        db = SimpleNamespace(log_error=AsyncMock(side_effect=asyncpg.InterfaceError("pool closed")))
        bot = SimpleNamespace(db=db, get_cog=lambda n: None)
        tracker = eh.ErrorTracker(bot)
        bot.get_cog = lambda n: tracker if n == "ErrorTracker" else None

        gone = discord.NotFound(SimpleNamespace(status=404, reason="x"), {"code": 10062, "message": "Unknown interaction"})
        response = SimpleNamespace(is_done=lambda: False, send_message=AsyncMock(side_effect=gone))
        interaction = SimpleNamespace(
            client=bot, user=SimpleNamespace(id=1, send=AsyncMock(side_effect=gone)),
            guild=None, channel=None, guild_id=None, extras={}, response=response,
            followup=SimpleNamespace(send=AsyncMock(side_effect=gone)),
            edit_original_response=AsyncMock(side_effect=gone), id=7,
            created_at=discord.utils.utcnow(), locale="en-US", data={}, type=None,
        )
        # must complete without raising, whatever fails along the way
        run(eh.report_interaction_error(interaction, ValueError("x"), command="c", error_type="t"))


# ---------------------------------------------------------------------------
# Internal API
# ---------------------------------------------------------------------------

class TestInternalApiHealth:
    def _client(self, monkeypatch, bot, **env):
        from fastapi.testclient import TestClient
        import internal_api.server as server

        monkeypatch.setattr(server, "_bot", bot)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        return TestClient(server.app)

    def test_health_is_200_even_without_a_bot(self, monkeypatch):
        r = self._client(monkeypatch, None).get("/health")
        assert r.status_code == 200 and r.json()["status"] == "starting"

    def test_health_reports_a_down_database_honestly_but_stays_200(self, monkeypatch):
        bot = SimpleNamespace(is_ready=lambda: True, db=None, redis=object())
        r = self._client(monkeypatch, bot, DATABASE_URL="postgresql://x", REDIS_URL="redis://x").get("/health")
        body = r.json()
        assert r.status_code == 200
        assert body["status"] == "degraded" and body["checks"]["database"] == "down"
        assert body["checks"]["redis"] == "ok"

    def test_health_healthy(self, monkeypatch):
        bot = SimpleNamespace(is_ready=lambda: True, db=SimpleNamespace(pool=object()), redis=object())
        r = self._client(monkeypatch, bot, DATABASE_URL="postgresql://x", REDIS_URL="redis://x").get("/health")
        assert r.json()["status"] == "healthy"

    def test_health_never_500s_on_a_broken_bot(self, monkeypatch):
        class Broken:
            def is_ready(self):
                raise RuntimeError("weird state")

        r = self._client(monkeypatch, Broken()).get("/health")
        assert r.status_code == 200

    def test_status_with_nan_latency_is_valid_json(self, monkeypatch):
        bot = SimpleNamespace(
            is_ready=lambda: True, shard_count=None, latency=float("nan"),
            guilds=[], users=[], _start_time=0)
        client = self._client(monkeypatch, bot, INTERNAL_API_SECRET="s")
        r = client.get("/status", headers={"Authorization": "Bearer s"})
        assert r.status_code == 200 and r.json()["latency_ms"] == 0


# ---------------------------------------------------------------------------
# Notifications: a DM never raises into the caller
# ---------------------------------------------------------------------------

class TestNotificationsNeverRaise:
    def _service(self):
        from notifications.service import NotificationService

        bot = SimpleNamespace(db=None, stats=None)
        return NotificationService(bot)

    def _content(self):
        from notifications.models import NotificationContent

        return NotificationContent(title="t", body="b", template_id="x")

    def test_blocked_bot_50007_is_a_failed_result(self):
        from notifications.models import NotificationSource

        service = self._service()
        forbidden = discord.Forbidden(SimpleNamespace(status=403, reason="x"),
                                      {"code": 50007, "message": "Cannot send messages to this user"})
        user = SimpleNamespace(id=5, send=AsyncMock(side_effect=forbidden))
        result = run(service.send_dm(user, content=self._content(),
                                     source=NotificationSource.service("test")))
        assert not result.delivered and result.forbidden

    def test_network_error_while_sending_is_a_failed_result(self):
        from notifications.models import NotificationSource

        service = self._service()
        user = SimpleNamespace(id=5, send=AsyncMock(side_effect=asyncio.TimeoutError()))
        result = run(service.send_dm(user, content=self._content(),
                                     source=NotificationSource.service("test")))
        assert not result.delivered and isinstance(result.error, asyncio.TimeoutError)

    def test_render_failure_is_a_failed_result(self, monkeypatch):
        from notifications.models import NotificationSource

        service = self._service()

        async def broken(*a, **k):
            raise ValueError("bad card")

        monkeypatch.setattr(service, "_build_view", broken)
        user = SimpleNamespace(id=5, send=AsyncMock())
        result = run(service.send_dm(user, content=self._content(),
                                     source=NotificationSource.service("test")))
        assert not result.delivered
        user.send.assert_not_awaited()


# ---------------------------------------------------------------------------
# Small components
# ---------------------------------------------------------------------------

class TestSmallComponents:
    def test_i18n_bad_format_string_does_not_raise(self, monkeypatch):
        from utils.i18n import i18n

        # I18n is a singleton: patch its dicts in place and restore afterwards.
        monkeypatch.setitem(i18n._translations, "en-US", {"a": {"b": "broken {0} and {"}})
        assert i18n.get("a.b", locale="en-US", name="x") == "broken {0} and {"

    def test_i18n_reload_keeps_old_text_when_files_are_malformed(self, tmp_path, monkeypatch):
        from utils.i18n import I18n, i18n

        before = dict(i18n._translations)
        assert before
        # Simulate every file failing to parse
        import json as _json
        monkeypatch.setattr(_json, "load", lambda f: (_ for _ in ()).throw(ValueError("bad")))
        try:
            i18n.reload_translations()
            assert i18n._translations  # old text kept
        finally:
            monkeypatch.undo()
            i18n._translations.clear()
            i18n._translations.update(before)
            I18n._loaded = True

    def test_guild_language_is_not_cached_while_the_database_is_down(self):
        from utils import guild_language as gl

        gl.invalidate_guild_language()
        bot = SimpleNamespace(db=None)
        assert run(gl.get_language_setting(bot, 4242)) == gl.AUTO
        assert 4242 not in gl._cache

    def test_user_preferences_skip_malformed_rows(self):
        from utils import user_preferences as up

        db = SimpleNamespace(get_users_with_preferences=AsyncMock(return_value=[
            (1, {"language": "fr"}), ("garbage",), (None, {"language": "de"}), (3, {"icon_theme": "light"})]))
        bot = SimpleNamespace(db=db)
        assert run(up.load_all(bot)) == 2
        up._cache.clear()

    def test_heartbeat_restarts_after_its_task_died(self):
        from services.heartbeat import HeartbeatClient

        async def go():
            hb = HeartbeatClient("svc", url="https://x", token="t")
            dead = asyncio.get_running_loop().create_future()
            task = asyncio.ensure_future(asyncio.sleep(0))
            await task
            hb._task = task          # finished task: start() must not treat it as running
            hb.start()
            assert hb._task is not task
            hb._task.cancel()

        run(go())

    def test_feeds_waits_for_redis_instead_of_staying_disabled(self):
        from services.feeds_client import FeedsClient

        async def go():
            bot = SimpleNamespace(redis=None)
            client = FeedsClient(bot)
            await client.start(AsyncMock())
            assert client._started is False and client._waiter_task is not None
            await client.stop()

        run(go())

    def test_gateway_logger_keeps_entries_in_memory_when_redis_push_fails(self):
        from gateway.logger import GatewayLogger

        redis = SimpleNamespace(rpush=AsyncMock(side_effect=ConnectionError("down")))
        config = SimpleNamespace(log_buffer_key="k", log_flush_interval=1, log_flush_batch=10, cost_table={})
        gl = GatewayLogger.__new__(GatewayLogger)
        gl._redis, gl._config, gl._stats, gl._tech_logger = redis, config, None, None
        from collections import deque
        gl._local = deque()
        gl._webhook_tasks = set()
        spec = SimpleNamespace(
            correlation_id="c", call_type="t", provider="p", operation="o", model="m",
            metadata={}, quota=[])
        run(gl.record(spec, success=True, latency_ms=1, attempts=1))
        assert len(gl._local) == 1

    def test_blacklist_check_fails_open_when_the_lookup_breaks(self, monkeypatch):
        from cogs import blacklist_check as bc

        processed = []

        async def original(message):
            processed.append(message)

        bot = SimpleNamespace(process_commands=original)

        async def get_prefix(message):
            return ["!"]

        bot.get_prefix = get_prefix
        cog = bc.BlacklistCheck(bot)

        async def boom(*a, **k):
            raise asyncpg.InterfaceError("closed")

        monkeypatch.setattr(bc.global_sanctions, "is_suspended", boom)
        message = SimpleNamespace(author=SimpleNamespace(bot=False, id=1), content="!ping", guild=None)
        run(bot.process_commands(message))
        assert processed == [message]

    def test_reminder_poison_row_is_marked_failed_after_five_attempts(self):
        from cogs.reminder import Reminder, MAX_SEND_ATTEMPTS

        marks = []
        db = SimpleNamespace(mark_reminder_sent=AsyncMock(side_effect=lambda i, failed=False: marks.append((i, failed))))
        cog = Reminder.__new__(Reminder)
        cog.bot = SimpleNamespace(db=db)
        cog._failures = {}
        cog.send_reminder = AsyncMock(side_effect=RuntimeError("bad row"))

        async def go():
            for _ in range(MAX_SEND_ATTEMPTS):
                await cog._send_guarded({"id": 9})

        run(go())
        assert marks == [(9, True)]

    def test_minibot_lifecycle_log_failure_does_not_break_supervision(self):
        from minibots.runtime import MiniBotRuntime

        rt = MiniBotRuntime.__new__(MiniBotRuntime)
        rt.resources = SimpleNamespace(tech_logger=None)
        rt.app_logger = lambda state: (_ for _ in ()).throw(RuntimeError("webhook gone"))
        run(rt._lifecycle(SimpleNamespace(spec=SimpleNamespace(id="x")), "crashed", []))
