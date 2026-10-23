"""OpenAI request translation for reasoning models + the gateway log buffer.

Pure logic — no network: the Chat Completions body sent for gpt-5-family
models, and the in-memory log buffer used when Redis is not configured.
"""

from __future__ import annotations

from gateway.adapters.openai import DEFAULT_CHAT_MODEL, _adapt_params
from gateway.config import GatewayConfig
from gateway.logger import GatewayLogger


class TestAdaptParams:
    def test_default_model_is_not_the_shut_down_nano(self):
        assert DEFAULT_CHAT_MODEL != "gpt-4.1-nano"

    def test_reasoning_model_gets_its_own_knobs(self):
        body = _adapt_params("gpt-5.6-luna", {
            "model": "gpt-5.6-luna", "messages": [],
            "temperature": 0.7, "max_tokens": 300,
        })
        assert "temperature" not in body
        assert "max_tokens" not in body
        assert body["max_completion_tokens"] == 300
        assert body["reasoning_effort"] == "none"

    def test_explicit_reasoning_effort_is_kept(self):
        body = _adapt_params("gpt-5.6-luna", {"reasoning_effort": "low"})
        assert body["reasoning_effort"] == "low"

    def test_classic_model_is_untouched(self):
        body = {"model": "gpt-4.1-mini", "temperature": 0.3, "max_tokens": 150}
        assert _adapt_params("gpt-4.1-mini", dict(body)) == body


class _FakeConn:
    def __init__(self, sink, fail):
        self._sink, self._fail = sink, fail

    async def executemany(self, _query, rows):
        if self._fail:
            raise RuntimeError("pg down")
        self._sink.extend(rows)


class _FakePool:
    def __init__(self, fail=False):
        self.rows: list = []
        self.fail = fail

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return _FakeConn(pool.rows, pool.fail)

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


def _entry(i: int) -> dict:
    return {
        "correlation_id": str(i), "call_type": "t", "provider": "openai",
        "operation": "chat", "success": True,
        "ts": "2026-10-23T17:00:00+00:00",
    }


class TestLoggerWithoutRedis:
    async def test_flushes_from_memory(self):
        pool = _FakePool()
        log = GatewayLogger(None, pool, GatewayConfig())
        log._local.extend(_entry(i) for i in range(3))
        await log._do_flush()
        assert [r[0] for r in pool.rows] == ["0", "1", "2"]
        assert not log._local

    async def test_failed_write_is_requeued_in_order(self):
        pool = _FakePool(fail=True)
        log = GatewayLogger(None, pool, GatewayConfig())
        log._local.extend(_entry(i) for i in range(3))
        await log._do_flush()
        assert [e["correlation_id"] for e in log._local] == ["0", "1", "2"]
