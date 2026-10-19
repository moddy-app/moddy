"""
AI image detector service (``bot.ai_detector``) — the Discord + Redis side.

Shared by the ``ai_detector`` module (automatic replies under images), the
persistent "details" button and ``/ai-check``. The decision itself is pure and
synchronous (``aidetector/analysis.py``); this service only:

- keeps the **official C2PA trust list** loaded: read from Redis
  (``moddy:ai_detector:trust_list``, 24 h TTL, shared by every bot process)
  or downloaded from the C2PA conformance repository and written back there;
- downloads attachments from ``attachment.url`` — the original file. Never
  ``proxy_url``: Discord's media proxy re-encodes images and drops the
  metadata, C2PA store included;
- runs the analysis in ``asyncio.to_thread`` under a small semaphore, so a
  burst of images never starves the event loop nor holds dozens of files in
  memory at once (automatic analyses beyond the queue limit are dropped);
- remembers recent results per attachment id, so the details button does not
  download the image a second time.

See docs/features/AI_DETECTOR.md.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple

import discord

from aidetector import analysis as an
from aidetector import formats, verify

logger = logging.getLogger("moddy.services.ai_detector")

REDIS_KEY = "moddy:ai_detector:trust_list"
TRUST_LIST_TTL = 24 * 3600
#: How often the loaded list is checked against Redis / re-downloaded.
REFRESH_EVERY = 3600
#: Retry delay after a failed download when nothing is loaded at all.
RETRY_EVERY = 600
DOWNLOAD_TIMEOUT = 20

#: Larger files are not analysed (Discord's own free upload cap is 10 MB).
MAX_FILE_BYTES = 25 * 1024 * 1024
#: Images of one message considered by the automatic reply.
MAX_IMAGES_PER_MESSAGE = 4
ATTACHMENT_TIMEOUT = 30
MAX_CONCURRENT = 3
#: Automatic analyses waiting for a slot beyond this are dropped (busy).
MAX_WAITING = 25

CACHE_SIZE = 512
CACHE_TTL = 3600


class AiDetectorService:
    """Trust list lifecycle + attachment analysis."""

    def __init__(self, bot):
        self.bot = bot
        self._settings_json: Optional[str] = None
        self._loaded_pem: Optional[str] = None
        self._loaded_at: float = 0.0
        self._refresh_task: Optional[asyncio.Task] = None
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        self._waiting = 0
        self._cache: "OrderedDict[int, Tuple[float, an.Analysis]]" = OrderedDict()
        self._c2pa_available = verify.is_available()
        if not self._c2pa_available:
            logger.warning("[WARN] c2pa-python unavailable: only unsigned PNG "
                           "metadata will be detected")

    # ----------------------------------------------------------------- #
    # Trust list
    # ----------------------------------------------------------------- #

    @property
    def trust_ready(self) -> bool:
        """True once a trust list is loaded (C2PA verdicts possible)."""
        return self._settings_json is not None

    def settings_json(self) -> Optional[str]:
        return self._settings_json if self._c2pa_available else None

    def start(self) -> None:
        """Load the trust list in the background and keep it fresh."""
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = asyncio.create_task(self._refresh_loop())

    async def stop(self) -> None:
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._refresh_task = None

    async def _refresh_loop(self) -> None:
        while True:
            try:
                await self.refresh_trust_list()
            except Exception as e:  # noqa: BLE001 — never let the loop die
                logger.error(f"[FAIL] AI detector trust list refresh: {e}", exc_info=True)
            await asyncio.sleep(REFRESH_EVERY if self.trust_ready else RETRY_EVERY)

    async def refresh_trust_list(self) -> bool:
        """Redis first, then the network. Returns True when a list is loaded.

        Whatever Redis holds is adopted (another process may have refreshed
        it); an expired key is re-downloaded and written back by this process.
        Without Redis, a list younger than the TTL is kept as is. A failed
        download never unloads the list in use.
        """
        if not self._c2pa_available:
            return False
        fresh = self.trust_ready and time.monotonic() - self._loaded_at < TRUST_LIST_TTL
        if fresh and getattr(self.bot, "redis", None) is None:
            return True  # no shared cache: one download a day is enough

        pem = await self._read_redis()
        if pem is not None:
            # Possibly refreshed by another process meanwhile: adopt it.
            if pem != self._loaded_pem:
                self._load(pem)
            return True
        # Expired in Redis (or never cached): this process refreshes it.
        pem = await self._download()
        if pem is None:
            return self.trust_ready
        await self._write_redis(pem)
        self._load(pem)
        return True

    def _load(self, pem: str) -> None:
        self._loaded_pem = pem
        self._settings_json = verify.build_settings(pem)
        self._loaded_at = time.monotonic()
        logger.info("AI detector: C2PA trust list loaded "
                    f"({pem.count('BEGIN CERTIFICATE')} certificates)")

    async def _read_redis(self) -> Optional[str]:
        redis = getattr(self.bot, "redis", None)
        if redis is None:
            return None
        try:
            value = await redis.get(REDIS_KEY)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"AI detector: Redis read failed: {e}")
            return None
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="replace")
        return value if verify.looks_like_pem_bundle(value) else None

    async def _write_redis(self, pem: str) -> None:
        redis = getattr(self.bot, "redis", None)
        if redis is None:
            return
        try:
            await redis.set(REDIS_KEY, pem, ex=TRUST_LIST_TTL)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"AI detector: Redis write failed: {e}")

    async def _download(self) -> Optional[str]:
        import aiohttp
        try:
            timeout = aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT)
            async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
                async with session.get(verify.TRUST_LIST_URL) as response:
                    if response.status != 200:
                        logger.warning(f"AI detector: trust list HTTP {response.status}")
                        return None
                    text = await response.text()
        except Exception as e:  # noqa: BLE001 — network, timeout, decoding
            logger.warning(f"AI detector: trust list download failed: {e}")
            return None
        if not verify.looks_like_pem_bundle(text):
            logger.warning("AI detector: downloaded trust list is not a PEM bundle")
            return None
        return text

    # ----------------------------------------------------------------- #
    # Analysis
    # ----------------------------------------------------------------- #

    @staticmethod
    def candidate_images(message: discord.Message) -> List[discord.Attachment]:
        """PNG / JPEG / WebP attachments of ``message``, in order, capped."""
        found = [
            a for a in getattr(message, "attachments", None) or []
            if formats.declared_mime(a.filename, a.content_type) is not None
        ]
        return found[:MAX_IMAGES_PER_MESSAGE]

    def cached(self, attachment_id: int) -> Optional[an.Analysis]:
        entry = self._cache.get(attachment_id)
        if entry is None:
            return None
        stored_at, result = entry
        if time.monotonic() - stored_at > CACHE_TTL:
            self._cache.pop(attachment_id, None)
            return None
        self._cache.move_to_end(attachment_id)
        return result

    def _remember(self, attachment_id: Optional[int], result: an.Analysis) -> None:
        if attachment_id is None or result.reason in (an.REASON_BUSY,
                                                      an.REASON_DOWNLOAD_FAILED,
                                                      an.REASON_TRUST_UNAVAILABLE):
            return
        self._cache[attachment_id] = (time.monotonic(), result)
        self._cache.move_to_end(attachment_id)
        while len(self._cache) > CACHE_SIZE:
            self._cache.popitem(last=False)

    async def analyze_bytes(self, data: bytes) -> an.Analysis:
        """Analyse raw bytes in a worker thread (no queue limit)."""
        async with self._semaphore:
            return await asyncio.to_thread(an.analyze, data, self.settings_json())

    async def analyze_attachment(self, attachment: discord.Attachment, *,
                                 automatic: bool = False) -> an.Analysis:
        """Download ``attachment`` (original URL) and analyse it.

        ``automatic`` analyses (the module) give up instead of queueing when
        the bot is already busy; on-demand ones (button, command) wait.
        """
        cached = self.cached(attachment.id)
        if cached is not None:
            return cached
        if attachment.size and attachment.size > MAX_FILE_BYTES:
            return an.Analysis(reason=an.REASON_TOO_LARGE, size=attachment.size)
        if automatic and self._waiting >= MAX_WAITING:
            return an.Analysis(reason=an.REASON_BUSY)

        self._waiting += 1
        try:
            async with self._semaphore:
                try:
                    # Attachment.read() downloads ``attachment.url`` (the
                    # original file); use_cached=True would go through
                    # proxy_url, which strips the C2PA store.
                    data = await asyncio.wait_for(
                        attachment.read(use_cached=False), ATTACHMENT_TIMEOUT)
                except (discord.HTTPException, asyncio.TimeoutError) as e:
                    logger.debug(f"AI detector: download failed for {attachment.id}: {e}")
                    return an.Analysis(reason=an.REASON_DOWNLOAD_FAILED)
                if len(data) > MAX_FILE_BYTES:
                    return an.Analysis(reason=an.REASON_TOO_LARGE, size=len(data))
                result = await asyncio.to_thread(an.analyze, data, self.settings_json())
        finally:
            self._waiting -= 1
        self._remember(attachment.id, result)
        return result

    async def analyze_message(
        self, message: discord.Message, *, automatic: bool = False,
        kinds: Optional[List[str]] = None,
    ) -> Optional[Tuple[discord.Attachment, an.Analysis]]:
        """The strongest verdict among a message's images, or None.

        ``kinds`` restricts which verdict kinds count (a server that switched
        some off); None accepts them all.
        """
        best: Optional[Tuple[discord.Attachment, an.Analysis]] = None
        for attachment in self.candidate_images(message):
            result = await self.analyze_attachment(attachment, automatic=automatic)
            if result.kind is None or (kinds is not None and result.kind not in kinds):
                continue
            if best is None or result.rank < best[1].rank:
                best = (attachment, result)
            if best[1].rank == 0:
                break  # nothing outranks a fully AI-generated image
        return best

    def record(self, kind: Optional[str], source: str) -> None:
        """Count a verdict (``source``: auto | command). Never raises."""
        stats = getattr(self.bot, "stats", None)
        if stats is None or kind is None:
            return
        stats.incr("ai_detector.verdict", dims={"kind": kind, "source": source})
