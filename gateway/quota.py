from __future__ import annotations
import asyncio
import logging
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Optional

from .spec import QuotaTarget, QuotaPlan
from .errors import QuotaExceededError

logger = logging.getLogger("moddy.gateway.quota")

_LIMIT_CACHE_TTL = 60.0  # seconds
# Hard cap on distinct (scope, key, type) limits kept in memory.
_LIMIT_CACHE_MAX_ENTRIES = 2048
_QUOTA_KEY_TTL = 172800  # 48h in seconds


class QuotaManager:
    """Daily quota tracking via Redis counters (reset at UTC midnight via key rotation)
    with PG-backed limit configuration cached in memory."""

    def __init__(self, redis, pool):
        self._redis = redis
        self._pool = pool
        # (scope, key, type) -> (limit, cached_at). The TTL was only checked on
        # read, so an expired entry stayed resident forever: one key per distinct
        # user/guild the gateway ever served. Insertion-ordered and capped, with
        # expired entries dropped on write (see _prune_limit_cache).
        self._limit_cache: "OrderedDict[tuple, tuple[int, float]]" = OrderedDict()
        self._cache_lock = asyncio.Lock()

    def _date_str(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%d")

    def _redis_key(self, target: QuotaTarget) -> str:
        return f"quota:{target.scope.value}:{target.key}:{target.type}:{self._date_str()}"

    async def _get_limit(self, target: QuotaTarget) -> int:
        """Resolve daily limit for a target. Returns -1 for unlimited."""
        cache_key = (target.scope.value, target.key, target.type)

        async with self._cache_lock:
            cached = self._limit_cache.get(cache_key)
            if cached and time.monotonic() - cached[1] < _LIMIT_CACHE_TTL:
                return cached[0]

        limit = -1
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT daily_limit FROM quota_overrides"
                    " WHERE scope=$1 AND key=$2 AND type=$3",
                    target.scope.value, target.key, target.type,
                )
                if row:
                    limit = row["daily_limit"]
                else:
                    row = await conn.fetchrow(
                        "SELECT daily_limit FROM quota_limits"
                        " WHERE scope=$1 AND type=$2 AND tier='default'",
                        target.scope.value, target.type,
                    )
                    limit = row["daily_limit"] if row else -1
        except Exception as exc:
            logger.warning("Failed to fetch quota limit for %s: %s", cache_key, exc)

        async with self._cache_lock:
            self._limit_cache[cache_key] = (limit, time.monotonic())
            self._limit_cache.move_to_end(cache_key)
            self._prune_limit_cache()

        return limit

    def _prune_limit_cache(self) -> None:
        """Drop expired entries, then the oldest ones past the size cap.

        Called under `_cache_lock` from the write path only: the read path is hot
        and must stay a plain dict lookup. Re-resolving an evicted limit costs one
        query, which is what the cache miss would have cost anyway.
        """
        now = time.monotonic()
        expired = [k for k, (_, at) in self._limit_cache.items()
                   if now - at >= _LIMIT_CACHE_TTL]
        for k in expired:
            del self._limit_cache[k]
        while len(self._limit_cache) > _LIMIT_CACHE_MAX_ENTRIES:
            self._limit_cache.popitem(last=False)

    async def _get_count(self, target: QuotaTarget) -> int:
        """Current daily usage from Redis."""
        try:
            val = await self._redis.get(self._redis_key(target))
            return int(val) if val else 0
        except Exception as exc:
            logger.warning("Failed to read quota count for %s: %s", target, exc)
            return 0

    async def check_all(self, plan: QuotaPlan) -> None:
        """Check all targets. Raises QuotaExceededError on first violation."""
        for target in plan:
            limit = await self._get_limit(target)
            if limit == -1:
                continue
            count = await self._get_count(target)
            if count >= limit:
                raise QuotaExceededError(target)

    async def consume_all(self, plan: QuotaPlan) -> None:
        """Increment all target counters after a successful call."""
        for target in plan:
            try:
                key = self._redis_key(target)
                pipe = self._redis.pipeline()
                pipe.incr(key)
                pipe.expire(key, _QUOTA_KEY_TTL)
                await pipe.execute()
            except Exception as exc:
                logger.warning("Failed to consume quota for %s: %s", target, exc)

    async def available(self, target: QuotaTarget) -> bool:
        """Quick boolean check for consumer-side gating."""
        try:
            limit = await self._get_limit(target)
            if limit == -1:
                return True
            count = await self._get_count(target)
            return count < limit
        except Exception:
            return True  # fail open

    def invalidate_cache(self, scope: Optional[str] = None) -> None:
        """Evict limit cache entries (call after quota_limits/overrides change)."""
        if scope is None:
            self._limit_cache.clear()
        else:
            self._limit_cache = {
                k: v for k, v in self._limit_cache.items() if k[0] != scope
            }
