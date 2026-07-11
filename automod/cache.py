"""A tiny, dependency-free bounded cache for the embedding step.

The embedding reference vectors are computed **once per process** and never
change during its lifetime (see :mod:`automod.embeddings`). The cosine score of
a given message is therefore a deterministic function of the message text for as
long as the process lives. That makes it safe to memoise, which is exactly what
we want at scale: a raid or a copypasta flood is, by construction, the same text
repeated many times, and today each occurrence costs one embedding call.

:class:`LruTtlCache` is a plain LRU cache with an optional TTL. The TTL is
purely defensive (a freshness bound / a way to let long-idle entries fall out);
correctness does not depend on it. The cache is **synchronous** — callers hold
no lock because the pipeline runs on a single asyncio event loop and every cache
mutation happens between ``await`` points, never across one.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Callable, Generic, Hashable, Optional, Tuple, TypeVar

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")

# Sentinel returned by ``get`` on a miss so that a legitimately cached ``None``
# value could still be distinguished from "absent" (the embedding cache never
# stores ``None``, but keeping the distinction makes the class reusable).
MISS = object()


class LruTtlCache(Generic[K, V]):
    """Bounded least-recently-used cache with an optional per-entry TTL.

    :param max_entries: hard cap on stored entries; the least-recently-used
        entry is evicted once the cap is exceeded. ``<= 0`` disables caching
        (every ``get`` is a miss and ``set`` is a no-op).
    :param ttl_seconds: entries older than this are treated as missing and
        dropped on access. ``None`` or ``<= 0`` disables expiry.
    :param clock: monotonic time source, injectable for tests.
    """

    def __init__(
        self,
        max_entries: int,
        ttl_seconds: Optional[float] = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max = int(max_entries)
        self._ttl = float(ttl_seconds) if ttl_seconds and ttl_seconds > 0 else None
        self._clock = clock
        # key -> (stored_at, value); ordered by recency of use (LRU at the front).
        self._data: "OrderedDict[K, Tuple[float, V]]" = OrderedDict()
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self.expirations = 0

    # -- core operations ---------------------------------------------------

    def get(self, key: K):
        """Return the cached value or :data:`MISS`. Counts hits/misses."""
        if self._max <= 0:
            self.misses += 1
            return MISS
        item = self._data.get(key)
        if item is None:
            self.misses += 1
            return MISS
        stored_at, value = item
        if self._ttl is not None and (self._clock() - stored_at) >= self._ttl:
            # Expired: drop and report as a miss.
            del self._data[key]
            self.expirations += 1
            self.misses += 1
            return MISS
        self._data.move_to_end(key)  # mark most-recently-used
        self.hits += 1
        return value

    def set(self, key: K, value: V) -> None:
        """Insert/refresh an entry, evicting the LRU entry past the cap."""
        if self._max <= 0:
            return
        if key in self._data:
            self._data.move_to_end(key)
        self._data[key] = (self._clock(), value)
        while len(self._data) > self._max:
            self._data.popitem(last=False)  # evict least-recently-used
            self.evictions += 1

    def clear(self) -> None:
        """Drop all entries (counters are preserved for observability)."""
        self._data.clear()

    # -- introspection -----------------------------------------------------

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key: object) -> bool:
        return self.get(key) is not MISS

    @property
    def total(self) -> int:
        return self.hits + self.misses

    @property
    def hit_rate(self) -> float:
        total = self.total
        return (self.hits / total) if total else 0.0

    def stats(self) -> dict:
        """Snapshot of counters for diagnostics/logging."""
        return {
            "size": len(self._data),
            "max_entries": self._max,
            "ttl_seconds": self._ttl,
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "expirations": self.expirations,
            "hit_rate": round(self.hit_rate, 4),
        }
