"""
The mini-bots process: N lean Discord clients on one event loop.

- **Supervision**: each client runs in its own task; a crash restarts that
  client with exponential backoff and never touches the others. A rejected
  token stops retrying (it will not fix itself).
- **One task per periodic job** for all bots (:meth:`register_periodic`), not
  one loop per bot.
- **Status heartbeat** to Redis (``minibots:status``) for ``/dev minibots``.
- **Control channel** (``minibots:control``): release a module before Moddy
  takes it over, reload a guild, restart an app.

Mini-bots have no dashboard (docs/features/MINIBOTS.md): the runtime does NOT
listen to ``moddy:bot`` dashboard pushes. Their configuration happens in
Discord, through each bot's own commands.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

from minibots import status as mstatus
from minibots.spec import MiniBotSpec

logger = logging.getLogger("moddy.minibots")

BACKOFF_START = 5
BACKOFF_MAX = 300


def process_rss_mb() -> Optional[float]:
    """Resident memory of this process (Linux /proc, no psutil needed)."""
    try:
        with open("/proc/self/statm") as fh:
            pages = int(fh.read().split()[1])
        return round(pages * os.sysconf("SC_PAGE_SIZE") / 1024 / 1024, 1)
    except (OSError, ValueError, IndexError):
        return None


class _AppState:
    def __init__(self, spec: MiniBotSpec):
        self.spec = spec
        self.client = None
        self.task: Optional[asyncio.Task] = None
        self.state = mstatus.STATE_STOPPED
        self.started_at: Optional[float] = None
        self.restarts = 0
        self.last_error: Optional[str] = None
        self.restart_requested = False


class MiniBotRuntime:
    def __init__(self, specs: Tuple[MiniBotSpec, ...], resources,
                 client_factory: Optional[Callable] = None):
        self.resources = resources
        self.apps: Dict[str, _AppState] = {spec.id: _AppState(spec) for spec in specs}
        self.started_at = time.time()
        self._client_factory = client_factory
        self._tasks: List[asyncio.Task] = []
        self._periodic: List[Tuple[str, float, Callable[["MiniBotRuntime"], Awaitable]]] = []
        self._stopping = False

    # ------------------------------------------------------------ public API

    def client_for(self, app: str):
        state = self.apps.get(app)
        return state.client if state else None

    def clients(self):
        return [s.client for s in self.apps.values() if s.client is not None]

    def register_periodic(self, name: str, interval: float,
                          job: Callable[["MiniBotRuntime"], Awaitable]) -> None:
        """Run ``job(runtime)`` every ``interval`` seconds — ONE task for every
        bot. The job dispatches to the right client via :meth:`client_for`."""
        self._periodic.append((name, interval, job))
        if self._tasks:  # already started
            self._tasks.append(asyncio.create_task(self._run_periodic(name, interval, job)))

    async def start(self) -> None:
        await self._publish_registry()
        for app, state in self.apps.items():
            if not state.spec.token:
                state.state = mstatus.STATE_NO_TOKEN
                logger.info("Mini-bot %s: no token (%s), not started", app, state.spec.token_env)
                continue
            state.task = asyncio.create_task(self._supervise(state), name=f"minibot:{app}")
        self._tasks.append(asyncio.create_task(self._heartbeat_loop(), name="minibots:heartbeat"))
        if self.resources.redis is not None:
            self._tasks.append(asyncio.create_task(self._control_loop(), name="minibots:control"))
        for name, interval, job in self._periodic:
            self._tasks.append(asyncio.create_task(self._run_periodic(name, interval, job)))
        running = sum(1 for s in self.apps.values() if s.task is not None)
        logger.info("Mini-bots runtime started: %d mini-bot(s), %d running", len(self.apps), running)

    async def stop(self) -> None:
        self._stopping = True
        for state in self.apps.values():
            if state.client is not None and not state.client.is_closed():
                try:
                    await state.client.close()
                except Exception:  # noqa: BLE001
                    pass
            if state.task is not None:
                state.task.cancel()
            if state.state != mstatus.STATE_NO_TOKEN:
                state.state = mstatus.STATE_STOPPED
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, *(s.task for s in self.apps.values() if s.task),
                             return_exceptions=True)
        await self._write_status()
        logger.info("Mini-bots runtime stopped")

    # ------------------------------------------------------------ supervision

    def _make_client(self, spec: MiniBotSpec):
        if self._client_factory is not None:
            return self._client_factory(spec, self.resources, self)
        from minibots.client import MiniBotClient
        return MiniBotClient(spec, self.resources, self)

    async def _supervise(self, state: _AppState) -> None:
        import discord
        backoff = BACKOFF_START
        while not self._stopping:
            client = self._make_client(state.spec)
            state.client = client
            state.state = mstatus.STATE_CONNECTING
            state.restart_requested = False
            try:
                await client.start(state.spec.token)
            except discord.LoginFailure as exc:
                state.state = mstatus.STATE_CRASHED
                state.last_error = f"login: {exc}"[:200]
                await self._lifecycle(state, "crashed", [f"**Token rejected** `{exc}`",
                                                          "No retry until the token is fixed."])
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — supervised
                state.state = mstatus.STATE_CRASHED
                state.last_error = f"{type(exc).__name__}: {exc}"[:200]
                logger.error("Mini-bot %s crashed: %s", state.spec.id, exc, exc_info=True)
                await self._lifecycle(state, "crashed", [f"**Error** `{state.last_error}`",
                                                          f"**Retry in** `{backoff}s`"])
            finally:
                if not client.is_closed():
                    try:
                        await client.close()
                    except Exception:  # noqa: BLE001
                        pass
            if self._stopping:
                break
            state.restarts += 1
            if state.restart_requested:
                backoff = BACKOFF_START
                await self._lifecycle(state, "restarted", ["Requested by staff."])
                continue
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)

    async def on_client_ready(self, client) -> None:
        state = self.apps.get(client.app_id)
        if state is None:
            return
        first = state.started_at is None
        state.state = mstatus.STATE_READY
        state.started_at = state.started_at or time.time()
        await self._write_status()
        await self._lifecycle(state, "ready" if first else "reconnected", [
            f"**User** `{client.user}` `{getattr(client.user, 'id', '?')}`",
            f"**Guilds** `{len(client.guilds)}` • **Modules** `{', '.join(state.spec.modules)}`",
        ])

    async def _lifecycle(self, state: _AppState, event: str, lines: List[str]) -> None:
        tech = self.resources.tech_logger
        if tech is not None:
            await tech.log_minibot_lifecycle(state.spec.id, event, lines)

    # --------------------------------------------------------------- status

    def app_status(self, state: _AppState) -> mstatus.AppStatus:
        client = state.client
        ready = client is not None and client.is_ready()
        latency = None
        if ready:
            try:
                latency = int(client.latency * 1000)
            except (TypeError, ValueError, OverflowError):
                latency = None
        return mstatus.AppStatus(
            app=state.spec.id, state=state.state,
            guilds=len(client.guilds) if ready else 0,
            latency_ms=latency, started_at=state.started_at,
            restarts=state.restarts, last_error=state.last_error,
        )

    async def _write_status(self) -> None:
        redis = self.resources.redis
        if redis is None:
            return
        mapping = {app: self.app_status(state).to_json() for app, state in self.apps.items()}
        mapping[mstatus.PROCESS_FIELD] = mstatus.ProcessStatus(
            rss_mb=process_rss_mb(), started_at=self.started_at,
            version=self.resources.version, apps=len(self.apps),
        ).to_json()
        try:
            await redis.hset(mstatus.STATUS_KEY, mapping=mapping)
            await redis.expire(mstatus.STATUS_KEY, mstatus.STALE_AFTER * 3)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Mini-bots status write failed: %s", exc)

    async def _publish_registry(self) -> None:
        redis = self.resources.redis
        if redis is None or not self.apps:
            return
        try:
            await redis.hset(mstatus.APPS_KEY, mapping={
                app: json.dumps(state.spec.to_public_dict(), separators=(",", ":"))
                for app, state in self.apps.items()
            })
        except Exception as exc:  # noqa: BLE001
            logger.debug("Mini-bots registry write failed: %s", exc)

    async def _heartbeat_loop(self) -> None:
        while True:
            await self._write_status()
            await asyncio.sleep(mstatus.HEARTBEAT_SECONDS)

    async def _run_periodic(self, name: str, interval: float, job) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await job(self)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — a job never kills the runtime
                logger.error("Mini-bots periodic job %s failed: %s", name, exc, exc_info=True)

    # -------------------------------------------------------------- control

    async def _control_loop(self) -> None:
        while True:
            try:
                pubsub = self.resources.redis.pubsub()
                await pubsub.subscribe(mstatus.CONTROL_CHANNEL)
                async for message in pubsub.listen():
                    if message.get("type") != "message":
                        continue
                    data = mstatus.parse_control(message.get("data"))
                    if data is not None:
                        asyncio.create_task(self.handle_control(data))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.error("Mini-bots control channel error: %s", exc)
                await asyncio.sleep(5)

    async def handle_control(self, data: dict) -> None:
        kind = data.get("type")
        app = data.get("app")
        try:
            if kind == mstatus.CTRL_RELEASE:
                await self._handle_release(data)
            elif kind == mstatus.CTRL_RELOAD_GUILD:
                guild_id = int(data.get("guild_id"))
                for client in self.clients():
                    if app in (None, "*", client.app_id) and client.get_guild(guild_id):
                        await client.module_manager.unload_guild_modules(guild_id)
            elif kind == mstatus.CTRL_RESTART:
                state = self.apps.get(app)
                if state is not None and state.client is not None:
                    state.restart_requested = True
                    await state.client.close()
        except Exception as exc:  # noqa: BLE001
            logger.error("Mini-bots control %s failed: %s", kind, exc, exc_info=True)

    async def _handle_release(self, data: dict) -> None:
        """Let go of a module Moddy is taking over, then ack.

        No ack when the owning client is not connected: it could not take its
        panel down, so Moddy's fallback clean-up has to run instead.
        """
        client = self.client_for(data.get("app"))
        if client is None or not client.is_ready():
            return
        guild_id = int(data["guild_id"])
        recap = await client.module_manager.release_module(
            guild_id, str(data["module_id"]), str(data.get("to_app") or "moddy"))
        redis = self.resources.redis
        key = mstatus.ack_key(str(data["request_id"]))
        await redis.rpush(key, json.dumps(recap, default=str))
        await redis.expire(key, mstatus.ACK_TTL)
