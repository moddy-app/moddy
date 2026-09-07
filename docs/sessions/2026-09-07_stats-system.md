# Statistics system — storage design and implementation

## What was done

Built the statistics system from nothing: Moddy had no way to measure
anything except `api_calls` (one raw row per gateway call). The system now
records commands, modules, AI cost, server activity, the bot's own growth
curve, server retention and acquisition sources.

The design constraint was cost, not schema. Railway bills RAM and storage,
and a "one row per event" table on a Discord bot produces millions of rows a
month to answer questions asked once a quarter. So the system **aggregates
before writing**: frequent events are counted in memory and a flush writes
the total once a minute. Ten thousand commands on one server in a day cost
one row.

## Files

**New**
- `stats/registry.py` — every metric declared once: scope, bucket size,
  allowed dimensions. Adding a metric needs no migration.
- `stats/service.py` — `bot.stats`: `incr()` (synchronous, never raises,
  aggregates), `observe_unique()` (HyperLogLog), 60 s flush with merge-back
  on failure.
- `stats/rollup.py` — daily snapshots, hourly→daily ageing, partition
  maintenance and purge.
- `db/repositories/stats.py` — `StatsRepository`: additive upserts, dashboard
  reads, growth/cohort/acquisition queries, partition management.
- `cogs/stats_events.py` — the Discord listeners that emit.
- `docs/STATS.md`, `tests/test_stats.py`.

**Modified**
- `db/base.py` — DDL for `stats_counters`, `stats_snapshots`, `guild_events`,
  `guild_installs`, `stats_events` + the two dashboard views.
- `bot.py` — instantiation, flush loop in `setup_hook`, rollup in `on_ready`,
  flush on shutdown.
- `gateway/logger.py`, `gateway/__init__.py` — AI counters alongside the
  existing `api_calls` row.
- `modules/module_manager.py` — `ModuleBase.count()` shorthand; emitters in
  `welcome_channel`, `auto_role`, `starboard`, `automod_ai`.
- `notifications/service.py`, `services/case_service.py`,
  `services/ticket_service.py`, `cogs/bump_reminder.py`,
  `serverlogs/service.py` — one counter each at their existing choke point.
- `CLAUDE.md`, `docs/DATABASE.md`, `docs/REDIS_COMMUNICATION.md`.

## Decisions

**Aggregation in memory, not in Redis.** The plan called for `HINCRBY` per
event. In-process aggregation is strictly better here: `incr()` becomes
synchronous, so recording a statistic cannot slow down or break the feature
it measures — a property `await redis.hincrby()` cannot offer. The additive
upsert makes it correct across processes anyway. Cost: up to one flush
interval of counters lost on a hard kill (a graceful shutdown flushes).
Redis is still used for the distinct-people HyperLogLogs.

**`dims` JSONB + `dims_hash`.** Dimensions in JSONB is what makes a new
metric free; hashing them keeps the primary key short and fixed-width. The
registry drops undeclared dimensions before they can reach the database,
which is the guard rail against the classic cardinality explosion (a user id
as a dimension turns one row a day into a hundred thousand).

**Daily buckets per server, hourly globally.** The single biggest cost lever:
hourly per-server buckets are 24× the rows for a precision nobody reads at
that scope.

**Partitioning over DELETE.** Retention is a `DROP TABLE`, instantaneous and
it returns the disk. Both partitioned tables keep a `DEFAULT` partition so a
missed maintenance pass never loses a write.

**Retention and cohorts are computed, not stored.** `guild_events` keeps one
row per arrival/departure — a few thousand a month — and every retention
question is a query over it.

**Per-server member counts are only written when they change.** A flat server
stores nothing; a chart carries the last known value forward.

## Verification

- `python3 -m pytest -q` — 1760 passed (29 new).
- The DDL, the additive upsert, the rollup, the views, the partition creation
  and the growth/cohort/acquisition queries were all executed against a live
  PostgreSQL 16, including checks that a re-run of the rollup changes nothing.

## Follow-ups

- **`guild_installs` is not filled in yet.** The backend must write the row at
  the OAuth2 callback (contract in `docs/STATS.md` §6). Until it does,
  `source` stays NULL everywhere and the acquisition report is empty — the bot
  works fine with the table empty.
- More emitters can be added as needed; each is one line at an existing choke
  point plus one entry in the registry.
- If `stats_counters_default` ever accumulates rows, a maintenance pass missed
  its window — the rows are queryable but not cheaply droppable.
