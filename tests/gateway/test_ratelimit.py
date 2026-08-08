"""Provider-account rate limiter (gateway/ratelimit.py).

Pure logic over a fake Redis — no network, no Discord, no database.

    pytest tests/gateway -q
"""

from __future__ import annotations

import pytest

from gateway.errors import ModelRateLimitError
from gateway.ratelimit import (
    HOUR,
    MINUTE,
    RateLimiter,
    RateRule,
    UNIT_AUDIO_SECONDS,
    UNIT_REQUESTS,
)


class FakeRedis:
    """Minimal async Redis stand-in: INCRBYFLOAT / EXPIRE / GET, pipelined."""

    def __init__(self, broken: bool = False):
        self.kv: dict[str, float] = {}
        self.broken = broken

    async def get(self, key):
        if self.broken:
            raise RuntimeError("redis down")
        value = self.kv.get(key)
        return None if value is None else str(value)

    def pipeline(self):
        return _FakePipeline(self)


class _FakePipeline:
    def __init__(self, redis: FakeRedis):
        self._redis = redis
        self._ops: list = []

    def incrbyfloat(self, key, amount):
        self._ops.append(("incrbyfloat", key, amount))
        return self

    def expire(self, key, ttl):
        self._ops.append(("expire", key, ttl))
        return self

    async def execute(self):
        if self._redis.broken:
            raise RuntimeError("redis down")
        results = []
        for op in self._ops:
            if op[0] == "incrbyfloat":
                _, key, amount = op
                self._redis.kv[key] = round(self._redis.kv.get(key, 0.0) + amount, 6)
                results.append(self._redis.kv[key])
            else:
                results.append(True)
        return results


PROVIDER = "groq"
MODEL = "whisper-large-v3-turbo"


def make_limiter(redis, *rules):
    return RateLimiter(redis, {(PROVIDER, MODEL): list(rules)})


async def test_requests_under_the_limit_pass():
    redis = FakeRedis()
    limiter = make_limiter(redis, RateRule("rpm", UNIT_REQUESTS, MINUTE, 3))

    for _ in range(3):
        await limiter.acquire(PROVIDER, MODEL, {UNIT_REQUESTS: 1})

    usage = await limiter.snapshot(PROVIDER, MODEL)
    assert usage[0]["used"] == 3


async def test_request_over_the_limit_raises_and_does_not_consume():
    redis = FakeRedis()
    limiter = make_limiter(redis, RateRule("rpm", UNIT_REQUESTS, MINUTE, 2))

    await limiter.acquire(PROVIDER, MODEL, {UNIT_REQUESTS: 1})
    await limiter.acquire(PROVIDER, MODEL, {UNIT_REQUESTS: 1})

    with pytest.raises(ModelRateLimitError) as excinfo:
        await limiter.acquire(PROVIDER, MODEL, {UNIT_REQUESTS: 1})

    assert excinfo.value.rule == "rpm"
    assert excinfo.value.retry_after and excinfo.value.retry_after <= MINUTE
    # The refused call must leave the counter exactly where it was, otherwise a
    # burst of rejections would keep the window closed forever.
    usage = await limiter.snapshot(PROVIDER, MODEL)
    assert usage[0]["used"] == 2


async def test_a_breach_rolls_back_the_rules_already_debited():
    """A call is all-or-nothing across rules."""
    redis = FakeRedis()
    limiter = make_limiter(
        redis,
        RateRule("rpm", UNIT_REQUESTS, MINUTE, 10),
        RateRule("ash", UNIT_AUDIO_SECONDS, HOUR, 100),
    )

    await limiter.acquire(PROVIDER, MODEL, {UNIT_REQUESTS: 1, UNIT_AUDIO_SECONDS: 90})

    with pytest.raises(ModelRateLimitError):
        await limiter.acquire(PROVIDER, MODEL, {UNIT_REQUESTS: 1, UNIT_AUDIO_SECONDS: 30})

    usage = {rule["rule"]: rule["used"] for rule in await limiter.snapshot(PROVIDER, MODEL)}
    assert usage == {"rpm": 1, "ash": 90}


async def test_release_gives_every_unit_back():
    redis = FakeRedis()
    limiter = make_limiter(
        redis,
        RateRule("rpm", UNIT_REQUESTS, MINUTE, 10),
        RateRule("ash", UNIT_AUDIO_SECONDS, HOUR, 100),
    )

    reservation = await limiter.acquire(
        PROVIDER, MODEL, {UNIT_REQUESTS: 1, UNIT_AUDIO_SECONDS: 42}
    )
    await reservation.release()

    usage = {rule["rule"]: rule["used"] for rule in await limiter.snapshot(PROVIDER, MODEL)}
    assert usage == {"rpm": 0, "ash": 0}


async def test_reconcile_corrects_an_estimated_cost():
    """The duration is guessed before the call and known exactly after it."""
    redis = FakeRedis()
    limiter = make_limiter(redis, RateRule("ash", UNIT_AUDIO_SECONDS, HOUR, 1000))

    reservation = await limiter.acquire(PROVIDER, MODEL, {UNIT_AUDIO_SECONDS: 60})
    await reservation.reconcile({UNIT_AUDIO_SECONDS: 12.5})

    usage = await limiter.snapshot(PROVIDER, MODEL)
    assert usage[0]["used"] == pytest.approx(12.5)

    # Reconciling twice must not double-correct.
    await reservation.reconcile({UNIT_AUDIO_SECONDS: 12.5})
    usage = await limiter.snapshot(PROVIDER, MODEL)
    assert usage[0]["used"] == pytest.approx(12.5)


async def test_unlimited_and_unknown_models_are_not_gated():
    redis = FakeRedis()
    limiter = make_limiter(redis, RateRule("rpm", UNIT_REQUESTS, MINUTE, -1))

    for _ in range(50):
        await limiter.acquire(PROVIDER, MODEL, {UNIT_REQUESTS: 1})
    await limiter.acquire(PROVIDER, "some-other-model", {UNIT_REQUESTS: 1})

    assert redis.kv == {}


async def test_redis_failure_fails_open():
    """A limiter that cannot count must never block the feature."""
    limiter = make_limiter(FakeRedis(broken=True), RateRule("rpm", UNIT_REQUESTS, MINUTE, 1))

    for _ in range(5):
        reservation = await limiter.acquire(PROVIDER, MODEL, {UNIT_REQUESTS: 1})
        assert reservation.entries == []


async def test_no_redis_at_all_is_a_no_op():
    limiter = RateLimiter(None, {(PROVIDER, MODEL): [RateRule("rpm", UNIT_REQUESTS, MINUTE, 1)]})
    reservation = await limiter.acquire(PROVIDER, MODEL, {UNIT_REQUESTS: 1})
    assert reservation.entries == []
    await reservation.release()  # must not raise
