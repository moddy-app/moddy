"""The daily pass: photographs, ageing and purging.

Counters answer "how many times"; they cannot answer "how many are there".
That second question — servers, members, adoption of a module, distinct
people — is a *gauge*, and a gauge is photographed rather than incremented.
This module takes those photographs, then does the housekeeping that keeps
the database from growing without end:

1. write today's snapshots (and rewrite yesterday's, so the last hours of a
   day are never missing from its final value),
2. read the distinct-people HyperLogLogs and store their cardinality — a
   number, never a list of ids,
3. collapse hourly buckets older than 90 days into daily ones,
4. drop the partitions past their retention,
5. create the partitions the coming days will need.

Everything here is idempotent: snapshots are replaced rather than added, and
the rollup deletes the hourly rows in the same transaction that writes the
daily ones. Running it twice changes nothing, which is what makes it safe to
run on every boot as well as on a schedule.

See docs/STATS.md.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from stats import registry
from stats.service import hll_key

logger = logging.getLogger("moddy.stats.rollup")

#: The pass is cheap and idempotent, so it runs often enough that the current
#: day is never more than a few hours stale on the dashboard.
ROLLUP_INTERVAL = 6 * 3600

#: Hourly buckets older than this are collapsed into daily ones.
HOURLY_RETENTION_DAYS = 90
#: Whole months of counters kept before the partition is dropped.
COUNTER_RETENTION_MONTHS = 12
#: The raw window.
RAW_RETENTION_DAYS = 14


class StatsRollup:
    """Owns the periodic maintenance. Lives on ``bot.stats_rollup``."""

    def __init__(self, bot, *, interval: float = ROLLUP_INTERVAL):
        self.bot = bot
        self.interval = interval
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="stats-rollup")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.run()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("stats rollup failed: %s", exc)
            try:
                await asyncio.sleep(self.interval)
            except asyncio.CancelledError:
                break

    # ------------------------------------------------------------------ #

    async def run(self) -> None:
        """One full pass. Safe to call at any time, as often as you like."""
        db = getattr(self.bot, "db", None)
        if not db or not getattr(db, "pool", None):
            return

        await db.ensure_stats_partitions()

        today = datetime.now(timezone.utc).date()
        yesterday = today - timedelta(days=1)

        await self.snapshot_globals(today)
        await self.snapshot_guild_gauges(today)
        for day in (today, yesterday):
            await self.snapshot_uniques(day)

        moved = await db.rollup_hourly_to_daily(HOURLY_RETENTION_DAYS)
        if moved:
            logger.info("stats: rolled %d hourly rows into daily buckets", moved)
        await db.drop_old_partitions(
            counter_months=COUNTER_RETENTION_MONTHS,
            event_days=RAW_RETENTION_DAYS,
        )

    # ------------------------------------------------------------------ #
    # Gauges
    # ------------------------------------------------------------------ #

    async def snapshot_globals(self, day: date) -> None:
        """The bot's own curve: servers, reachable members, adoption."""
        db = self.bot.db
        guilds = list(getattr(self.bot, "guilds", []) or [])
        rows: List[Tuple[str, str, int, date, Dict[str, str], float]] = [
            ("bot.guilds", "global", 0, day, {}, float(len(guilds))),
            ("bot.members", "global", 0, day, {},
             float(sum((g.member_count or 0) for g in guilds))),
        ]

        async with db.pool.acquire() as conn:
            rows.append(("bot.known_users", "global", 0, day, {},
                         float(await conn.fetchval("SELECT COUNT(*) FROM users") or 0)))
            rows.append(("bot.premium_users", "global", 0, day, {}, float(
                await conn.fetchval("SELECT COUNT(*) FROM users WHERE attributes ? 'PREMIUM'") or 0
            )))
            rows.append(("bot.premium_guilds", "global", 0, day, {}, float(
                await conn.fetchval("SELECT COUNT(*) FROM guilds WHERE attributes ? 'PREMIUM'") or 0
            )))
            # Module adoption, straight from the stored configuration: one
            # row per module per day, which is the whole adoption history for
            # the price of a few dozen rows.
            adoption = await conn.fetch(
                """
                SELECT m.key AS module, COUNT(*) AS enabled
                FROM guilds g,
                     LATERAL jsonb_each(COALESCE(g.data->'modules', '{}'::jsonb)) AS m(key, value)
                WHERE m.value->>'enabled' = 'true'
                GROUP BY m.key
                """
            )
        for record in adoption:
            rows.append(("module.enabled", "global", 0, day,
                         {"module": record["module"]}, float(record["enabled"])))

        await db.write_snapshots(rows)

    async def snapshot_guild_gauges(self, day: date) -> None:
        """Per-server member counts — but only when the number moved.

        Writing every server every day would cost a row per server per day
        forever, to record mostly that nothing happened. Storing only the
        changes turns a flat server into zero rows; a curve is then read by
        carrying the last known value forward, which the dashboard does when
        it plots it.
        """
        db = self.bot.db
        guilds = list(getattr(self.bot, "guilds", []) or [])
        if not guilds:
            return
        previous = await db.latest_snapshot_values("guild.members", scope="guild")
        rows = [
            ("guild.members", "guild", g.id, day, {}, float(g.member_count or 0))
            for g in guilds
            if previous.get(g.id) != float(g.member_count or 0)
        ]
        if rows:
            await db.write_snapshots(rows)

    # ------------------------------------------------------------------ #
    # Uniques
    # ------------------------------------------------------------------ #

    async def snapshot_uniques(self, day: date) -> None:
        """Turn each HyperLogLog into a single number in ``stats_snapshots``."""
        redis = getattr(self.bot, "redis", None)
        if redis is None:
            return
        db = self.bot.db
        stamp = day.strftime("%Y-%m-%d")
        rows: List[Tuple[str, str, int, date, Dict[str, str], float]] = []
        for metric in registry.uniques():
            pattern = hll_key(metric.key, metric.scope, "*", stamp)
            try:
                async for key in redis.scan_iter(match=pattern, count=500):
                    scope_id = _scope_id_from_key(key)
                    if scope_id is None:
                        continue
                    count = await redis.pfcount(key)
                    rows.append((metric.key, metric.scope, scope_id, day, {}, float(count)))
            except Exception as exc:
                logger.debug("stats: unique scan failed for %s: %s", metric.key, exc)
        if rows:
            await db.write_snapshots(rows)


def _scope_id_from_key(key: str) -> Optional[int]:
    """``stats:hll:<metric>:<scope>:<scope_id>:<day>`` → ``scope_id``."""
    parts = key.split(":")
    if len(parts) < 2:
        return None
    try:
        return int(parts[-2])
    except ValueError:
        return None
