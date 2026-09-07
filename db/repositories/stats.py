"""Statistics storage — writes in aggregate, reads for the dashboard.

Everything here follows one rule: **the number of rows written must not grow
with Discord traffic**. Counters arrive already summed by
:class:`stats.service.StatsService` and land as a single upsert per
``(metric, scope, scope_id, bucket, dims)``, so a server that runs ten
thousand commands in a day costs one row, not ten thousand.

The two tables that do keep one row per occurrence — ``guild_events`` and
``guild_installs`` — hold events that happen a few thousand times a *month*,
and they earn it: they are what the growth curve, the retention cohorts and
the acquisition report are computed from.

See docs/STATS.md.
"""

import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger('moddy.database')

#: Partitions are named after the window they hold, so the maintenance task
#: can work out what to drop from the name alone.
_COUNTER_PARTITION_FMT = "stats_counters_%Y%m"
_EVENT_PARTITION_FMT = "stats_events_%Y%m%d"


def _month_start(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )


def _next_month(moment: datetime) -> datetime:
    start = _month_start(moment)
    return _month_start(start + timedelta(days=32))


class StatsRepository:
    """``stats_counters``, ``stats_snapshots``, ``guild_events``,
    ``guild_installs`` and ``stats_events``."""

    # ------------------------------------------------------------------ #
    # Counters
    # ------------------------------------------------------------------ #

    async def upsert_counters(
        self,
        rows: Sequence[Tuple[str, str, int, datetime, Dict[str, str], bytes, int]],
    ) -> None:
        """Add pre-aggregated counter values.

        ``rows`` are ``(metric, scope, scope_id, bucket, dims, dims_hash,
        value)``. The write is an addition, not a replacement, which is what
        makes a flush safe to replay: the same batch applied twice after a
        failed commit adds the same numbers once per successful commit, and a
        concurrent flush from another process interleaves without locking.
        """
        if not rows:
            return
        async with self.pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO stats_counters
                    (metric, scope, scope_id, bucket, dims, dims_hash, value)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)
                ON CONFLICT (metric, scope, scope_id, bucket, dims_hash)
                DO UPDATE SET value = stats_counters.value + EXCLUDED.value
                """,
                [
                    (metric, scope, scope_id, bucket, json.dumps(dims, sort_keys=True),
                     dims_hash, value)
                    for metric, scope, scope_id, bucket, dims, dims_hash, value in rows
                ],
            )

    async def get_counters(
        self,
        metric: str,
        *,
        scope: str = "guild",
        scope_id: int = 0,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """Daily series for one metric — what a dashboard chart reads."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT (bucket AT TIME ZONE 'UTC')::date AS day,
                       dims,
                       SUM(value)::bigint AS value
                FROM stats_counters
                WHERE metric = $1 AND scope = $2 AND scope_id = $3
                  AND ($4::timestamptz IS NULL OR bucket >= $4)
                  AND ($5::timestamptz IS NULL OR bucket < $5)
                GROUP BY day, dims
                ORDER BY day
                """,
                metric, scope, scope_id, since, until,
            )
        return [
            {"day": r["day"], "dims": self._parse_jsonb(r["dims"]), "value": r["value"]}
            for r in rows
        ]

    # ------------------------------------------------------------------ #
    # Snapshots (gauges)
    # ------------------------------------------------------------------ #

    async def write_snapshot(
        self,
        metric: str,
        value: float,
        *,
        scope: str = "global",
        scope_id: int = 0,
        day: Optional[date] = None,
        dims: Optional[Dict[str, str]] = None,
    ) -> None:
        """Record "how many there are" for a day.

        Replaces rather than adds, so re-running the daily rollup is a no-op
        instead of a doubling.
        """
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO stats_snapshots (metric, scope, scope_id, day, dims, value)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6)
                ON CONFLICT (metric, scope, scope_id, day, dims)
                DO UPDATE SET value = EXCLUDED.value
                """,
                metric, scope, scope_id,
                day or datetime.now(timezone.utc).date(),
                json.dumps(dims or {}, sort_keys=True),
                value,
            )

    async def write_snapshots(
        self,
        rows: Sequence[Tuple[str, str, int, date, Dict[str, str], float]],
    ) -> None:
        """Bulk form of :meth:`write_snapshot` — ``(metric, scope, scope_id,
        day, dims, value)``."""
        if not rows:
            return
        async with self.pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO stats_snapshots (metric, scope, scope_id, day, dims, value)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6)
                ON CONFLICT (metric, scope, scope_id, day, dims)
                DO UPDATE SET value = EXCLUDED.value
                """,
                [
                    (metric, scope, scope_id, day,
                     json.dumps(dims or {}, sort_keys=True), value)
                    for metric, scope, scope_id, day, dims, value in rows
                ],
            )

    async def latest_snapshot_values(self, metric: str, *,
                                     scope: str = "guild") -> Dict[int, float]:
        """Last recorded value of a gauge, per subject.

        Used by the rollup to skip writing a number that has not moved: a
        server whose member count is flat costs nothing, and the dashboard
        carries the last known value forward.
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT ON (scope_id) scope_id, value
                FROM stats_snapshots
                WHERE metric = $1 AND scope = $2
                ORDER BY scope_id, day DESC
                """,
                metric, scope,
            )
        return {r["scope_id"]: float(r["value"]) for r in rows}

    async def get_snapshots(
        self,
        metric: str,
        *,
        scope: str = "global",
        scope_id: int = 0,
        days: int = 90,
    ) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT day, dims, value
                FROM stats_snapshots
                WHERE metric = $1 AND scope = $2 AND scope_id = $3
                  AND day >= (CURRENT_DATE - $4::int)
                ORDER BY day
                """,
                metric, scope, scope_id, days,
            )
        return [
            {"day": r["day"], "dims": self._parse_jsonb(r["dims"]), "value": float(r["value"])}
            for r in rows
        ]

    # ------------------------------------------------------------------ #
    # Guild lifecycle — the growth curve and the retention cohorts
    # ------------------------------------------------------------------ #

    async def record_guild_event(
        self,
        guild_id: int,
        event: str,
        *,
        member_count: Optional[int] = None,
        owner_id: Optional[int] = None,
        guild_age: Optional[timedelta] = None,
        lifetime: Optional[timedelta] = None,
        source: Optional[str] = None,
    ) -> None:
        """One arrival or departure. ``event`` is ``join`` or ``leave``."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO guild_events
                    (guild_id, event, member_count, owner_id, guild_age, lifetime, source)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                guild_id, event, member_count, owner_id, guild_age, lifetime, source,
            )

    async def last_guild_join(self, guild_id: int) -> Optional[datetime]:
        """When Moddy was last added here — used to fill ``lifetime`` on leave."""
        async with self.pool.acquire() as conn:
            return await conn.fetchval(
                """
                SELECT created_at FROM guild_events
                WHERE guild_id = $1 AND event = 'join'
                ORDER BY created_at DESC LIMIT 1
                """,
                guild_id,
            )

    async def guild_growth(self, days: int = 90) -> List[Dict[str, Any]]:
        """Joins, leaves and the net movement, per day."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT (created_at AT TIME ZONE 'UTC')::date AS day,
                       COUNT(*) FILTER (WHERE event = 'join')  AS joins,
                       COUNT(*) FILTER (WHERE event = 'leave') AS leaves
                FROM guild_events
                WHERE created_at >= now() - ($1::int * INTERVAL '1 day')
                GROUP BY day
                ORDER BY day
                """,
                days,
            )
        return [
            {
                "day": r["day"],
                "joins": r["joins"],
                "leaves": r["leaves"],
                "net": r["joins"] - r["leaves"],
            }
            for r in rows
        ]

    async def retention_cohorts(self, months: int = 12) -> List[Dict[str, Any]]:
        """Of the servers that added Moddy in month M, how many stayed.

        Retention is derived, never stored: a server is still there if its
        latest event is a join.
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH latest AS (
                    SELECT DISTINCT ON (guild_id)
                           guild_id, event, created_at
                    FROM guild_events
                    ORDER BY guild_id, created_at DESC
                ),
                first_join AS (
                    SELECT guild_id, MIN(created_at) AS joined_at
                    FROM guild_events WHERE event = 'join'
                    GROUP BY guild_id
                )
                SELECT date_trunc('month', f.joined_at)::date AS cohort,
                       COUNT(*) AS acquired,
                       COUNT(*) FILTER (WHERE l.event = 'join') AS retained
                FROM first_join f
                JOIN latest l USING (guild_id)
                WHERE f.joined_at >= date_trunc('month', now()) - ($1::int * INTERVAL '1 month')
                GROUP BY cohort
                ORDER BY cohort
                """,
                months,
            )
        return [
            {
                "cohort": r["cohort"],
                "acquired": r["acquired"],
                "retained": r["retained"],
                "rate": (r["retained"] / r["acquired"]) if r["acquired"] else 0.0,
            }
            for r in rows
        ]

    # ------------------------------------------------------------------ #
    # Acquisition — the half of the story the backend owns
    # ------------------------------------------------------------------ #

    async def confirm_install(self, guild_id: int) -> Optional[str]:
        """Mark an installation as having actually landed, return its source.

        The backend writes the row at the OAuth2 callback, where the UTM
        lives; the bot only learns the install succeeded. Returns ``None``
        when there is no row — a direct invite, or a backend that does not
        write this table yet, and neither is an error.
        """
        async with self.pool.acquire() as conn:
            return await conn.fetchval(
                """
                UPDATE guild_installs
                SET confirmed_at = COALESCE(confirmed_at, now())
                WHERE guild_id = $1
                RETURNING source
                """,
                guild_id,
            )

    async def install_source(self, guild_id: int) -> Optional[str]:
        async with self.pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT source FROM guild_installs WHERE guild_id = $1", guild_id
            )

    async def acquisition_report(self, days: int = 30) -> List[Dict[str, Any]]:
        """Per source: clicks that reached the callback, and how many landed."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT COALESCE(source, 'unknown') AS source,
                       COUNT(*) AS started,
                       COUNT(*) FILTER (WHERE confirmed_at IS NOT NULL) AS installed
                FROM guild_installs
                WHERE first_seen_at >= now() - ($1::int * INTERVAL '1 day')
                GROUP BY source
                ORDER BY installed DESC
                """,
                days,
            )
        return [
            {
                "source": r["source"],
                "started": r["started"],
                "installed": r["installed"],
                "conversion": (r["installed"] / r["started"]) if r["started"] else 0.0,
            }
            for r in rows
        ]

    # ------------------------------------------------------------------ #
    # Raw events (short-lived)
    # ------------------------------------------------------------------ #

    async def insert_raw_events(
        self,
        rows: Sequence[Tuple[str, Optional[int], Optional[int], Dict[str, Any], datetime]],
    ) -> None:
        if not rows:
            return
        async with self.pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO stats_events (event, guild_id, user_id, payload, created_at)
                VALUES ($1, $2, $3, $4::jsonb, $5)
                """,
                [
                    (event, guild_id, user_id, json.dumps(payload), created_at)
                    for event, guild_id, user_id, payload, created_at in rows
                ],
            )

    # ------------------------------------------------------------------ #
    # Partition maintenance
    # ------------------------------------------------------------------ #

    async def ensure_stats_partitions(self, *, months_ahead: int = 1,
                                      days_ahead: int = 2) -> None:
        """Create the partitions the next writes will need.

        Both tables carry a DEFAULT partition, so a missed maintenance pass
        never loses a write — it just lands somewhere that cannot be dropped
        cheaply. Attaching a range partition while conflicting rows sit in the
        default fails; that is logged and skipped rather than raised, because
        a statistic must never take the bot down.
        """
        now = datetime.now(timezone.utc)
        month = _month_start(now)
        for _ in range(months_ahead + 1):
            await self._ensure_partition(
                "stats_counters", month.strftime(_COUNTER_PARTITION_FMT),
                month, _next_month(month),
            )
            month = _next_month(month)

        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        for _ in range(days_ahead + 1):
            await self._ensure_partition(
                "stats_events", day.strftime(_EVENT_PARTITION_FMT),
                day, day + timedelta(days=1),
            )
            day += timedelta(days=1)

    async def _ensure_partition(self, parent: str, name: str,
                                start: datetime, end: datetime) -> None:
        async with self.pool.acquire() as conn:
            exists = await conn.fetchval("SELECT to_regclass($1)", f"public.{name}")
            if exists:
                return
            try:
                await conn.execute(
                    f"CREATE TABLE {name} PARTITION OF {parent} "
                    f"FOR VALUES FROM ('{start.isoformat()}') TO ('{end.isoformat()}')"
                )
                logger.info("[stats] Created partition %s", name)
            except Exception as exc:
                logger.warning("[stats] Could not create partition %s: %s", name, exc)

    async def drop_old_partitions(self, *, counter_months: int = 12,
                                  event_days: int = 14) -> List[str]:
        """Drop what is past its retention. Returns the partitions dropped.

        A DROP is instantaneous and returns the disk; the DELETE it replaces
        would lock the writers and leave the space behind as bloat.
        """
        dropped: List[str] = []
        now = datetime.now(timezone.utc)
        counter_cutoff = _month_start(now)
        for _ in range(counter_months):
            counter_cutoff = _month_start(counter_cutoff - timedelta(days=1))
        event_cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=event_days)

        async with self.pool.acquire() as conn:
            names = await conn.fetch(
                """
                SELECT c.relname AS name
                FROM pg_class c
                JOIN pg_inherits i ON i.inhrelid = c.oid
                JOIN pg_class p ON p.oid = i.inhparent
                WHERE p.relname IN ('stats_counters', 'stats_events')
                """
            )
            for row in names:
                name = row["name"]
                window = self._partition_window(name)
                if window is None:
                    continue  # the DEFAULT partition, or something hand-made
                start, kind = window
                cutoff = counter_cutoff if kind == "counter" else event_cutoff
                if start >= cutoff:
                    continue
                try:
                    await conn.execute(f"DROP TABLE IF EXISTS {name}")
                    dropped.append(name)
                    logger.info("[stats] Dropped partition %s", name)
                except Exception as exc:
                    logger.warning("[stats] Could not drop partition %s: %s", name, exc)
        return dropped

    @staticmethod
    def _partition_window(name: str) -> Optional[Tuple[datetime, str]]:
        """Read a partition's start back from its name."""
        for prefix, fmt, kind in (
            ("stats_counters_", "%Y%m", "counter"),
            ("stats_events_", "%Y%m%d", "event"),
        ):
            if not name.startswith(prefix):
                continue
            suffix = name[len(prefix):]
            try:
                return datetime.strptime(suffix, fmt).replace(tzinfo=timezone.utc), kind
            except ValueError:
                return None
        return None

    # ------------------------------------------------------------------ #
    # Rollup
    # ------------------------------------------------------------------ #

    async def rollup_hourly_to_daily(self, older_than_days: int = 90) -> int:
        """Collapse aged hourly buckets into their day.

        Hourly detail is worth keeping while someone might look at it, and
        worth 24× its size in nothing afterwards. Idempotent: the daily rows
        are written first, and the hourly ones deleted in the same
        transaction, so an interrupted run resumes without double-counting.
        """
        cutoff = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ) - timedelta(days=older_than_days)
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                moved = await conn.fetch(
                    """
                    WITH aged AS (
                        DELETE FROM stats_counters
                        WHERE bucket < $1
                          AND bucket <> date_trunc('day', bucket)
                        RETURNING metric, scope, scope_id, bucket, dims, dims_hash, value
                    )
                    SELECT metric, scope, scope_id,
                           date_trunc('day', bucket) AS day,
                           dims, dims_hash, SUM(value)::bigint AS value
                    FROM aged
                    GROUP BY metric, scope, scope_id, day, dims, dims_hash
                    """,
                    cutoff,
                )
                if moved:
                    await conn.executemany(
                        """
                        INSERT INTO stats_counters
                            (metric, scope, scope_id, bucket, dims, dims_hash, value)
                        VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)
                        ON CONFLICT (metric, scope, scope_id, bucket, dims_hash)
                        DO UPDATE SET value = stats_counters.value + EXCLUDED.value
                        """,
                        [
                            (r["metric"], r["scope"], r["scope_id"], r["day"],
                             r["dims"] if isinstance(r["dims"], str) else json.dumps(r["dims"]),
                             r["dims_hash"], r["value"])
                            for r in moved
                        ],
                    )
        return len(moved)
