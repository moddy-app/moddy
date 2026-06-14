"""
Feeds service client — Redis transport for the ``moddy-feeds`` integration.

This is the ONLY contract between the bot and the ``moddy-feeds`` service. All
communication goes through three shared Redis streams (see
docs/SOCIAL_NOTIFICATIONS.md):

  - ``feeds:commands``      bot  -> service   (subscribe / unsubscribe)
  - ``feeds:replies``       service -> bot    (correlated by ``request_id``)
  - ``notifications:queue`` service -> bot    (normalized notification events)

The bot NEVER touches the service's database directly. This client only knows
how to push commands, await correlated replies, and consume the notification
queue via a consumer group.
"""

import asyncio
import json
import logging
import os
import socket
from typing import Any, Awaitable, Callable, Dict, Optional
from uuid import uuid4

logger = logging.getLogger('moddy.services.feeds')

COMMANDS_STREAM = "feeds:commands"
REPLIES_STREAM = "feeds:replies"
QUEUE_STREAM = "notifications:queue"
HEARTBEAT_KEY = "feeds:heartbeat"

QUEUE_GROUP = "discord-bot"

DEFAULT_REPLY_TIMEOUT = 10  # seconds (per integration contract)

# Type alias for the async callback invoked for every notification event.
QueueHandler = Callable[[Dict[str, Any]], Awaitable[None]]


class FeedsClient:
    """Async client that bridges the bot and the moddy-feeds service over Redis."""

    def __init__(self, bot):
        self.bot = bot
        # Correlated reply futures keyed by request_id.
        self._pending: Dict[str, asyncio.Future] = {}
        self._reply_task: Optional[asyncio.Task] = None
        self._queue_task: Optional[asyncio.Task] = None
        self._consumer_name = f"bot-{socket.gethostname()}-{os.getpid()}"
        self._started = False

    @property
    def redis(self):
        return getattr(self.bot, "redis", None)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    async def start(self, queue_handler: QueueHandler) -> None:
        """Start the reply reader and the notification queue consumer.

        Safe to call once; no-op if Redis is unavailable.
        """
        if self._started:
            return
        if not self.redis:
            logger.warning("[Feeds] Redis unavailable — feeds client disabled")
            return

        self._started = True
        self._reply_task = asyncio.create_task(self._reply_reader())
        self._queue_task = asyncio.create_task(self._queue_consumer(queue_handler))
        logger.info("[Feeds] Feeds client started")

    async def stop(self) -> None:
        """Cancel background tasks (called on bot shutdown)."""
        for task in (self._reply_task, self._queue_task):
            if task and not task.done():
                task.cancel()
        self._started = False

    # ------------------------------------------------------------------ #
    # Commands (subscribe / unsubscribe)
    # ------------------------------------------------------------------ #
    async def subscribe(
        self, platform: str, identifier: str, poll_interval: Optional[int] = None
    ) -> Dict[str, Any]:
        """Subscribe to a target. Returns the service reply dict (see contract)."""
        return await self._send_command("subscribe", platform, identifier, poll_interval)

    async def unsubscribe(
        self, platform: str, identifier: str, poll_interval: Optional[int] = None
    ) -> Dict[str, Any]:
        """Unsubscribe from a target.

        Per the contract: omit ``poll_interval`` to fully remove the target (no
        guild left); pass it to keep the target alive with a relaxed interval.
        """
        return await self._send_command("unsubscribe", platform, identifier, poll_interval)

    async def _send_command(
        self,
        action: str,
        platform: str,
        identifier: str,
        poll_interval: Optional[int],
        timeout: int = DEFAULT_REPLY_TIMEOUT,
    ) -> Dict[str, Any]:
        if not self.redis:
            return {"ok": False, "error": "service_unavailable"}

        request_id = str(uuid4())
        payload: Dict[str, Any] = {
            "request_id": request_id,
            "action": action,
            "platform": platform,
            "identifier": identifier,
        }
        if poll_interval is not None:
            payload["poll_interval"] = poll_interval

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending[request_id] = future

        try:
            await self.redis.xadd(COMMANDS_STREAM, {"data": json.dumps(payload)})
        except Exception as e:
            self._pending.pop(request_id, None)
            logger.error(f"[Feeds] Failed to publish command: {e}")
            return {"ok": False, "error": "service_unavailable"}

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(f"[Feeds] Timeout waiting for reply ({action} {platform} {identifier})")
            return {"ok": False, "error": "timeout"}
        finally:
            self._pending.pop(request_id, None)

    async def _reply_reader(self) -> None:
        """Continuously read ``feeds:replies`` and resolve correlated futures."""
        last_id = "$"  # only replies produced after we start listening
        while True:
            try:
                messages = await self.redis.xread({REPLIES_STREAM: last_id}, block=5000, count=20)
                if not messages:
                    continue
                for _stream, entries in messages:
                    for entry_id, fields in entries:
                        last_id = entry_id
                        self._resolve_reply(fields)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"[Feeds] Reply reader error: {e}")
                await asyncio.sleep(2)

    def _resolve_reply(self, fields: Dict[str, Any]) -> None:
        try:
            data = json.loads(fields.get("data", "{}"))
        except (json.JSONDecodeError, TypeError):
            return
        request_id = data.get("request_id")
        if not request_id:
            return
        future = self._pending.get(request_id)
        if future and not future.done():
            future.set_result(data)

    # ------------------------------------------------------------------ #
    # Notification queue
    # ------------------------------------------------------------------ #
    async def _queue_consumer(self, handler: QueueHandler) -> None:
        """Consume ``notifications:queue`` via a consumer group and dispatch."""
        # Ensure the consumer group exists (idempotent).
        try:
            await self.redis.xgroup_create(QUEUE_STREAM, QUEUE_GROUP, id="0", mkstream=True)
        except Exception as e:
            # BUSYGROUP means the group already exists — fine.
            if "BUSYGROUP" not in str(e):
                logger.error(f"[Feeds] Could not create consumer group: {e}")

        while True:
            try:
                resp = await self.redis.xreadgroup(
                    QUEUE_GROUP,
                    self._consumer_name,
                    {QUEUE_STREAM: ">"},
                    count=50,
                    block=5000,
                )
                for _stream, messages in resp or []:
                    for msg_id, fields in messages:
                        try:
                            event = json.loads(fields.get("data", "{}"))
                            await handler(event)
                        except Exception as e:
                            logger.error(f"[Feeds] Error handling notification {msg_id}: {e}", exc_info=True)
                        finally:
                            # Always ack: the service guarantees dedup, so we never
                            # want a poison event stuck at the head of the queue.
                            try:
                                await self.redis.xack(QUEUE_STREAM, QUEUE_GROUP, msg_id)
                            except Exception:
                                pass
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"[Feeds] Queue consumer error: {e}")
                await asyncio.sleep(2)

    # ------------------------------------------------------------------ #
    # Healthcheck
    # ------------------------------------------------------------------ #
    async def is_service_alive(self) -> bool:
        """Return True if the feeds service heartbeat key is present."""
        if not self.redis:
            return False
        try:
            return bool(await self.redis.exists(HEARTBEAT_KEY))
        except Exception:
            return False
