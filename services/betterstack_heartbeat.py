"""
Better Stack cron/heartbeat monitor ping.

Unlike the Moddy Health Monitor (services/heartbeat.py, checks-driven,
JSON body, 20s), Better Stack's heartbeat contract is a bare URL: a plain
GET on the heartbeat URL means "alive", a GET on ``<url>/fail`` reports a
failure explicitly. No body is required either way — see
docs/HEALTH_MONITOR.md for the full contract and rationale.

Fire-and-forget like the other heartbeat: a failed push only logs, the loop
never blocks anything else and never stops itself.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Optional

import aiohttp

logger = logging.getLogger("moddy.services.betterstack_heartbeat")

IsHealthy = Callable[[], Awaitable[bool]]


class BetterStackHeartbeat:
    """Pings a Better Stack heartbeat monitor on a fixed interval.

    ``healthy`` is an optional coroutine returning whether the service is
    currently healthy; when it returns ``False`` the ping goes to
    ``<url>/fail`` instead of ``<url>``, reporting the failure explicitly
    rather than just going silent. With no ``healthy`` callback, every ping
    reports success (the monitor only cares that *some* request arrives
    within the expected frequency + grace period).
    """

    def __init__(
        self,
        *,
        url: str,
        healthy: Optional[IsHealthy] = None,
        interval: int = 180,
    ) -> None:
        self.url = (url or "").rstrip("/")
        self._healthy = healthy
        self._interval = interval
        self._task: Optional[asyncio.Task] = None
        self._session: Optional[aiohttp.ClientSession] = None

    def start(self) -> None:
        """Start the background ping loop. Safe to call once; a no-op
        (with a warning) when no heartbeat URL is configured."""
        if self._task is not None:
            return
        if not self.url:
            logger.warning("BETTERSTACK_HEARTBEAT_URL missing: Better Stack heartbeat disabled")
            return
        self._task = asyncio.create_task(self._loop(), name="betterstack-heartbeat")

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

    async def _loop(self) -> None:
        timeout = aiohttp.ClientTimeout(total=5)
        self._session = aiohttp.ClientSession(timeout=timeout)
        while True:
            try:
                healthy = await self._healthy() if self._healthy else True
                target = self.url if healthy else f"{self.url}/fail"
                async with self._session.get(target) as response:
                    if response.status >= 400:
                        logger.warning(
                            "Better Stack heartbeat rejected (HTTP %s)", response.status,
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Better Stack heartbeat failed: %s", exc)
            await asyncio.sleep(self._interval)
