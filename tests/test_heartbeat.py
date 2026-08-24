"""Tests for the Moddy Health Monitor heartbeat (see docs/HEALTH_MONITOR.md).

Nothing here talks to the network: ``HeartbeatClient`` is tested on its pure
payload-building logic and its start()/stop() lifecycle, and the bot's own
``_build_heartbeat_checks`` is tested against a bare stand-in object so no
live Discord gateway is required.

    pytest tests/test_heartbeat.py -q
"""

import asyncio
from types import SimpleNamespace

import pytest

from services.heartbeat import HeartbeatClient


# --------------------------------------------------------------- HeartbeatClient

def test_payload_defaults_without_build():
    client = HeartbeatClient("moddy-api", url="https://hm.example", token="secret")
    payload = asyncio.run(client._payload())

    assert payload["service"] == "moddy-api"
    assert payload["status"] == "ok"
    assert payload["checks"] == {}
    assert payload["meta"] == {}
    assert isinstance(payload["uptime_s"], int)


def test_payload_uses_build_output():
    async def build():
        return {
            "status": "degraded",
            "checks": {"redis": {"ok": False}},
            "meta": {"workers": 4},
        }

    client = HeartbeatClient(
        "moddy-api", url="https://hm.example", token="secret",
        version="1.4.2", build=build,
    )
    payload = asyncio.run(client._payload())

    assert payload["status"] == "degraded"
    assert payload["version"] == "1.4.2"
    assert payload["checks"] == {"redis": {"ok": False}}
    assert payload["meta"] == {"workers": 4}


def test_start_is_noop_without_url_or_token():
    client = HeartbeatClient("moddy-api", url="", token="")

    async def run():
        client.start()
        assert client._task is None
        await client.stop()  # must not raise even though it never started

    asyncio.run(run())


def test_start_creates_task_when_configured():
    async def run():
        client = HeartbeatClient("moddy-api", url="https://hm.example", token="secret")
        client.start()
        assert client._task is not None
        await client.stop()
        assert client._task is None

    asyncio.run(run())


def test_incident_active_defaults_to_false():
    client = HeartbeatClient("moddy-api", url="https://hm.example", token="secret")
    assert client.incident_active is False


# --------------------------------------------------------------- bot checks

def _fake_bot(*, ready: bool, latency: float, guild_count: int = 3, shards=None):
    return SimpleNamespace(
        is_ready=lambda: ready,
        latency=latency,
        shards=shards or {},
        guilds=[object()] * guild_count,
    )


def test_build_bot_checks_down_when_not_ready():
    from bot import ModdyBot

    fake = _fake_bot(ready=False, latency=float("nan"))
    result = asyncio.run(ModdyBot._build_heartbeat_checks(fake))

    assert result["status"] == "down"
    assert result["checks"]["is_ready"]["ok"] is False
    assert result["checks"]["discord_gateway"]["latency_ms"] is None


def test_build_bot_checks_ok_when_ready():
    from bot import ModdyBot

    fake = _fake_bot(ready=True, latency=0.042)
    result = asyncio.run(ModdyBot._build_heartbeat_checks(fake))

    assert result["status"] == "ok"
    assert result["checks"]["discord_gateway"]["latency_ms"] == 42
    assert result["checks"]["shards"] == {"ok": True, "connected": 1, "total": 1}
    assert result["meta"]["guilds"] == 3


def test_build_bot_checks_degraded_when_a_shard_is_down():
    from bot import ModdyBot

    open_shard = SimpleNamespace(is_closed=lambda: False)
    closed_shard = SimpleNamespace(is_closed=lambda: True)
    fake = _fake_bot(ready=True, latency=0.01, shards={0: open_shard, 1: closed_shard})
    result = asyncio.run(ModdyBot._build_heartbeat_checks(fake))

    assert result["status"] == "degraded"
    assert result["checks"]["shards"] == {"ok": False, "connected": 1, "total": 2}
