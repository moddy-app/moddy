"""Recording statistics — ``bot.stats``.

The whole system rests on one move: **aggregate before writing**.

.. code-block:: python

    bot.stats.incr("command.used", guild_id=guild.id, dims={"command": "config"})

That call adds one to a dictionary in memory. Sixty seconds later a single
``INSERT … ON CONFLICT DO UPDATE`` carries the whole minute to Postgres, one
row per ``(metric, scope, scope_id, bucket, dims)``. Ten thousand commands
in a day on one server cost one row, not ten thousand — which is the entire
reason this system can afford to measure everything.

Why in memory rather than in Redis
----------------------------------
Because the alternative buys nothing here and costs a round trip on the hot
path. ``incr()`` is **synchronous**: no ``await``, no I/O, no exception —
recording a statistic can never slow down or break the feature it measures,
which is not a property an ``await redis.hincrby()`` can offer. Nor is the
process boundary a problem: the flush is an *addition*, so two processes (or
a second one started mid-day) simply add their own totals to the same row.

What is lost is up to one flush interval of counters if the container is
killed without warning — an acceptable price for a statistic, and one
``stop()`` pays back on every graceful shutdown.

Redis is still used for one thing counting cannot do in a dictionary:
distinct-people counts, through a HyperLogLog that survives a restart and
never stores a single id (see :meth:`StatsService.observe_unique`).

See docs/STATS.md.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from stats import registry
from stats.registry import (
    KIND_UNIQUE,
    SCOPE_GLOBAL,
    SCOPE_GUILD,
    SCOPE_USER,
    Metric,
    UnknownMetric,
)

logger = logging.getLogger("moddy.stats")

#: How often the aggregate is carried to Postgres.
FLUSH_INTERVAL = 60.0

#: Hard ceilings. They exist so that a bug in a caller — a dimension that
#: turns out to be unbounded, a burst nobody predicted — degrades into lost
#: statistics instead of an out-of-memory kill.
MAX_PENDING_KEYS = 50_000
MAX_RAW_EVENTS = 10_000
MAX_UNIQUE_IDS = 100_000

#: HyperLogLogs live a few days: long enough for the rollup to read yesterday
#: after midnight, short enough that they never accumulate.
HLL_TTL = 3 * 24 * 3600

# (metric, scope, scope_id, bucket, dims_hash)
_CounterKey = Tuple[str, str, int, datetime, bytes]
# (metric, scope, scope_id, day)
_UniqueKey = Tuple[str, str, int, str]


class StatsService:
    """Counts things. Lives on ``bot.stats``."""

    def __init__(self, bot, *, flush_interval: float = FLUSH_INTERVAL):
        self.bot = bot
        self.flush_interval = flush_interval
        self._pending: Dict[_CounterKey, Tuple[Dict[str, str], int]] = {}
        self._raw: List[Tuple[str, Optional[int], Optional[int], Dict[str, Any], datetime]] = []
        self._uniques: Dict[_UniqueKey, Set[int]] = {}
        self._task: Optional[asyncio.Task] = None
        self._warned: Set[str] = set()

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._flush_loop(), name="stats-flush")

    async def stop(self) -> None:
        """Cancel the loop and write what is still pending."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self.flush()

    # ------------------------------------------------------------------ #
    # Recording
    # ------------------------------------------------------------------ #

    def incr(
        self,
        key: str,
        *,
        guild_id: Optional[int] = None,
        user_id: Optional[int] = None,
        scope: Optional[str] = None,
        value: int = 1,
        dims: Optional[Dict[str, Any]] = None,
        when: Optional[datetime] = None,
    ) -> None:
        """Add ``value`` to a counter. Never raises, never awaits.

        The scope comes from the metric's declaration, not from the call:
        passing a ``guild_id`` to a global metric counts it globally, which
        is what the registry said the metric means.
        """
        try:
            metric = self._resolve(key, scope, guild_id=guild_id, user_id=user_id)
            if metric is None:
                return
            scope_id = self._scope_id(metric, guild_id, user_id)
            if scope_id is None:
                return
            clean = registry.normalise_dims(metric, dims)
            self._warn_unknown_dims(metric, dims)
            bucket = registry.floor_bucket(metric.resolution, when)
            ckey: _CounterKey = (
                metric.key, metric.scope, scope_id, bucket, registry.dims_hash(clean),
            )
            current = self._pending.get(ckey)
            if current is None:
                if len(self._pending) >= MAX_PENDING_KEYS:
                    self._warn_once(
                        "pending-full",
                        "stats buffer full (%d keys) — dropping %s until the next flush"
                        % (MAX_PENDING_KEYS, metric.key),
                    )
                    return
                self._pending[ckey] = (clean, value)
            else:
                self._pending[ckey] = (current[0], current[1] + value)

            if metric.raw and len(self._raw) < MAX_RAW_EVENTS:
                self._raw.append((
                    metric.key, guild_id, user_id, dict(clean),
                    when or datetime.now(timezone.utc),
                ))
        except Exception as exc:  # a statistic must never break its caller
            logger.debug("stats.incr(%s) failed: %s", key, exc)

    def observe_unique(
        self,
        key: str,
        subject_id: int,
        *,
        guild_id: Optional[int] = None,
        when: Optional[datetime] = None,
    ) -> None:
        """Record that ``subject_id`` was seen today, for a distinct count.

        The ids are buffered only until the next flush, then folded into a
        Redis HyperLogLog — roughly 12 KB per key for a 0.8 % error, and no
        list of who was there. The daily rollup reads the cardinality and
        stores a single number.
        """
        try:
            metric = self._resolve(key, SCOPE_GUILD if guild_id else SCOPE_GLOBAL)
            if metric is None or metric.kind != KIND_UNIQUE:
                return
            scope_id = self._scope_id(metric, guild_id, None)
            if scope_id is None:
                return
            day = registry.floor_bucket(registry.RESOLUTION_DAY, when).strftime("%Y-%m-%d")
            ukey: _UniqueKey = (metric.key, metric.scope, scope_id, day)
            bucket = self._uniques.setdefault(ukey, set())
            if len(bucket) < MAX_UNIQUE_IDS:
                bucket.add(subject_id)
        except Exception as exc:
            logger.debug("stats.observe_unique(%s) failed: %s", key, exc)

    # ------------------------------------------------------------------ #
    # Flushing
    # ------------------------------------------------------------------ #

    async def _flush_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.flush_interval)
                await self.flush()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("stats flush loop error: %s", exc)

    async def flush(self) -> None:
        """Carry the aggregate to Postgres and the uniques to Redis.

        On a database failure the batch is merged back into the pending
        aggregate rather than dropped: the next flush writes the sum, and the
        additive upsert means nothing is double-counted if the failed write
        had in fact committed.
        """
        await self._flush_counters()
        await self._flush_raw()
        await self._flush_uniques()

    async def _flush_counters(self) -> None:
        if not self._pending:
            return
        db = getattr(self.bot, "db", None)
        if not db or not getattr(db, "pool", None):
            return
        batch, self._pending = self._pending, {}
        rows = [
            (metric, scope, scope_id, bucket, dims, dims_hash, value)
            for (metric, scope, scope_id, bucket, dims_hash), (dims, value) in batch.items()
        ]
        try:
            await db.upsert_counters(rows)
            logger.debug("stats: flushed %d counter rows", len(rows))
        except Exception as exc:
            logger.warning("stats: counter flush failed (%d rows): %s", len(rows), exc)
            self._merge_back(batch)

    def _merge_back(self, batch: Dict[_CounterKey, Tuple[Dict[str, str], int]]) -> None:
        for ckey, (dims, value) in batch.items():
            current = self._pending.get(ckey)
            if current is None:
                if len(self._pending) >= MAX_PENDING_KEYS:
                    return
                self._pending[ckey] = (dims, value)
            else:
                self._pending[ckey] = (current[0], current[1] + value)

    async def _flush_raw(self) -> None:
        if not self._raw:
            return
        db = getattr(self.bot, "db", None)
        if not db or not getattr(db, "pool", None):
            self._raw.clear()  # the raw window is expendable by design
            return
        batch, self._raw = self._raw, []
        try:
            await db.insert_raw_events(batch)
        except Exception as exc:
            logger.warning("stats: raw event flush failed (%d rows): %s", len(batch), exc)

    async def _flush_uniques(self) -> None:
        if not self._uniques:
            return
        redis = getattr(self.bot, "redis", None)
        if redis is None:
            self._uniques.clear()
            return
        batch, self._uniques = self._uniques, {}
        for (metric, scope, scope_id, day), ids in batch.items():
            key = hll_key(metric, scope, scope_id, day)
            try:
                await redis.pfadd(key, *ids)
                await redis.expire(key, HLL_TTL)
            except Exception as exc:
                logger.debug("stats: pfadd %s failed: %s", key, exc)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _resolve(self, key: str, scope: Optional[str] = None, *,
                 guild_id: Optional[int] = None,
                 user_id: Optional[int] = None) -> Optional[Metric]:
        """Find the metric, inferring the scope when the key spans several.

        A handful of keys are measured at more than one level (a case is
        recorded for a server, or by the Moddy team for the platform). When
        the caller did not say which, the arguments do: an id means that
        level, no id means global.
        """
        try:
            return registry.get(key, scope)
        except UnknownMetric:
            if scope is None:
                inferred = (
                    SCOPE_GUILD if guild_id
                    else SCOPE_USER if user_id
                    else SCOPE_GLOBAL
                )
                try:
                    return registry.get(key, inferred)
                except UnknownMetric:
                    pass
            self._warn_once(
                f"unknown-{key}-{scope}",
                "stats: %s is not declared in stats/registry.py — ignored" % key,
            )
            return None

    @staticmethod
    def _scope_id(metric: Metric, guild_id: Optional[int],
                  user_id: Optional[int]) -> Optional[int]:
        if metric.scope == SCOPE_GLOBAL:
            return 0
        if metric.scope == SCOPE_GUILD:
            return guild_id if guild_id else None
        if metric.scope == SCOPE_USER:
            return user_id if user_id else None
        return None

    def _warn_unknown_dims(self, metric: Metric, dims: Optional[Dict]) -> None:
        extra = registry.unknown_dims(metric, dims)
        if extra:
            self._warn_once(
                f"dims-{metric.key}-{extra}",
                "stats: %s does not declare %s — dropped (see stats/registry.py)"
                % (metric.key, ", ".join(extra)),
            )

    def _warn_once(self, token: str, message: str) -> None:
        if token in self._warned:
            return
        self._warned.add(token)
        logger.warning(message)


def hll_key(metric: str, scope: str, scope_id, day: str) -> str:
    """Redis key of one day's HyperLogLog.

    ``scope_id`` may be ``"*"`` to build a scan pattern.

    ``stats:*`` is this system's own namespace — not ``moddy:*`` (the
    backend's) nor a service's, per docs/REDIS_COMMUNICATION.md §5.
    """
    return f"stats:hll:{metric}:{scope}:{scope_id}:{day}"
