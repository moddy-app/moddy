"""
Provider-side rate limiting (requests/minute, audio-seconds/hour, ...).

This is **not** the quota system. The two are complementary and answer
different questions:

- ``quota.py``  — "is this *guild* / *user* allowed to spend one more call
  today?" Daily counters, configured per entity in PostgreSQL.
- ``ratelimit.py`` — "would this call breach the limit *our provider account*
  is capped at?" Global, per ``(provider, model)``, several sliding windows at
  once, and metered in arbitrary units (requests, audio seconds, ...).

The limits mirror what the provider console exposes (e.g. Groq → *Configure
Model Limits*): requests per minute, requests per day, audio seconds per hour,
audio seconds per day. They live in :mod:`gateway.config` so they can be tuned
in code or through environment variables without touching this file.

Accounting is a fixed window per rule, stored in Redis so every bot shard sees
the same counters:

    ratelimit:{provider}:{model}:{rule}:{window_index}  →  float counter

A reservation is taken **before** the provider is contacted (atomic
``INCRBYFLOAT`` then check, rolling back if the increment breached the limit),
which makes bursts of concurrent calls safe. The reservation is released when
the call fails, and reconciled when the real cost is only known afterwards —
audio duration, for instance, is estimated from the file before the call and
returned exactly by the provider after it.

Redis failures fail **open**: a rate limiter that cannot read its counters must
never take a feature down.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .errors import ModelRateLimitError

logger = logging.getLogger("moddy.gateway.ratelimit")

# Units a rule can be metered in. Consumers pass a cost dict keyed by these.
UNIT_REQUESTS = "requests"
UNIT_AUDIO_SECONDS = "audio_seconds"

MINUTE = 60
HOUR = 3600
DAY = 86400


@dataclass(frozen=True)
class RateRule:
    """One provider limit: ``limit`` units of ``unit`` per ``window`` seconds."""

    name: str      # short id, used in the Redis key and in error messages
    unit: str      # UNIT_REQUESTS | UNIT_AUDIO_SECONDS
    window: int    # seconds
    limit: int     # -1 = unlimited

    @property
    def unlimited(self) -> bool:
        return self.limit < 0


@dataclass
class Reservation:
    """Units already debited for one in-flight call.

    Returned by :meth:`RateLimiter.acquire`. The executor releases it when the
    call fails and reconciles it when the provider reports the real cost.
    """

    limiter: Optional["RateLimiter"] = None
    # (redis key, amount, rule) triples, in the order they were debited
    entries: List[Tuple[str, float, RateRule]] = field(default_factory=list)

    async def release(self) -> None:
        """Give every debited unit back (the call never happened / failed)."""
        if self.limiter is None:
            return
        for key, amount, _rule in self.entries:
            await self.limiter._add(key, -amount)
        self.entries = []

    async def reconcile(self, actual: Optional[Dict[str, float]]) -> None:
        """Correct the debit once the real cost is known.

        Only ever adjusts by the delta; it never raises, because the call has
        already been made — over-shooting the window is accounted for, and the
        *next* call is the one that gets refused.
        """
        if self.limiter is None or not actual:
            return
        for index, (key, amount, rule) in enumerate(self.entries):
            real = actual.get(rule.unit)
            if real is None:
                continue
            delta = float(real) - amount
            if abs(delta) < 0.001:
                continue
            await self.limiter._add(key, delta)
            self.entries[index] = (key, float(real), rule)


class RateLimiter:
    """Fixed-window rate limiter over Redis, one window per :class:`RateRule`."""

    def __init__(self, redis, rules_by_model: Dict[Tuple[str, str], List[RateRule]]):
        self._redis = redis
        self._rules = rules_by_model

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def rules_for(self, provider: str, model: Optional[str]) -> List[RateRule]:
        """Rules configured for a ``(provider, model)`` pair (empty when none)."""
        if not model:
            return []
        return self._rules.get((provider, model), [])

    async def acquire(
        self,
        provider: str,
        model: Optional[str],
        cost: Dict[str, float],
    ) -> Reservation:
        """Debit ``cost`` against every matching rule, or raise.

        Raises:
            ModelRateLimitError: the first rule the call would breach. Nothing
                stays debited — every earlier rule of this call is rolled back.
        """
        reservation = Reservation(limiter=self)
        if not cost or self._redis is None:
            return reservation

        now = time.time()
        for rule in self.rules_for(provider, model):
            amount = float(cost.get(rule.unit, 0) or 0)
            if rule.unlimited or amount <= 0:
                continue

            key = self._key(provider, model, rule, now)
            total = await self._add(key, amount, ttl=rule.window * 2)
            if total is None:
                continue  # Redis unavailable — fail open, see module docstring

            if total > rule.limit:
                await self._add(key, -amount)
                await reservation.release()
                raise ModelRateLimitError(
                    provider=provider,
                    model=model or "",
                    rule=rule.name,
                    limit=rule.limit,
                    retry_after=self._seconds_to_reset(rule, now),
                )

            reservation.entries.append((key, amount, rule))

        return reservation

    async def snapshot(self, provider: str, model: Optional[str]) -> List[dict]:
        """Current usage per rule — for staff diagnostics and `/status` style views."""
        now = time.time()
        out: List[dict] = []
        for rule in self.rules_for(provider, model):
            used = 0.0
            if self._redis is not None:
                try:
                    raw = await self._redis.get(self._key(provider, model, rule, now))
                    used = float(raw) if raw else 0.0
                except Exception as exc:  # noqa: BLE001 — diagnostics only
                    logger.debug("Rate limit snapshot failed for %s: %s", rule.name, exc)
            out.append({
                "rule": rule.name,
                "unit": rule.unit,
                "window": rule.window,
                "limit": rule.limit,
                "used": used,
                "resets_in": self._seconds_to_reset(rule, now),
            })
        return out

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    @staticmethod
    def _window_index(rule: RateRule, now: float) -> int:
        return int(now // rule.window)

    @staticmethod
    def _seconds_to_reset(rule: RateRule, now: float) -> float:
        return round(rule.window - (now % rule.window), 3)

    def _key(self, provider: str, model: Optional[str], rule: RateRule, now: float) -> str:
        return (
            f"ratelimit:{provider}:{model or '-'}:{rule.name}:"
            f"{self._window_index(rule, now)}"
        )

    async def _add(self, key: str, amount: float, ttl: Optional[int] = None) -> Optional[float]:
        """Atomically add ``amount`` to a counter. Returns the new total, or None."""
        if self._redis is None:
            return None
        try:
            pipe = self._redis.pipeline()
            pipe.incrbyfloat(key, amount)
            if ttl:
                pipe.expire(key, ttl)
            results = await pipe.execute()
            return float(results[0])
        except Exception as exc:  # noqa: BLE001 — fail open
            logger.warning("Rate limit counter %s unavailable: %s", key, exc)
            return None
