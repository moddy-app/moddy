"""Google Vision provider + the calendar-month, fail-closed rate rules.

Pure logic — no network: the request body builder, the response parsers, the
monthly window (Pacific billing clock) and the fail-closed behaviour when Redis
cannot be read.
"""

from __future__ import annotations

import base64
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from gateway.adapters.google_vision import (
    build_request, parse_document_text, parse_safe_search,
)
from gateway.config import GatewayConfig
from gateway.errors import ConfigurationError, ModelRateLimitError
from gateway.ratelimit import (
    CALENDAR_MONTH, MONTH, RateLimiter, RateRule, UNIT_REQUESTS, month_bounds,
)

from tests.gateway.test_ratelimit import FakeRedis

LA = ZoneInfo("America/Los_Angeles")


def _ts(*args) -> float:
    return datetime(*args, tzinfo=LA).timestamp()


def _monthly(limit: int = 3) -> RateRule:
    return RateRule("rpmo", UNIT_REQUESTS, MONTH, limit,
                    calendar=CALENDAR_MONTH, fail_closed=True)


class TestRequestBody:
    def test_safe_search_body_embeds_the_image_as_base64(self):
        body = build_request("safe_search", b"\x89PNG")
        req = body["requests"][0]
        assert req["features"] == [{"type": "SAFE_SEARCH_DETECTION"}]
        assert base64.b64decode(req["image"]["content"]) == b"\x89PNG"

    def test_document_text_carries_language_hints(self):
        body = build_request("document_text", b"x", language_hints=["fr"])
        req = body["requests"][0]
        assert req["features"] == [{"type": "DOCUMENT_TEXT_DETECTION"}]
        assert req["imageContext"] == {"languageHints": ["fr"]}

    def test_unknown_operation_is_refused(self):
        with pytest.raises(ConfigurationError):
            build_request("label_detection", b"x")


class TestParsers:
    def test_safe_search_normalises_unknown_values(self):
        out = parse_safe_search({"safeSearchAnnotation": {
            "adult": "VERY_LIKELY", "racy": "LIKELY", "violence": "bogus"}})
        assert out == {"adult": "VERY_LIKELY", "racy": "LIKELY", "violence": "UNKNOWN",
                       "medical": "UNKNOWN", "spoof": "UNKNOWN"}

    def test_document_text_reads_full_text_and_language(self):
        out = parse_document_text({"fullTextAnnotation": {
            "text": "Withdrawal Success!\n",
            "pages": [{"property": {"detectedLanguages": [{"languageCode": "en"}]}}],
        }})
        assert out == {"text": "Withdrawal Success!", "language": "en"}

    def test_document_text_falls_back_to_text_annotations(self):
        out = parse_document_text({"textAnnotations": [
            {"description": "Bonjour", "locale": "fr"}]})
        assert out == {"text": "Bonjour", "language": "fr"}

    def test_empty_response_is_empty_text(self):
        assert parse_document_text({}) == {"text": "", "language": None}


class TestCalendarMonth:
    def test_month_bounds_follow_pacific_time(self):
        start, end = month_bounds(_ts(2026, 9, 15, 12))
        assert start == _ts(2026, 9, 1)
        assert end == _ts(2026, 10, 1)

    def test_december_rolls_into_january(self):
        _start, end = month_bounds(_ts(2026, 12, 31, 23))
        assert end == _ts(2027, 1, 1)

    def test_window_index_is_the_pacific_month(self):
        rule = _monthly()
        # 2026-10-01 03:00 UTC is still September in Los Angeles.
        utc_oct_first = datetime(2026, 10, 1, 3, tzinfo=ZoneInfo("UTC")).timestamp()
        assert RateLimiter._window_index(rule, utc_oct_first) == 202609
        assert RateLimiter._window_index(rule, _ts(2026, 10, 1, 1)) == 202610

    async def test_monthly_cap_refuses_past_the_allowance(self):
        limiter = RateLimiter(FakeRedis(), {("google_vision", "safe_search"): [_monthly(2)]})
        for _ in range(2):
            await limiter.acquire("google_vision", "safe_search", {UNIT_REQUESTS: 1})
        with pytest.raises(ModelRateLimitError) as exc:
            await limiter.acquire("google_vision", "safe_search", {UNIT_REQUESTS: 1})
        assert exc.value.retry_after > 0

    async def test_fail_closed_when_redis_is_broken(self):
        limiter = RateLimiter(FakeRedis(broken=True),
                              {("google_vision", "safe_search"): [_monthly()]})
        with pytest.raises(ModelRateLimitError):
            await limiter.acquire("google_vision", "safe_search", {UNIT_REQUESTS: 1})

    async def test_fail_closed_without_redis_at_all(self):
        limiter = RateLimiter(None, {("google_vision", "safe_search"): [_monthly()]})
        with pytest.raises(ModelRateLimitError):
            await limiter.acquire("google_vision", "safe_search", {UNIT_REQUESTS: 1})

    async def test_ordinary_rules_still_fail_open_without_redis(self):
        rule = RateRule("rpm", UNIT_REQUESTS, 60, 1)
        limiter = RateLimiter(None, {("openai", "gpt-4.1-nano"): [rule]})
        await limiter.acquire("openai", "gpt-4.1-nano", {UNIT_REQUESTS: 1})


def test_default_config_caps_both_vision_features_monthly():
    rules = GatewayConfig().model_rate_limits
    for model in ("safe_search", "document_text"):
        (rule,) = rules[("google_vision", model)]
        assert rule.calendar == CALENDAR_MONTH
        assert rule.fail_closed
        assert rule.limit == 1000


class _CaptureExecutor:
    def __init__(self, result):
        self.result = result
        self.spec = None

    async def execute(self, spec):
        self.spec = spec
        return self.result


async def test_vision_clients_keep_image_bytes_out_of_the_logged_payload():
    from gateway.clients.ai import AIClient
    from gateway.clients.vision import VisionClient

    image = b"\xff\xd8" + b"A" * 5000
    ex = _CaptureExecutor("text")
    await AIClient(ex).vision(image=image, mime="image/jpeg", prompt="ocr",
                              quota=[], call_type="automod_image_ocr")
    assert ex.spec.binary == image
    assert ex.spec.operation == "vision"
    assert base64.b64encode(image).decode()[:40] not in repr(ex.spec.payload)

    ex = _CaptureExecutor({"adult": "LIKELY"})
    out = await VisionClient(ex).safe_search(image, mime="image/jpeg")
    assert out.adult == "LIKELY" and out.racy == "UNKNOWN"
    assert ex.spec.model == "safe_search" and ex.spec.binary == image
    assert "content" not in ex.spec.payload
