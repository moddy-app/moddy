# 2026-07-11 — Automod embedding score cache + first automated test suite

## What was done

Two connected pieces of work on the automod detection core, both fully
verifiable offline (no live Discord gateway or database):

### 1. Embedding score cache (addresses the flagged volume/cost follow-up)

The embedding step (`automod/embeddings.py`, funnel step 4) fired **one embedding
API call for every non-trivial, non-blocklisted message** — the volume concern
called out in the `2026-06-28` automod session logs and `docs/AUTOMOD.md`.

Because the reference vectors are embedded **once per process and never change**,
a message's cosine score is deterministic for the process lifetime. So we now
memoise it:

- **`automod/cache.py`** (new) — `LruTtlCache`, a dependency-free bounded LRU
  cache with an optional TTL (defensive only; correctness doesn't need it) and
  hit/miss/eviction/expiration counters + `stats()`. Injectable clock for tests.
- **`EmbeddingEngine.score()`** now checks the cache first, and coalesces
  concurrent identical requests via a **single-flight** in-flight future map.
  Net effect: a raid/copypasta flood (same text ×N) costs **one** embedding call
  instead of N — whether the duplicates arrive back-to-back (cache hit) or all at
  once (single-flight). Transient `None` results (not-ready / empty response) are
  **not** cached, so a blip can't get pinned.
- It is purely an optimization — identical input → identical output — so it can
  never change a moderation decision. Proven by the baseline tests below still
  passing unchanged after the cache landed.
- **`constants.py`** — `EMBED_CACHE_ENABLED` / `EMBED_CACHE_MAX_ENTRIES` (4096) /
  `EMBED_CACHE_TTL_SECONDS` (1800).
- **`AutomodEngine.cache_stats()`** / `EmbeddingEngine.cache_stats()` expose the
  counters for a future staff diagnostic (`bot._automod_engine.cache_stats()`).

### 2. First automated test suite for the detection core

There was **no** automated test suite for the pure-Python, Discord-agnostic
automod pipeline. Added `tests/automod/` (pytest, 101 tests):

- `test_normalize.py` — accent/leet folding, repeat collapse, de-spam.
- `test_prefiltre.py` — bot/system/empty short-circuits.
- `test_triviaux.py` — allowlist + the lowercase/stripped safety invariant.
- `test_blocklist.py` — routing + category/gravity, obfuscation (leet,
  separators, repetition), emoji gestures, word-boundary Scunthorpe safety.
- `test_constants.py` — severity clamp + threshold monotonicity.
- `test_embeddings.py` — `ensure_ready` idempotency, scoring, **and** the cache
  (hit/miss, distinct keys, transient-None-not-cached, disabled cache,
  single-flight coalescing).
- `test_cache.py` — LRU eviction + recency, TTL expiry (fake clock), disabled,
  stats/hit_rate.
- `test_engine.py` — funnel routing: trivial & blocklist **skip** the embedding
  step, embedding above/below threshold, `force_nano`, and a 5×-flood → 1 embed
  call integration test.

Tooling: `pytest.ini` (asyncio auto mode), `requirements-dev.txt`, root
`conftest.py` (repo root on `sys.path`).

## Files

- Added: `automod/cache.py`, `tests/automod/test_*.py` (7 files), `pytest.ini`,
  `requirements-dev.txt`, `conftest.py`, this log.
- Modified: `automod/embeddings.py`, `automod/constants.py`, `automod/engine.py`,
  `docs/AUTOMOD.md`, `CLAUDE.md`.

## Verification

`pytest` → **102 passed** (101 automod + the pre-existing `test_embeds.py`). The
baseline detection tests were written **before** the cache and still pass
unchanged afterwards, which is the proof that the cache is behavior-preserving.

## Decisions

- **Key on the exact message text**, not a normalized form: the embedding is
  computed on the raw content, so exact-text keying is the only correctness-safe
  choice. Raid/copypasta floods are byte-identical, which is exactly the win.
- **In-memory, not Redis**: the shared per-bot engine already holds the
  references in-process; an in-memory cache needs no infra and matches the
  engine's lifecycle. A Redis-backed cross-process cache could come later if the
  bot ever shards the automod engine, but it isn't needed today.
- **TTL is defensive only** (30 min) — the score is stable for the process
  lifetime, so the TTL just bounds memory freshness.

## Follow-ups / not done

- A staff `/dev` diagnostic surfacing `cache_stats()` would make the savings
  visible in production (small, optional).
- `SEUIL_EMBEDDING` / references / blocklist calibration on real traffic remains
  the standing automod follow-up (unchanged by this work).
