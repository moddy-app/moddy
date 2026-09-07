# Statistics

> How Moddy measures everything without paying for it. Read this before
> adding a statistic, and before writing anything that reads one.

---

## 1. The idea in one paragraph

A Discord bot produces millions of measurable events a month and gets asked
maybe fifty questions a quarter. Storing one row per event to answer those
fifty questions is how a statistics system becomes the most expensive table
in the database. So Moddy never writes a row per event: it **counts in
memory and writes the total**. Ten thousand commands on one server in a day
cost one row. What that costs in exchange is precision nobody asked for —
the exact second a command ran — and a fortnight-long raw window exists for
the times that turns out to matter.

Adding a metric is one entry in `stats/registry.py`. No migration, no
column, no schema change — ever.

---

## 2. The pieces

| File | Role |
|---|---|
| `stats/registry.py` | **The source of truth.** Every metric, its scope, its bucket size and its allowed dimensions |
| `stats/service.py` | `bot.stats` — records (`incr`, `observe_unique`) and flushes the aggregate every 60 s |
| `stats/rollup.py` | `bot.stats_rollup` — daily snapshots, ageing, partition maintenance |
| `db/repositories/stats.py` | The writes and the dashboard reads |
| `cogs/stats_events.py` | The Discord listeners that emit |
| `db/base.py` | The DDL of the five tables |

---

## 3. Recording a statistic

```python
bot.stats.incr("command.used", guild_id=guild.id,
               dims={"command": "config", "kind": "slash"})
```

Three properties matter, and all three are deliberate:

* **It is synchronous.** No `await`, no I/O on the hot path.
* **It never raises.** A bad dimension, an unknown key, a broken database —
  every one of them is logged once and swallowed. Measuring a feature must
  never be able to break it.
* **It aggregates.** The call adds to a dictionary; the flush writes the sum.

Inside a module, use the shorthand — it fills in the module id and the guild:

```python
self.count("sent")          # ModuleBase.count(), see modules/module_manager.py
```

For distinct people, never a list of ids:

```python
bot.stats.observe_unique("user.active", user.id, guild_id=guild.id)
```

The ids are folded into a Redis HyperLogLog at the next flush (~12 KB per
key, 0.8 % error) and the daily rollup stores only its cardinality.

### Adding a metric

Add one entry to `_ALL` in `stats/registry.py`:

```python
Metric("ticket.opened", dimensions=("panel",))
```

Then emit it. That is the whole change. Two rules decide whether the metric
will be cheap:

1. **Every dimension must have bounded cardinality.** A command name, a
   module id, a boolean, a provider — yes. A user id, a message id, a
   channel id, free-text — never: each distinct value is a row per day.
   Undeclared dimensions are dropped before they reach the database, so the
   mistake cannot be made by accident, only by declaring it.
2. **Leave the resolution alone.** Per-server metrics are daily, global ones
   hourly. Making a per-server metric hourly multiplies its rows by 24.

---

## 4. What is stored

| Table | What | Retention |
|---|---|---|
| `stats_counters` | Pre-aggregated counters, partitioned by month | hourly buckets 90 days, then daily; partitions dropped at 12 months |
| `stats_snapshots` | Daily gauges: servers, members, adoption, distinct users | forever (a few dozen rows a day) |
| `guild_events` | Every arrival and departure of Moddy | forever |
| `guild_installs` | Where an installation came from (UTM) — **written by the backend** | forever |
| `stats_events` | Raw rows for metrics marked `raw=True`, partitioned by day | 14 days |

### `stats_counters`

```
metric | scope | scope_id | bucket | dims (jsonb) | dims_hash | value
```

`dims` holds whatever the metric declared, which is what makes the system
extensible without columns; `dims_hash` (SHA-1 of the canonical JSON) keeps
the primary key short and fixed-width. The write is
`INSERT … ON CONFLICT DO UPDATE SET value = value + EXCLUDED.value`: an
**addition**, so a replayed flush after a failed commit cannot double-count,
and two bot processes simply add their own totals to the same row.

Partitioning is what makes retention free — dropping a month is a `DROP
TABLE`, not a `DELETE` that locks writers and leaves bloat. Both partitioned
tables also carry a `DEFAULT` partition so a missed maintenance pass never
loses a write; rows that land there are queryable but cannot be dropped
cheaply, so `stats_counters_default` should stay empty in normal operation.

### Gauges vs counters

A counter answers "how many times", a gauge answers "how many are there".
The bot's own curve — servers, members, premium, module adoption — is
gauges, photographed once a day by the rollup. Per-server member counts are
photographed **only when they change**: a server whose size is flat stores
nothing, and a chart carries the last known value forward.

---

## 5. The bot's growth and its retention

`guild_events` is the one place that keeps a row per occurrence without
apology: arrivals happen a few thousand times a month, and aggregating them
would destroy the detail that makes them worth having.

Retention is **computed, never stored** — a server is still there if its
latest event is a join:

```sql
-- Net growth per day
SELECT day, joins, leaves, joins - leaves AS net
FROM (
  SELECT (created_at AT TIME ZONE 'UTC')::date AS day,
         COUNT(*) FILTER (WHERE event = 'join')  AS joins,
         COUNT(*) FILTER (WHERE event = 'leave') AS leaves
  FROM guild_events GROUP BY 1
) g ORDER BY day;
```

`StatsRepository.guild_growth()` and `retention_cohorts()` are these queries,
ready to call.

---

## 6. Acquisition — the contract with the backend

`guild_installs` is the only stats table the bot does not fill in on its own,
because neither side knows the whole story: the **backend** sees the UTM
parameters carried in the OAuth2 `state`, but not whether the install
succeeded; the **bot** sees the guild appear, but not where it came from.

**The backend writes the row** at the OAuth2 callback:

```sql
INSERT INTO guild_installs (guild_id, installer_id, source, utm)
VALUES ($1, $2, $3, $4::jsonb)
ON CONFLICT (guild_id) DO UPDATE
SET source = EXCLUDED.source, utm = EXCLUDED.utm,
    first_seen_at = now(), confirmed_at = NULL;
```

* `source` — a short, bounded label: `topgg`, `discovery`, `profile`, `ads`,
  `command`, `direct`. Keep the vocabulary small; it is a dimension.
* `utm` — the full detail: `{"medium": …, "campaign": …, "content": …,
  "term": …}`. Free-form, queried rarely, never used as a dimension.

**The bot stamps `confirmed_at`** at `on_guild_join` (`confirm_install()`)
and copies `source` onto the `guild_events` row. A missing row is not an
error — it means a direct invite, or a backend that does not write this table
yet. Nothing in the bot depends on it.

That split is what makes both halves computable:

```sql
-- Conversion by source: clicks that reached the callback vs installs that landed
SELECT COALESCE(source,'unknown') AS source,
       COUNT(*) AS started,
       COUNT(*) FILTER (WHERE confirmed_at IS NOT NULL) AS installed
FROM guild_installs GROUP BY 1 ORDER BY installed DESC;

-- And retention by source: which channel brings servers that stay
SELECT e.source, COUNT(*) FILTER (WHERE e.event = 'join') AS acquired,
       COUNT(*) FILTER (WHERE latest.event = 'join')      AS still_here
FROM guild_events e
JOIN LATERAL (
  SELECT event FROM guild_events x
  WHERE x.guild_id = e.guild_id ORDER BY created_at DESC LIMIT 1
) latest ON TRUE
WHERE e.event = 'join'
GROUP BY e.source;
```

A source that converts well but retains badly is a source that is buying the
wrong servers — which is the actual question the UTMs were added to answer.

---

## 7. Reading (dashboard)

The backend shares the database and reads it directly. Two views hide the
partitioning and the `dims_hash`:

| View | Columns |
|---|---|
| `stats_guild_daily_v` | `metric, guild_id, day, dims, value` |
| `stats_global_daily_v` | `metric, day, dims, value` |

```sql
-- Top commands on one server over 30 days
SELECT dims->>'command' AS command, SUM(value) AS uses
FROM stats_guild_daily_v
WHERE metric = 'command.used' AND guild_id = $1
  AND day >= CURRENT_DATE - 30
GROUP BY 1 ORDER BY uses DESC LIMIT 20;

-- What a server cost in AI last month, in dollars
SELECT SUM(value) / 1e6 AS usd
FROM stats_guild_daily_v
WHERE metric = 'ai.cost' AND guild_id = $1
  AND day >= date_trunc('month', CURRENT_DATE - INTERVAL '1 month');

-- The bot's curve
SELECT day, value FROM stats_snapshots
WHERE metric = 'bot.guilds' AND scope = 'global' ORDER BY day;

-- Module adoption over time
SELECT day, dims->>'module' AS module, value FROM stats_snapshots
WHERE metric = 'module.enabled' ORDER BY day;
```

Money is stored as **millionths of a dollar** (`ai.cost`): the counter column
is a `BIGINT`, and a float would not survive the additive upsert intact.

`api_calls` remains the fine-grained truth for API calls — one row per call,
with the correlation id, the latency and the error. `ai.*` are the same facts
pre-aggregated, so "what did this server cost us in August" is a three-row
read instead of a scan.

---

## 8. What can go wrong

| Symptom | Cause | Fix |
|---|---|---|
| A metric records nothing | Its key is not in `stats/registry.py`, or a per-server metric was called without a `guild_id` | Check the logs: both are logged once as a warning |
| A dimension is missing from the rows | It was not declared on the metric | Add it to `dimensions=` — and check its cardinality first |
| `stats_counters` grows faster than expected | A dimension with unbounded values, or a per-server metric set to hourly | `SELECT metric, COUNT(*) FROM stats_counters GROUP BY 1 ORDER BY 2 DESC` names the culprit immediately |
| Counters stop after a Redis outage | They do not — only distinct-people counts need Redis | — |
| Up to a minute of counters missing after a crash | The aggregate had not been flushed | By design; a graceful shutdown flushes |

---

## 9. See also

- [DATABASE.md](DATABASE.md) — the tables in the wider schema
- [API_GATEWAY.md](API_GATEWAY.md) — `api_calls`, the raw side of `ai.*`
- [REDIS_COMMUNICATION.md](REDIS_COMMUNICATION.md) — the `stats:*` namespace
- [RAILWAY.md](RAILWAY.md) — why storage and memory are the cost that matters
