# 2026-09-02 — Railway cost: memory optimization pass (tier 0 + tier 1)

## Why

The Railway bill was **$6.14** over the billed window, of which **93% is RAM**
($5.70) against $0.25 CPU, $0.12 volumes and $0.08 egress. Optimizing anything
other than memory is wasted effort.

The per-service breakdown also reframed the target: the bot is only **24.7%** of
the bill. The four separate Postgres instances together are **27.5%**, the
backend 22.7%, moddy-feeds 10.2%, AltGuard 7.6%.

This session covers what is fixable **inside this repo**. The infrastructure
work (consolidating the four Postgres instances, the redundant Health service)
is documented in the plan but is a dashboard operation, not a code change.

## What was done

### Tier 0 — no behaviour change

- **Removed the duplicate uvicorn server.** `bot.start_internal_api_server()`
  spawned a *second* uvicorn for the same `internal_api.server:app` on the same
  port, in a daemon thread with its own event loop, while `main.start_api_server()`
  already served it on the bot's loop. Only one could ever bind; the loser died
  silently while still costing a thread, a loop and a duplicate FastAPI app.
  Replaced by `ModdyBot.wire_internal_api()`, which keeps the one thing the
  thread actually did that mattered: `set_bot(self)` — `main.py` imported
  `set_bot` but never called it, so removing the thread outright would have left
  `/status` reporting on a `None` bot.
- **Logging.** Root logger and handlers now follow `DEBUG` (INFO in production
  instead of DEBUG everywhere), and no log file is written on Railway
  (`RAILWAY_ENVIRONMENT`), where stdout is already collected. Elsewhere a
  `RotatingFileHandler` (5 MB × 3) replaces the un-rotated per-day
  `logging.FileHandler`, which grew forever with no cleanup path.
- **Sentry.** `traces_sample_rate` 0.1 → 0.01, `profiles_sample_rate` 0.1 → 0.0,
  both env-overridable. The profiler ran a dedicated sampling thread; error
  tracking, the actual use case, is unaffected.
- **i18n loaded once.** The singleton is built at import time and `bot.setup_hook`
  called `load_translations()` again, re-parsing ~1.1 MB of JSON into a dict that
  was simply overwritten. `load_translations()` is now idempotent;
  `reload_translations()` passes `force=True`.
- **`gc.freeze()`** at the end of `setup_hook`, once cogs, modules, staff
  commands, persistent views and translations are all resident and permanent.

### Tier 1 — behaviour change, gated and compensated

- **`chunk_guilds_at_startup=False`** (new `config.CHUNK_GUILDS_AT_STARTUP`,
  default False, `CHUNK_GUILDS_AT_STARTUP=true` restores the old behaviour in one
  redeploy). This is the single largest memory item: with the `members` intent on,
  discord.py otherwise downloads every guild's full member list at startup and
  keeps a resident `Member` (~1–2 KB) for each.
- **New `utils/members.py`**: `get_or_fetch_member()` (cache, then one REST
  fetch) and `fetch_all_members(guild, cache=False)` (full list on demand,
  without leaving it resident).
- **Converted the call sites where a `None` breaks a feature**, all verified to
  be in async functions: ticket permissions, appeal apply/reverse, backend
  sanction add/revoke, interserver relay, team link sessions, staff thread guard,
  AltGuard staff target resolution, reminder delivery, poll-vote logs.
  `modules/altguard.py` already had its own fetch fallback and was left alone.
- **`modules/automod_ai.py::_observe_target_reaction`** was the sharpest edge: it
  read a plain cache miss as `target_gone = True`, which would have fed a false
  signal into the target-reaction classifier and therefore into a sanction
  decision. Now resolved authoritatively.
- **`services/ticket_service.build_overwrites` stayed synchronous.** It has 4
  production callers and 12 test callers; making it async would have churned all
  of them. Instead it takes an optional pre-resolved `members` map (falling back
  to `guild.get_member`), and the four async callers build it first via the new
  `resolve_ticket_members()`. An uncached ticket owner would otherwise have been
  left out of the overwrites — locked out of their own ticket.
- **`max_messages`** 5000 → 1000. Every consumer acts on messages seconds to
  minutes old (the file's own comment says so).
- **Redis blocking reads** `block=5000` → `30000` in the task stream and both
  feeds loops. A blocking `XREAD` returns as soon as an entry lands, so latency
  is unchanged; the client just stops re-issuing the command every 5s around the
  clock. Redis was burning 152 vCPU-min, nearly as much as the bot itself.
- **Bounded three caches that could only grow:**
  - `automod/engine.py`: `_budget_degraded` / `_budget_notified` were keyed
    `(guild, UTC day)` and grew by one tuple per active guild per day forever,
    while only the current day was ever read. Now guild-id sets cleared on day
    rollover.
  - `gateway/quota.py`: `_limit_cache` checked its TTL on read only, so expired
    entries stayed resident — one per distinct user/guild ever served. Now an
    `OrderedDict` pruned of expired entries and capped at 2048 on the write path.
  - `services/precedent_service.py`: the TTL bounded the cache in *time* but not
    in *size*. Added `PRECEDENT_CACHE_MAX_GUILDS = 50` with LRU eviction — at
    500 vectors per guild that is ~3 MB each, so a busy window could otherwise
    pin hundreds of megabytes.

## Decisions and why

- **The Health heartbeat interval was left at 20s.** It was going to move to 60s
  for egress, until the file's own docstring turned out to state the monitor's
  TTL is 60s (three missed heartbeats). A 60s interval would have broken the dead
  man's switch. Egress is 1.3% of the bill — not worth any risk.
- **float32 vectors and the precedent TTL sweep were already implemented.** The
  plan listed both; only the missing global size cap was added.
- **Mutual-server scans were deliberately NOT converted.** `cogs/logs.py`
  (`on_user_update` fan-out), `staff/commands/team/mutualserver.py` and
  `staff/commands/team/user.py` iterate `bot.guilds` calling `get_member` on each.
  Fetching there would mean one REST call **per guild per lookup**. They now
  under-report to "guilds where the user is cached" — see Known issues.

## Files modified

`bot.py`, `main.py`, `config.py`, `utils/i18n.py`, `utils/tech_logger.py`,
`utils/members.py` (new), `cogs/error_handler.py`, `cogs/altguard.py`,
`cogs/tickets.py`, `cogs/reminder.py`, `cogs/moderation_commands.py`,
`modules/automod_ai.py`, `modules/interserver.py`, `services/ticket_service.py`,
`services/appeal_service.py`, `services/team_link_session.py`,
`services/precedent_service.py`, `services/feeds_client.py`,
`serverlogs/listeners/polls.py`, `staff/commands/team/server.py`,
`staff/commands/mod/altguard/_shared.py`, `automod/engine.py`,
`automod/constants.py`, `gateway/quota.py`, `docs/RAILWAY.md`, `CLAUDE.md`.

## Known issues / follow-ups

- **Accepted regression from disabling the chunk:** anything scanning every guild
  to find where a user is a member now under-reports — `/mutualserver`, the shared
  server count in `/team user`, and the `on_user_update` fan-out in server logs
  (username/avatar changes will only be logged in guilds where the member is
  cached). `CHUNK_GUILDS_AT_STARTUP=true` restores accuracy at the old memory
  cost. Worth watching in production before deciding.
- **`MALLOC_ARENA_MAX=2` is not set by this commit** — it is a Railway env var,
  and probably the single best effort/reward item left. Documented in
  `docs/RAILWAY.md`.
- **Not touched, and where the money actually is:** consolidating the four
  Postgres instances into one (~27.5% of the bill), the Health service that
  duplicates Better Stack, and the Backend / moddy-feeds services (33% combined),
  none of which live in this repo.
- **No memory instrumentation exists.** Every figure above is an estimate. A
  `/dev memory` staff command exposing `tracemalloc` and the size of the main
  caches would turn the next pass into measurement rather than guesswork
  (`psutil` is already a dependency).

## Verification

- `python3 -m pytest -q` → **1546 passed**.
- Every `.py` in the repo compiles (`compile()`, which catches `await` outside an
  async function where `ast.parse` does not).
- Every touched module imports cleanly, and `i18n` was confirmed to load its five
  locales exactly once.
