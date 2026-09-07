"""Statistics: the registry, the aggregation, the rollup.

The properties tested here are the ones the whole design rests on. If any of
them breaks, the system stops being cheap — silently, and only visible on the
Railway bill months later:

* N events produce **one** row, not N,
* a dimension nobody declared can never reach the database,
* a failed flush loses nothing and double-counts nothing,
* recording a statistic cannot raise, whatever the caller passes.
"""

import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db.repositories.stats import StatsRepository  # noqa: E402
from stats import registry  # noqa: E402
from stats.rollup import StatsRollup, _scope_id_from_key  # noqa: E402
from stats.service import StatsService, hll_key  # noqa: E402


# --------------------------------------------------------------------------- #
# Doubles
# --------------------------------------------------------------------------- #

class FakeDB:
    """Just enough of ModdyDatabase for the service to think it can write."""

    def __init__(self, *, fail: bool = False):
        self.pool = object()
        self.fail = fail
        self.counter_rows = []
        self.raw_rows = []
        self.snapshots = []

    async def upsert_counters(self, rows):
        if self.fail:
            raise RuntimeError("postgres is having a day")
        self.counter_rows.extend(rows)

    async def insert_raw_events(self, rows):
        self.raw_rows.extend(rows)

    async def write_snapshots(self, rows):
        self.snapshots.extend(rows)

    async def latest_snapshot_values(self, metric, scope="guild"):
        return {}

    async def ensure_stats_partitions(self, **kwargs):
        return None

    async def rollup_hourly_to_daily(self, older_than_days=90):
        return 0

    async def drop_old_partitions(self, **kwargs):
        return []


class FakeRedis:
    def __init__(self):
        self.hll = {}
        self.expired = []

    async def pfadd(self, key, *ids):
        self.hll.setdefault(key, set()).update(ids)

    async def expire(self, key, ttl):
        self.expired.append((key, ttl))

    async def pfcount(self, key):
        return len(self.hll.get(key, ()))

    async def scan_iter(self, match=None, count=None):
        import fnmatch
        for key in list(self.hll):
            if match is None or fnmatch.fnmatch(key, match):
                yield key


class FakeGuild:
    def __init__(self, guild_id, member_count):
        self.id = guild_id
        self.member_count = member_count


class FakeBot:
    def __init__(self, db=None, redis=None, guilds=()):
        self.db = db
        self.redis = redis
        self.guilds = list(guilds)


# --------------------------------------------------------------------------- #
# The registry
# --------------------------------------------------------------------------- #

class TestRegistry:
    def test_every_metric_is_uniquely_addressable(self):
        pairs = [(m.key, m.scope) for m in registry.all_metrics()]
        assert len(pairs) == len(set(pairs))

    def test_an_undeclared_metric_is_not_resolvable(self):
        with pytest.raises(registry.UnknownMetric):
            registry.get("something.invented")

    def test_per_server_metrics_are_daily_and_global_ones_hourly(self):
        # The single biggest cost lever: an hourly bucket per server is 24×
        # the rows for a precision nobody reads at that scope.
        assert registry.get("command.used").resolution == registry.RESOLUTION_DAY
        assert registry.get("guild.join").resolution == registry.RESOLUTION_HOUR

    def test_an_undeclared_dimension_cannot_reach_the_database(self):
        metric = registry.get("command.used")
        clean = registry.normalise_dims(metric, {"command": "config", "user_id": 42})
        assert clean == {"command": "config"}
        assert registry.unknown_dims(metric, {"user_id": 42}) == ("user_id",)

    def test_a_long_dimension_value_is_truncated(self):
        metric = registry.get("command.used")
        clean = registry.normalise_dims(metric, {"command": "x" * 500})
        assert len(clean["command"]) == registry.MAX_DIM_VALUE_LEN

    def test_booleans_are_stored_readably(self):
        metric = registry.get("ai.calls")
        clean = registry.normalise_dims(
            metric, {"provider": "openai", "model": "m", "call_type": "c", "ok": False}
        )
        assert clean["ok"] == "false"

    def test_the_dimension_hash_ignores_key_order(self):
        assert registry.dims_hash({"a": "1", "b": "2"}) == registry.dims_hash({"b": "2", "a": "1"})

    def test_different_dimensions_hash_differently(self):
        assert registry.dims_hash({"a": "1"}) != registry.dims_hash({"a": "2"})

    def test_buckets_are_floored_in_utc(self):
        moment = datetime(2026, 9, 7, 13, 45, 12, tzinfo=timezone.utc)
        assert registry.floor_bucket("hour", moment).hour == 13
        assert registry.floor_bucket("hour", moment).minute == 0
        assert registry.floor_bucket("day", moment).hour == 0


# --------------------------------------------------------------------------- #
# Recording
# --------------------------------------------------------------------------- #

class TestAggregation:
    def test_many_events_become_one_row(self):
        """The whole point of the system, in one assertion."""
        service = StatsService(FakeBot())
        for _ in range(10_000):
            service.incr("command.used", guild_id=1,
                         dims={"command": "config", "kind": "slash"})
        assert len(service._pending) == 1
        assert list(service._pending.values())[0][1] == 10_000

    def test_different_dimensions_stay_apart(self):
        service = StatsService(FakeBot())
        service.incr("command.used", guild_id=1, dims={"command": "config"})
        service.incr("command.used", guild_id=1, dims={"command": "ping"})
        assert len(service._pending) == 2

    def test_different_servers_stay_apart(self):
        service = StatsService(FakeBot())
        service.incr("command.used", guild_id=1, dims={"command": "config"})
        service.incr("command.used", guild_id=2, dims={"command": "config"})
        assert len(service._pending) == 2

    def test_an_undeclared_dimension_is_dropped_not_stored(self):
        service = StatsService(FakeBot())
        service.incr("command.used", guild_id=1,
                     dims={"command": "config", "user_id": 12345})
        (dims, _), = service._pending.values()
        assert "user_id" not in dims

    def test_an_unknown_metric_is_ignored(self):
        service = StatsService(FakeBot())
        service.incr("not.declared", guild_id=1)
        assert service._pending == {}

    def test_a_server_metric_without_a_server_is_ignored(self):
        service = StatsService(FakeBot())
        service.incr("command.used", dims={"command": "config"})
        assert service._pending == {}

    def test_the_scope_is_inferred_when_a_key_spans_several(self):
        service = StatsService(FakeBot())
        service.incr("case.created", dims={"type": "global", "action": "ban"})
        service.incr("case.created", guild_id=7, dims={"type": "guild", "action": "ban"})
        scopes = sorted(key[1] for key in service._pending)
        assert scopes == ["global", "guild"]

    def test_recording_never_raises(self):
        """A statistic must not be able to break the feature it measures."""
        service = StatsService(FakeBot())
        service.incr("command.used", guild_id=1, dims={"command": object()})
        service.incr(None, guild_id=1)                      # type: ignore[arg-type]
        service.incr("command.used", guild_id=1, when="not a date")  # type: ignore[arg-type]
        service.observe_unique("user.active", "not an id", guild_id=1)  # type: ignore[arg-type]

    def test_the_buffer_is_bounded(self):
        service = StatsService(FakeBot())
        from stats import service as service_module
        for i in range(service_module.MAX_PENDING_KEYS + 50):
            service.incr("command.used", guild_id=i, dims={"command": "config"})
        assert len(service._pending) == service_module.MAX_PENDING_KEYS


class TestFlush:
    async def test_a_flush_writes_the_aggregate_and_clears_it(self):
        db = FakeDB()
        service = StatsService(FakeBot(db=db))
        for _ in range(5):
            service.incr("command.used", guild_id=1, dims={"command": "config"})
        await service.flush()
        assert len(db.counter_rows) == 1
        assert db.counter_rows[0][-1] == 5
        assert service._pending == {}

    async def test_a_failed_flush_keeps_the_numbers(self):
        db = FakeDB(fail=True)
        service = StatsService(FakeBot(db=db))
        service.incr("command.used", guild_id=1, dims={"command": "config"})
        await service.flush()
        assert sum(v for _, v in service._pending.values()) == 1

        # And the retry adds up rather than losing the first attempt.
        service.incr("command.used", guild_id=1, dims={"command": "config"})
        db.fail = False
        await service.flush()
        assert db.counter_rows[0][-1] == 2

    async def test_nothing_is_written_without_a_database(self):
        service = StatsService(FakeBot(db=None))
        service.incr("command.used", guild_id=1, dims={"command": "config"})
        await service.flush()
        assert len(service._pending) == 1  # kept for when the pool comes back

    async def test_stopping_flushes_what_is_pending(self):
        db = FakeDB()
        service = StatsService(FakeBot(db=db), flush_interval=3600)
        service.start()
        service.incr("command.used", guild_id=1, dims={"command": "config"})
        await service.stop()
        assert len(db.counter_rows) == 1

    async def test_distinct_people_become_a_hyperloglog_not_a_list(self):
        redis = FakeRedis()
        service = StatsService(FakeBot(db=FakeDB(), redis=redis))
        for user_id in range(50):
            service.observe_unique("user.active", user_id, guild_id=1)
        await service.flush()
        key = hll_key("user.active", "guild", 1,
                      datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        assert await redis.pfcount(key) == 50
        assert service._uniques == {}
        assert redis.expired  # the key is not kept forever


# --------------------------------------------------------------------------- #
# The daily pass
# --------------------------------------------------------------------------- #

class TestRollup:
    async def test_the_global_snapshot_photographs_the_bot(self):
        db = FakeDB()

        class Conn:
            async def fetchval(self, *args):
                return 3

            async def fetch(self, *args):
                return [{"module": "tickets", "enabled": 12}]

        class Pool:
            def acquire(self):
                class Ctx:
                    async def __aenter__(self):
                        return Conn()

                    async def __aexit__(self, *exc):
                        return False
                return Ctx()

        db.pool = Pool()
        bot = FakeBot(db=db, guilds=[FakeGuild(1, 100), FakeGuild(2, 250)])
        await StatsRollup(bot).snapshot_globals(date(2026, 9, 7))
        values = {row[0]: row[5] for row in db.snapshots}
        assert values["bot.guilds"] == 2
        assert values["bot.members"] == 350
        assert values["module.enabled"] == 12

    async def test_a_server_whose_size_did_not_move_stores_nothing(self):
        db = FakeDB()

        async def latest(metric, scope="guild"):
            return {1: 100.0}

        db.latest_snapshot_values = latest
        bot = FakeBot(db=db, guilds=[FakeGuild(1, 100), FakeGuild(2, 250)])
        await StatsRollup(bot).snapshot_guild_gauges(date(2026, 9, 7))
        assert [row[2] for row in db.snapshots] == [2]

    async def test_uniques_are_stored_as_a_number(self):
        redis = FakeRedis()
        db = FakeDB()
        day = date(2026, 9, 7)
        stamp = day.strftime("%Y-%m-%d")
        await redis.pfadd(hll_key("user.active", "guild", 5, stamp), 1, 2, 3)
        await StatsRollup(FakeBot(db=db, redis=redis)).snapshot_uniques(day)
        assert db.snapshots == [("user.active", "guild", 5, day, {}, 3.0)]

    def test_a_hyperloglog_key_names_its_subject(self):
        assert _scope_id_from_key("stats:hll:user.active:guild:99:2026-09-07") == 99
        assert _scope_id_from_key("stats:hll:user.active:global:0:2026-09-07") == 0


# --------------------------------------------------------------------------- #
# Retention plumbing
# --------------------------------------------------------------------------- #

class TestPartitions:
    def test_a_partition_names_the_window_it_holds(self):
        window = StatsRepository._partition_window("stats_counters_202609")
        assert window == (datetime(2026, 9, 1, tzinfo=timezone.utc), "counter")
        window = StatsRepository._partition_window("stats_events_20260907")
        assert window == (datetime(2026, 9, 7, tzinfo=timezone.utc), "event")

    def test_the_default_partition_is_never_dropped(self):
        # It is the safety net for a missed maintenance pass: dropping it
        # would delete whatever landed there.
        assert StatsRepository._partition_window("stats_counters_default") is None
        assert StatsRepository._partition_window("stats_events_default") is None
        assert StatsRepository._partition_window("api_calls") is None
