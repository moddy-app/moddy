# 2026-08-08 — Memory optimization (bot process)

## Context

The Railway bill (~5.77 $/month, ~3.54 $ of it for MODDY + Backend) is driven
almost entirely by RAM; CPU is negligible. The graphs show no leak — spikes then
a return to a low base — so the goal was **lowering the resident baseline**, not
chasing a leak. Scope was deliberately limited to targeted, low-risk changes at
the current scale (~50 guilds); no architectural rework, no cache migration to
Redis (Redis also carries the bot↔backend Pub/Sub and Streams, and must not
compete with it for memory).

Audit covered: discord.py configuration, every custom in-memory cache, the
in-process FastAPI, connection pools, and service/DB topology. The **Backend is a
separate repo** and was not audited — deferred to a later session.

## What was found (by estimated impact)

| Item | Before | Notes |
|---|---|---|
| Precedent vectors held as `list[float]` | 24.6 MB **per guild** at cap | Stored float32 on disk, unpacked into 1536 distinct Python floats |
| `PrecedentService._cache` never actively evicted | grows with every guild ever seen | TTL enforced on read only, and only for the guild being read |
| `max_messages=10000` | ~15–30 MB | 10× the discord.py default |
| `PRECEDENT_QUERY_VECTOR_CACHE` (256 vectors) | 12.6 MB | Bounded, but the entries were oversized |
| Embedding reference vectors (96 phrases) | 4.7 MB | Same `list[float]` problem |
| `member_cache_flags` implicit `all()` | voice cache paid, never read | No `on_voice_state_update` anywhere in the codebase |
| `_DELETED_CACHE` | up to ~17 MB | Purged on read only; reads only happen on `/ban`,`/kick`,`/mute`,`/warn` |
| DB pool hardcoded `5/20` | a few MB + Postgres-side RAM | Silently overrode `config.py`'s env-driven values |

Explicitly left alone: `chunk_guilds_at_startup` (kept `True` — turning it off
risks silent `get_member()` → `None` degradation in `appeal_service` and
`mutualserver`; revisit once total member count is known), i18n translations
(~2.5–4 MB, not worth the resync risk), the `LruTtlCache` instances (already
bounded with TTL and eviction counters), `starboard_messages` (~100 B/entry).

## Changes

### 1. Embedding vectors → float32 `array.array` (the big one)

- `db/repositories/precedents.py` — `unpack_vector()` returns `array.array('f')`
  via `frombytes` instead of `list(struct.unpack(...))`; added an explicit
  corrupt-blob guard (length not a multiple of 4) and a big-endian byteswap so
  the little-endian `<f` on-disk format stays authoritative.
- `automod/embeddings.py` — `_normalize_vec()` now returns `array.array('f')`.
  This is the single point every live vector passes through, so it covers the
  reference vectors, the query-vector cache and `embed_query()`'s return value.
- `automod/precedents.py` — `Precedent.vector` and the matcher signatures widened
  to `Sequence[float]`. `_dot`/`cosine` already used `zip()`/`len()` and needed
  no logic change.

Measured: **49 208 B → 6 620 B per 1536-dim vector (7.4×)**.

- Precedents at cap: 24.6 MB → 3.31 MB per guild
- Query vector cache: 12.6 MB → 1.69 MB
- Reference vectors: 4.7 MB → 0.64 MB

No precision loss (the disk format is float32 already): self-similarity measures
0.999999996, i.e. ~4e-9 drift against thresholds at 0.80 / 0.85 / 0.97. The
golden-set replay reports **`changed vs baseline: none`**.

### 2. Active eviction of the precedent cache

`services/precedent_service.py` — added `_evict_expired()`, called on every load.
Bounds the cache to guilds active within `PRECEDENT_CACHE_TTL_SECONDS` instead of
every guild seen since boot. O(cached guilds) on a path already about to hit the
DB.

### 3. `max_messages` 10000 → 5000 (`bot.py`)

The cache is global and evicted by count, not age. Its only consumers are the
non-raw `on_message_delete` listeners (interserver relay cleanup, staff
auto-delete, the deleted-message content cache) and the non-raw `on_reaction_add`
feeding the automod relationship graph (`REACTION_WAIT_SECONDS` = 20) — all
acting on messages seconds to minutes old.

5000 rather than the 2000 initially proposed: a deleted message's **content** is
only recoverable if it is still cached at deletion time, so shrinking the cache
shrinks that capture window. 5000 keeps margin while still returning ~9–18 MB.

### 4. `member_cache_flags(joined=True, voice=False)` (`bot.py`)

Default was `MemberCacheFlags.all()` (value 3). There is no
`on_voice_state_update` listener anywhere, so the voice cache was pure cost.
`joined=True` is kept — `get_member()` is used across appeals, automod,
interserver, reminders and staff commands. Verified `_verify_intents()` accepts
the combination.

`presences` was already correctly off (`Intents.default()` excludes it); `/invite`
takes its online count from the REST API's `approximate_presence_count`.

### 5. DB pool wired to `config.py`

`db/base.py` aliases `POOL_MIN_SIZE`/`POOL_MAX_SIZE` from
`config.DB_POOL_MIN_SIZE`/`DB_POOL_MAX_SIZE` instead of hardcoding `5`/`20` —
the hardcoded values silently overrode the env vars and left the config module's
values dead. Defaults are now `1`/`8`. Also cuts Postgres-side RAM (separately
billed Railway service).

### 6. `_DELETED_CACHE` purge on write (`cogs/moderation_commands.py`)

Cap 500 → 300, plus a 24 h TTL enforced on write: the written guild is pruned
every time, all other guilds are swept at most once per 10 min. Empty buckets
drop their guild key. A guild that never receives a sanction command no longer
holds its bucket for the process lifetime.

### 7. Dead files removed

`PR_DESCRIPTION.md`, `tests/Test V2.py` (a standalone scratch bot with its own
`commands.Bot` and hardcoded intents — not collected by pytest).

## Estimated total

~20–22 MB per automod-active guild, plus ~25–35 MB of fixed baseline
(message cache, query-vector cache, reference vectors, voice cache, pool,
deleted-message cache). The per-guild term dominates and was previously the
single largest resident cost.

## Verification

- `python3 -m pytest -q` → **650 passed** (was 648 + 2 new tests on the vector
  representation).
- `python3 -m automod.eval.run --replay` → precision/recall/f1 = 1.000,
  `changed vs baseline: none`.
- Bot constructed with a dummy token to confirm the live values: `max_messages=5000`,
  `member_cache_flags` joined=True/voice=False (value 2), `intents.presences=False`,
  pool `1/8`.

## Known trade-offs / follow-ups

- **CPU:** `array.array` boxes a float per index access, so the dot product is
  slightly slower. Measured on the worst case (500 precedents × 1536 dims):
  30.8 ms → 34.7 ms (+13 %, ~4 ms). That path only runs after an embedding
  network call of tens of ms, so it is not a meaningful regression. If it ever
  became one, the fix is numpy, not a return to lists — and numpy would add
  ~20 MB, defeating the purpose.
- **Deleted-message capture window** narrows slightly with `max_messages=5000`.
  It only affects the AI-suggested *prefill* of the sanction reason (editable by
  the moderator); `channel.history()` remains the primary source and automod
  evidence is captured at `on_message`, not at deletion.
- `tests/automod/test_precedents.py::test_unpack_none_is_empty` was updated:
  `array.array('f') == []` is `False`, so it now asserts emptiness via `len()`
  and truthiness.
- **Not done:** `chunk_guilds_at_startup` (needs `sum(g.member_count for g in
  bot.guilds)` to size the trade-off), and the **Backend** audit (separate repo).
- Worth checking on the Railway side: `moddy-feeds-DB` is never touched by the
  bot in SQL (only via Redis in `services/feeds_client.py`), so merging it into
  the main Postgres as a second schema would be transparent **from the bot's
  side** — depends on what moddy-feeds itself does with it.
