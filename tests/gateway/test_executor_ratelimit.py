"""The executor's rate-limit reservation lifecycle.

A reservation is taken before the provider is contacted; it must be given back
when the call fails, and corrected when the provider reports what the call
really cost. Both are what keeps the audio-seconds budget honest.
"""

from __future__ import annotations

import pytest

from gateway.adapters.base import AdapterResult
from gateway.config import GatewayConfig
from gateway.errors import ProviderError
from gateway.executor import GatewayExecutor
from gateway.ratelimit import (
    HOUR, MINUTE, RateLimiter, RateRule, UNIT_AUDIO_SECONDS, UNIT_REQUESTS,
)
from gateway.resilience import CircuitBreaker
from gateway.spec import CallSpec

from tests.gateway.test_ratelimit import FakeRedis

PROVIDER = "groq"
MODEL = "whisper-large-v3-turbo"


class StubAdapter:
    """Returns a fixed result, or raises, on demand."""

    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = 0

    async def execute(self, spec):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


class NoQuota:
    async def check_all(self, plan):
        pass

    async def consume_all(self, plan):
        pass


class NoLog:
    async def record(self, *args, **kwargs):
        pass


def build(adapter, redis):
    limiter = RateLimiter(redis, {
        (PROVIDER, MODEL): [
            RateRule("rpm", UNIT_REQUESTS, MINUTE, 20),
            RateRule("ash", UNIT_AUDIO_SECONDS, HOUR, 7_200),
        ],
    })
    executor = GatewayExecutor(
        adapters={PROVIDER: adapter},
        quota=NoQuota(),
        circuit_breaker=CircuitBreaker(),
        gw_logger=NoLog(),
        config=GatewayConfig(max_retries=0),
        ratelimiter=limiter,
    )
    return executor, limiter


def spec(duration_hint: float) -> CallSpec:
    return CallSpec(
        provider=PROVIDER,
        operation="transcribe",
        model=MODEL,
        payload={"filename": "voice-message.ogg"},
        quota=[],
        call_type="voice_transcription",
        correlation_id="test",
        rate_cost={UNIT_REQUESTS: 1, UNIT_AUDIO_SECONDS: duration_hint},
        binary=b"audio",
    )


async def usage(limiter):
    return {rule["rule"]: rule["used"] for rule in await limiter.snapshot(PROVIDER, MODEL)}


async def test_a_successful_call_is_billed_at_its_real_duration():
    adapter = StubAdapter(AdapterResult(
        data={"text": "hello", "duration": 8.0},
        rate_cost={UNIT_REQUESTS: 1, UNIT_AUDIO_SECONDS: 8.0},
    ))
    executor, limiter = build(adapter, FakeRedis())

    # The estimate said 60s of audio; the provider measured 8.
    result = await executor.execute(spec(60.0))

    assert result["text"] == "hello"
    assert await usage(limiter) == {"rpm": 1, "ash": pytest.approx(8.0)}


async def test_a_failed_call_costs_nothing():
    adapter = StubAdapter(error=ProviderError(PROVIDER, 500, "boom"))
    executor, limiter = build(adapter, FakeRedis())

    with pytest.raises(ProviderError):
        await executor.execute(spec(30.0))

    assert await usage(limiter) == {"rpm": 0, "ash": 0}
