"""Tests for ``automod.cache.LruTtlCache``."""

from automod.cache import MISS, LruTtlCache


class FakeClock:
    """Manually-advanced monotonic clock for deterministic TTL tests."""

    def __init__(self, now: float = 0.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, dt: float) -> None:
        self.now += dt


class TestBasics:
    def test_hit_and_miss(self):
        c = LruTtlCache(max_entries=8)
        assert c.get("a") is MISS
        c.set("a", 1)
        assert c.get("a") == 1
        assert c.hits == 1
        assert c.misses == 1

    def test_set_overwrites(self):
        c = LruTtlCache(max_entries=8)
        c.set("a", 1)
        c.set("a", 2)
        assert c.get("a") == 2
        assert len(c) == 1

    def test_contains_uses_get_semantics(self):
        c = LruTtlCache(max_entries=8)
        c.set("a", 1)
        assert "a" in c
        assert "b" not in c


class TestLruEviction:
    def test_evicts_least_recently_used(self):
        c = LruTtlCache(max_entries=2)
        c.set("a", 1)
        c.set("b", 2)
        c.set("c", 3)  # evicts "a"
        assert c.get("a") is MISS
        assert c.get("b") == 2
        assert c.get("c") == 3
        assert c.evictions == 1

    def test_get_refreshes_recency(self):
        c = LruTtlCache(max_entries=2)
        c.set("a", 1)
        c.set("b", 2)
        assert c.get("a") == 1   # "a" now most-recently-used
        c.set("c", 3)            # should evict "b", not "a"
        assert c.get("a") == 1
        assert c.get("b") is MISS

    def test_set_refreshes_recency(self):
        c = LruTtlCache(max_entries=2)
        c.set("a", 1)
        c.set("b", 2)
        c.set("a", 10)           # touch "a"
        c.set("c", 3)            # evicts "b"
        assert c.get("b") is MISS
        assert c.get("a") == 10


class TestTtl:
    def test_entry_expires_after_ttl(self):
        clock = FakeClock()
        c = LruTtlCache(max_entries=8, ttl_seconds=10, clock=clock)
        c.set("a", 1)
        clock.advance(9)
        assert c.get("a") == 1     # still fresh
        clock.advance(1)           # now exactly at TTL → expired
        assert c.get("a") is MISS
        assert c.expirations == 1
        assert len(c) == 0

    def test_ttl_none_never_expires(self):
        clock = FakeClock()
        c = LruTtlCache(max_entries=8, ttl_seconds=None, clock=clock)
        c.set("a", 1)
        clock.advance(10_000)
        assert c.get("a") == 1


class TestDisabled:
    def test_zero_max_entries_is_a_noop(self):
        c = LruTtlCache(max_entries=0)
        c.set("a", 1)
        assert c.get("a") is MISS
        assert len(c) == 0
        assert c.misses == 1


class TestStats:
    def test_clear_keeps_counters(self):
        c = LruTtlCache(max_entries=8)
        c.set("a", 1)
        c.get("a")
        c.clear()
        assert len(c) == 0
        assert c.hits == 1

    def test_hit_rate_and_snapshot(self):
        c = LruTtlCache(max_entries=8, ttl_seconds=30)
        c.get("x")          # miss
        c.set("x", 1)
        c.get("x")          # hit
        c.get("x")          # hit
        assert c.hit_rate == 2 / 3
        snap = c.stats()
        assert snap["hits"] == 2
        assert snap["misses"] == 1
        assert snap["size"] == 1
        assert snap["max_entries"] == 8
        assert snap["ttl_seconds"] == 30
        assert snap["hit_rate"] == round(2 / 3, 4)
