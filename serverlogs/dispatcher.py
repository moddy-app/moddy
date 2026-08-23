"""Delivery of rendered logs — webhooks, batching and back-pressure.

Logs are the one feature that fires hardest exactly when a server is in
trouble (a raid, a mass ban, a purge). Posting them with ``channel.send``
would burn the bot's own rate-limit buckets and slow every other command
down, so every log goes out through a **channel webhook** instead:

* webhooks have their own rate-limit bucket, separate from the bot user;
* one webhook call can carry **10 embeds**, so a burst collapses into a
  handful of requests;
* if the webhook is missing or unusable, delivery falls back to a plain
  ``channel.send`` so a misconfigured server still gets its logs.

Each destination channel owns one worker task with a bounded queue. When the
queue is full (a genuine flood) the oldest pending entry is dropped and
counted rather than letting memory grow without bound.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional, Tuple

import discord

logger = logging.getLogger('moddy.serverlogs.dispatcher')

#: Name of the webhook Moddy creates in each log channel.
WEBHOOK_NAME = "Moddy Logs"
#: Discord's own limit for embeds in a single message.
MAX_EMBEDS_PER_MESSAGE = 10
#: How long a worker waits for more entries before flushing a batch.
BATCH_WINDOW = 0.75
#: Pending entries kept per channel before the oldest are dropped.
QUEUE_MAXSIZE = 500


class _Payload:
    """One queued log message: its embed and any attachments."""

    __slots__ = ("embed", "files")

    def __init__(self, embed: discord.Embed, files: List[discord.File]):
        self.embed = embed
        self.files = files

    @property
    def batchable(self) -> bool:
        # Files are per-message, so an entry carrying a transcript is sent on
        # its own rather than merged into a batch.
        return not self.files


class LogDispatcher:
    """Owns the webhooks, the per-channel queues and their worker tasks."""

    def __init__(self, bot):
        self.bot = bot
        self._queues: Dict[int, asyncio.Queue] = {}
        self._workers: Dict[int, asyncio.Task] = {}
        self._webhooks: Dict[int, Optional[discord.Webhook]] = {}
        self._webhook_locks: Dict[int, asyncio.Lock] = {}
        self._dropped: Dict[int, int] = {}

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def enqueue(self, channel_id: int, embed: discord.Embed,
                files: Optional[List[discord.File]] = None) -> None:
        """Hand one rendered log to the channel's worker. Never blocks."""
        queue = self._queues.get(channel_id)
        if queue is None:
            queue = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
            self._queues[channel_id] = queue

        payload = _Payload(embed, files or [])
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            # Under a flood, keep the most recent events: drop the oldest.
            try:
                queue.get_nowait()
                queue.task_done()
                queue.put_nowait(payload)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass
            self._dropped[channel_id] = self._dropped.get(channel_id, 0) + 1
            if self._dropped[channel_id] % 100 == 1:
                logger.warning(
                    f"[Logs] Channel {channel_id} is flooding: "
                    f"{self._dropped[channel_id]} log entries dropped"
                )

        worker = self._workers.get(channel_id)
        if worker is None or worker.done():
            self._workers[channel_id] = asyncio.create_task(
                self._run_worker(channel_id), name=f"moddy-logs-{channel_id}")

    def invalidate_webhook(self, channel_id: int) -> None:
        """Forget a cached webhook (deleted in Discord, permissions changed…)."""
        self._webhooks.pop(channel_id, None)

    async def close(self) -> None:
        """Cancel every worker — called when the cog is unloaded."""
        for task in self._workers.values():
            task.cancel()
        self._workers.clear()
        self._queues.clear()
        self._webhooks.clear()

    # ------------------------------------------------------------------ #
    # Worker
    # ------------------------------------------------------------------ #

    async def _run_worker(self, channel_id: int) -> None:
        queue = self._queues.get(channel_id)
        if queue is None:
            return
        try:
            while True:
                try:
                    first: _Payload = await asyncio.wait_for(queue.get(), timeout=30)
                except asyncio.TimeoutError:
                    # Idle channel: let the task die, `enqueue` restarts one.
                    return

                batch = [first]
                if first.batchable:
                    batch.extend(await self._drain(queue))

                try:
                    await self._deliver(channel_id, batch)
                except Exception as e:
                    logger.error(f"[Logs] Delivery failed for channel {channel_id}: {e}",
                                 exc_info=True)
                finally:
                    for _ in batch:
                        queue.task_done()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[Logs] Worker crashed for channel {channel_id}: {e}",
                         exc_info=True)

    async def _drain(self, queue: asyncio.Queue) -> List[_Payload]:
        """Collect further batchable entries within the batching window."""
        collected: List[_Payload] = []
        deadline = asyncio.get_running_loop().time() + BATCH_WINDOW
        while len(collected) < MAX_EMBEDS_PER_MESSAGE - 1:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                payload: _Payload = await asyncio.wait_for(queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            if not payload.batchable:
                # Not mergeable: put it back at the head by sending it next.
                collected.append(payload)
                break
            collected.append(payload)
        return collected

    async def _deliver(self, channel_id: int, batch: List[_Payload]) -> None:
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            return

        # Entries carrying files go out one by one; the rest are merged.
        mergeable = [p for p in batch if p.batchable]
        standalone = [p for p in batch if not p.batchable]

        for chunk_start in range(0, len(mergeable), MAX_EMBEDS_PER_MESSAGE):
            chunk = mergeable[chunk_start:chunk_start + MAX_EMBEDS_PER_MESSAGE]
            await self._send(channel, [p.embed for p in chunk], [])

        for payload in standalone:
            await self._send(channel, [payload.embed], payload.files)

    async def _send(self, channel, embeds: List[discord.Embed],
                    files: List[discord.File]) -> None:
        if not embeds:
            return

        webhook, thread = await self._get_webhook(channel)
        if webhook is not None:
            try:
                kwargs = {"embeds": embeds, "files": files} if files else {"embeds": embeds}
                if thread is not None:
                    kwargs["thread"] = thread
                await webhook.send(wait=False, **kwargs)
                return
            except discord.NotFound:
                # Webhook deleted behind our back — drop it and fall through.
                self.invalidate_webhook(channel.id)
            except discord.HTTPException as e:
                logger.warning(f"[Logs] Webhook send failed in {channel.id}: {e}")
                self.invalidate_webhook(channel.id)

        try:
            await channel.send(embeds=embeds, files=files or discord.utils.MISSING)
        except discord.HTTPException as e:
            logger.warning(f"[Logs] Fallback send failed in {channel.id}: {e}")

    # ------------------------------------------------------------------ #
    # Webhooks
    # ------------------------------------------------------------------ #

    async def _get_webhook(self, channel) -> Tuple[Optional[discord.Webhook], Optional[discord.abc.Snowflake]]:
        """Return ``(webhook, thread)`` for a destination channel.

        Threads have no webhook of their own: the parent's webhook is used
        with ``thread=``. ``None`` means "no usable webhook" — the caller
        falls back to ``channel.send``.
        """
        thread = None
        target = channel
        if isinstance(channel, discord.Thread):
            thread = channel
            target = channel.parent
            if target is None:
                return None, None

        cached = self._webhooks.get(target.id)
        if cached is not None:
            return cached, thread
        if target.id in self._webhooks:  # cached negative result
            return None, thread

        lock = self._webhook_locks.setdefault(target.id, asyncio.Lock())
        async with lock:
            if target.id in self._webhooks:
                return self._webhooks[target.id], thread

            webhook = await self._resolve_webhook(target)
            self._webhooks[target.id] = webhook
            return webhook, thread

    async def _resolve_webhook(self, channel) -> Optional[discord.Webhook]:
        me = channel.guild.me if channel.guild else None
        if me is None or not channel.permissions_for(me).manage_webhooks:
            return None

        try:
            existing = await channel.webhooks()
        except discord.HTTPException as e:
            logger.debug(f"[Logs] Could not list webhooks in {channel.id}: {e}")
            return None

        for webhook in existing:
            if webhook.token and webhook.name == WEBHOOK_NAME:
                return webhook

        avatar_bytes = None
        try:
            if self.bot.user and self.bot.user.display_avatar:
                avatar_bytes = await self.bot.user.display_avatar.read()
        except Exception:
            avatar_bytes = None

        try:
            return await channel.create_webhook(
                name=WEBHOOK_NAME,
                avatar=avatar_bytes,
                reason="Moddy server logs",
            )
        except discord.HTTPException as e:
            logger.warning(f"[Logs] Could not create a webhook in {channel.id}: {e}")
            return None
