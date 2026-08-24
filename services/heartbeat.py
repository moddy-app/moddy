"""
Heartbeat to the Moddy Health Monitor (see docs/HEALTH_MONITOR.md).

The monitor never polls a service for its state: each service pushes its own
state every 20 seconds, and silence *is* the signal (dead man's switch — the
monitor's TTL is 60s, three missed heartbeats). This client is fire-and-forget
and must never become part of a critical path: a failed push only logs, the
loop just tries again on the next interval.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Dict, Optional

import aiohttp

logger = logging.getLogger("moddy.services.heartbeat")

BuildChecks = Callable[[], Awaitable[Dict[str, Any]]]


class HeartbeatClient:
    """Pushes a periodic heartbeat to the Moddy Health Monitor.

    ``build`` is an optional coroutine returning
    ``{"status": ..., "checks": {...}, "meta": {...}}`` — the caller decides
    what its own dependencies mean (a dead vital dependency is ``down``, a
    dead secondary one is ``degraded``). The monitor itself never interprets
    ``checks`` keys; it only renders them.
    """

    def __init__(
        self,
        service: str,
        *,
        url: str,
        token: str,
        version: str = "0.0.0",
        build: Optional[BuildChecks] = None,
        interval: int = 20,
    ) -> None:
        self.service = service
        self.url = (url or "").rstrip("/")
        self.token = token or ""
        self.version = version
        self._build = build
        self._interval = interval
        self._started = time.monotonic()
        self._task: Optional[asyncio.Task] = None
        self._session: Optional[aiohttp.ClientSession] = None
        # Set from the monitor's response on every heartbeat: lets a service
        # cut non-critical notifications / heavy background work / aggressive
        # retries while an incident is active. Purely optional to consume.
        self.incident_active = False

    def start(self) -> None:
        """Start the background heartbeat loop. Safe to call once; a no-op
        (with a warning) when HM_URL or HM_INGEST_TOKEN is not configured."""
        if self._task is not None:
            return
        if not self.url or not self.token:
            logger.warning(
                "HM_URL or HM_INGEST_TOKEN missing: heartbeat disabled for %s",
                self.service,
            )
            return
        self._task = asyncio.create_task(self._loop(), name=f"heartbeat:{self.service}")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _payload(self) -> Dict[str, Any]:
        extra = await self._build() if self._build else {}
        return {
            "service": self.service,
            "status": extra.get("status", "ok"),
            "version": self.version,
            "uptime_s": int(time.monotonic() - self._started),
            "checks": extra.get("checks", {}),
            "meta": extra.get("meta", {}),
        }

    async def _loop(self) -> None:
        timeout = aiohttp.ClientTimeout(total=5)
        self._session = aiohttp.ClientSession(timeout=timeout)
        while True:
            try:
                payload = await self._payload()
                async with self._session.post(
                    f"{self.url}/ingest/heartbeat",
                    json=payload,
                    headers={"X-Health-Token": self.token},
                ) as response:
                    if response.status < 400:
                        data = await response.json()
                        self.incident_active = bool(data.get("incident_active"))
                    else:
                        logger.warning(
                            "Heartbeat rejected for %s (HTTP %s)",
                            self.service, response.status,
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Heartbeat failed for %s: %s", self.service, exc)
            await asyncio.sleep(self._interval)
