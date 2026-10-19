"""
Translate button service — everything the module and the button share.

* **Detection** (``tools/langdetect.py``): the model is loaded lazily, in a
  worker thread, the first time a server produces an eligible message.
* **Learning**: every confidently detected message bumps a per-server, per-day
  counter. Counters are aggregated in memory and flushed to Redis every
  ``FLUSH_INTERVAL`` seconds in one pipeline (one hash per server per day,
  14-day rolling window). A language is "usual" on a server from 10 % of the
  sample, once 200 messages were sampled.
* **Prompts**: the small "<flag> Language detected [Translate]" message posted
  under a foreign message, extended in place when the same member keeps
  writing (a *burst*), and deleted after a configurable delay.
* **Translation** (a click): fetches the message(s), translates through the
  gateway (DeepL) into the *clicker's* language, with a 24 h Redis cache per
  message and target language.

See docs/features/TRANSLATE_BUTTON.md. Nothing here is persisted except the
learning counters and the translation cache (both Redis, both expiring).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Deque, Dict, List, Optional, Tuple

import discord

from tools import langdetect as ld

logger = logging.getLogger("moddy.translate_button")

NO_MENTIONS = discord.AllowedMentions.none()

# --------------------------------------------------------------------------- #
# Tunables
# --------------------------------------------------------------------------- #

FLUSH_INTERVAL = 30.0            # seconds between two counter flushes
LEARNT_CACHE_TTL = 300.0         # seconds a server's read counters are reused
BURST_WINDOW = 120.0             # a member's next foreign message extends the prompt
BURST_MAX_MESSAGES = 10          # …up to this many messages per prompt
MAX_TRACKED = 4096               # (channel, author) pairs remembered at most
MAX_DELETIONS = 5000             # scheduled prompt deletions at most
CLICKS_PER_MINUTE = 20           # per user, like /translate
CACHE_TTL = 24 * 3600            # translation cache
MAX_SOURCE_CHARS = 1500          # one message, before translation
CARD_TEXT_BUDGET = 3200          # translated text of the whole card
HISTORY_SCAN = 100               # messages scanned to find a burst's messages

_KEY_COUNTS = "moddy:trbtn:langs"
_KEY_CACHE = "moddy:trbtn:tr"

#: Seconds offered by /config for the prompt's lifetime; 0 = never delete.
DELETE_AFTER_OPTIONS: Tuple[int, ...] = (120, 300, 600, 1800, 0)
DEFAULT_DELETE_AFTER = 600


class TranslateError(Exception):
    """An expected failure of a click; ``code`` maps to ``errors.<code>`` in i18n."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


# --------------------------------------------------------------------------- #
# Bursts (pure)
# --------------------------------------------------------------------------- #

@dataclass
class PromptState:
    """The prompt currently standing for one member in one channel."""

    prompt_id: int
    first_id: int
    last_id: int
    language: str
    count: int = 1
    updated: float = 0.0


class BurstTracker:
    """Remembers, in memory, the latest prompt of each (channel, author).

    A prompt is *extendable* while the member keeps writing foreign messages in
    the same language: the previous message was less than ``window`` seconds
    ago and fewer than ``cap`` messages are covered. Starting a prompt for the
    same (channel, author) replaces the entry, so the tracked prompt is always
    the latest one Moddy posted for that member. Lost on restart, by design.
    """

    def __init__(self, window: float = BURST_WINDOW, cap: int = BURST_MAX_MESSAGES,
                 max_entries: int = MAX_TRACKED):
        self.window = window
        self.cap = cap
        self.max_entries = max_entries
        self._entries: Dict[Tuple[int, int], PromptState] = {}

    def extendable(self, channel_id: int, author_id: int, language: str,
                   now: float) -> Optional[PromptState]:
        state = self._entries.get((channel_id, author_id))
        if state is None:
            return None
        if (now - state.updated > self.window or state.count >= self.cap
                or state.language != language):
            return None
        return state

    def start(self, channel_id: int, author_id: int, prompt_id: int, message_id: int,
              language: str, now: float) -> PromptState:
        if len(self._entries) >= self.max_entries:
            self._prune(now)
        state = PromptState(prompt_id, message_id, message_id, language, 1, now)
        self._entries[(channel_id, author_id)] = state
        return state

    def extend(self, state: PromptState, message_id: int, now: float) -> None:
        state.last_id = max(state.last_id, message_id)
        state.count += 1
        state.updated = now

    def forget(self, channel_id: int, author_id: int) -> None:
        self._entries.pop((channel_id, author_id), None)

    def forget_prompt(self, prompt_id: int) -> None:
        for key in [k for k, s in self._entries.items() if s.prompt_id == prompt_id]:
            self._entries.pop(key, None)

    def _prune(self, now: float) -> None:
        for key in [k for k, s in self._entries.items() if now - s.updated > self.window]:
            self._entries.pop(key, None)
        # Still full (a very busy bot): drop the oldest quarter.
        if len(self._entries) >= self.max_entries:
            oldest = sorted(self._entries, key=lambda k: self._entries[k].updated)
            for key in oldest[: max(1, self.max_entries // 4)]:
                self._entries.pop(key, None)

    def __len__(self) -> int:
        return len(self._entries)


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #

@dataclass
class TranslatedItem:
    message_id: int
    text: str
    source: Optional[str] = None     # DeepL's detected source language ("FR", "PT"…)
    jump_url: str = ""


@dataclass
class TranslationResult:
    author_id: int
    target: str                      # DeepL target code ("EN-US")
    items: List[TranslatedItem] = field(default_factory=list)
    from_cache: bool = False


@dataclass
class Learnt:
    """What the server's last two weeks say, for the /config screen."""

    sample: int = 0
    usual: Dict[str, float] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #

class TranslateButtonService:
    """``bot.translate_button`` — see the module docstring."""

    def __init__(self, bot):
        self.bot = bot
        self.detector = ld.Detector()
        self.tracker = BurstTracker()
        self._load_lock: Optional[asyncio.Lock] = None
        self._pending: Dict[Tuple[int, str], Counter] = {}
        self._local: Dict[Tuple[int, str], Counter] = {}
        self._learnt_cache: Dict[int, Tuple[float, Dict[str, int]]] = {}
        self._deletions: Dict[int, asyncio.Task] = {}
        self._locks: Dict[Tuple[int, int], asyncio.Lock] = {}
        self._clicks: Dict[int, Deque[float]] = {}
        self._flush_task: Optional[asyncio.Task] = None
        self._today = lambda: datetime.now(timezone.utc).date()

    # ----------------------------------------------------------------- #
    # Lifecycle
    # ----------------------------------------------------------------- #

    def start(self) -> None:
        """Start the counter flush loop (needs a running loop)."""
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.get_running_loop().create_task(
                self._flush_loop(), name="moddy-translate-button-flush")

    async def stop(self) -> None:
        if self._flush_task is not None:
            self._flush_task.cancel()
            self._flush_task = None
        for task in list(self._deletions.values()):
            task.cancel()
        self._deletions.clear()
        await self.flush()

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(FLUSH_INTERVAL)
            try:
                await self.flush()
            except Exception:  # noqa: BLE001 — never let the loop die
                logger.exception("Translate button: counter flush failed")

    # ----------------------------------------------------------------- #
    # Availability
    # ----------------------------------------------------------------- #

    def available(self) -> bool:
        """True when DeepL is configured: without it a button could not work."""
        gateway = getattr(self.bot, "gateway", None)
        try:
            return bool(gateway is not None and gateway.deepl_available())
        except Exception:  # noqa: BLE001
            return False

    # ----------------------------------------------------------------- #
    # Detection
    # ----------------------------------------------------------------- #

    async def detect(self, text: str) -> Optional[ld.Detection]:
        """Detect ``text``'s language; loads the model on first use (worker thread)."""
        if not self.detector.loaded:
            if self._load_lock is None:
                self._load_lock = asyncio.Lock()
            async with self._load_lock:
                if not self.detector.loaded:
                    await asyncio.to_thread(self.detector.load)
        return self.detector.detect(text)

    # ----------------------------------------------------------------- #
    # Learning the languages a server speaks
    # ----------------------------------------------------------------- #

    @staticmethod
    def _counts_key(guild_id: int, day: date) -> str:
        return f"{_KEY_COUNTS}:{guild_id}:{day.strftime('%Y%m%d')}"

    def count(self, guild_id: int, language: str) -> None:
        """Count one confidently detected message (known language or not)."""
        key = (guild_id, self._today().isoformat())
        self._pending.setdefault(key, Counter())[language] += 1

    async def flush(self) -> None:
        """Write the pending counters: one Redis pipeline for every server."""
        if not self._pending:
            return
        pending, self._pending = self._pending, {}
        redis = getattr(self.bot, "redis", None)
        if redis is not None:
            try:
                pipe = redis.pipeline(transaction=False)
                for (guild_id, iso), counter in pending.items():
                    key = self._counts_key(guild_id, date.fromisoformat(iso))
                    for language, number in counter.items():
                        pipe.hincrby(key, language, number)
                    pipe.expire(key, ld.COUNTER_TTL_DAYS * 86400)
                await pipe.execute()
                return
            except Exception as exc:  # noqa: BLE001 — keep the counts in this process
                logger.warning("Translate button: Redis flush failed (%s)", exc)
        for key, counter in pending.items():
            self._local.setdefault(key, Counter()).update(counter)
        self._trim_local()

    def _trim_local(self) -> None:
        """Without Redis the in-process counters keep the window's days only."""
        oldest = ld.window_days(self._today())[-1].isoformat()
        for key in [k for k in self._local if k[1] < oldest]:
            self._local.pop(key, None)

    async def learnt_counts(self, guild_id: int, *, fresh: bool = False) -> Dict[str, int]:
        """``{language: messages}`` over the 14-day window (cached a few minutes)."""
        now = time.monotonic()
        cached = self._learnt_cache.get(guild_id)
        if cached is not None and cached[0] > now and not fresh:
            return cached[1]

        today = self._today()
        days = ld.window_days(today)
        daily: List[Dict[str, Any]] = []
        redis = getattr(self.bot, "redis", None)
        read = False
        if redis is not None:
            try:
                pipe = redis.pipeline(transaction=False)
                for day in days:
                    pipe.hgetall(self._counts_key(guild_id, day))
                daily.extend(await pipe.execute())
                read = True
            except Exception as exc:  # noqa: BLE001
                logger.warning("Translate button: Redis read failed (%s)", exc)
        if not read:
            for day in days:
                daily.append(dict(self._local.get((guild_id, day.isoformat()), {})))
        # Counts not flushed yet belong to the picture too.
        for day in days:
            daily.append(dict(self._pending.get((guild_id, day.isoformat()), {})))

        counts = ld.merge_counts(daily)
        self._learnt_cache[guild_id] = (now + LEARNT_CACHE_TTL, counts)
        if len(self._learnt_cache) > 20000:
            self._learnt_cache.clear()
        return counts

    async def learnt(self, guild_id: int, *, fresh: bool = False) -> Learnt:
        counts = await self.learnt_counts(guild_id, fresh=fresh)
        return Learnt(sample=sum(counts.values()), usual=ld.usual_languages(counts))

    async def known_languages(self, guild_id: int, server_locale: str,
                              extra: Any = ()) -> set:
        """Languages that get no button on this server (server + learnt + manual)."""
        counts = await self.learnt_counts(guild_id)
        return ld.known_languages(server_locale, ld.usual_languages(counts), extra)

    # ----------------------------------------------------------------- #
    # Prompts
    # ----------------------------------------------------------------- #

    def _lock_for(self, key: Tuple[int, int]) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            if len(self._locks) > MAX_TRACKED:
                for stale in [k for k, v in self._locks.items() if not v.locked()]:
                    self._locks.pop(stale, None)
            lock = self._locks[key] = asyncio.Lock()
        return lock

    async def offer(self, message: discord.Message, language: str, locale: str,
                    delete_after: int) -> bool:
        """Post the prompt under ``message``, or extend the member's current one.

        Returns True when a prompt was posted or extended.
        """
        from utils.translate_button_views import build_prompt_view

        channel = message.channel
        key = (channel.id, message.author.id)
        async with self._lock_for(key):
            now = time.monotonic()
            state = self.tracker.extendable(*key, language, now)
            if state is not None:
                view = build_prompt_view(channel.id, state.first_id, message.id, language, locale)
                partial = channel.get_partial_message(state.prompt_id)
                try:
                    await partial.edit(view=view)
                except discord.NotFound:
                    self.tracker.forget(*key)  # deleted meanwhile: start a fresh prompt
                except discord.HTTPException as exc:
                    logger.warning("Translate button: could not extend a prompt: %s", exc)
                    return False
                else:
                    self.tracker.extend(state, message.id, now)
                    self.schedule_delete(partial, key, delete_after)
                    return True

            view = build_prompt_view(channel.id, message.id, message.id, language, locale)
            try:
                reply = await message.reply(view=view, mention_author=False,
                                            allowed_mentions=NO_MENTIONS, silent=True)
            except discord.Forbidden:
                logger.debug("Translate button: no permission to reply in channel %s", channel.id)
                return False
            except discord.HTTPException as exc:
                logger.warning("Translate button: could not post a prompt: %s", exc)
                return False
            self.tracker.start(*key, reply.id, message.id, language, now)
            self.schedule_delete(channel.get_partial_message(reply.id), key, delete_after)
            return True

    def schedule_delete(self, prompt, key: Tuple[int, int], seconds: int) -> None:
        """Delete ``prompt`` after ``seconds`` (0 = never). Best effort.

        The timer lives in this process: a prompt posted before a restart is
        simply left in place.
        """
        previous = self._deletions.pop(prompt.id, None)
        if previous is not None:
            previous.cancel()
        if not seconds or len(self._deletions) >= MAX_DELETIONS:
            return

        async def _later():
            try:
                await asyncio.sleep(seconds)
                try:
                    await prompt.delete()
                except (discord.NotFound, discord.Forbidden):
                    pass
                except discord.HTTPException as exc:
                    logger.debug("Translate button: could not delete a prompt: %s", exc)
            finally:
                self._deletions.pop(prompt.id, None)
                self.tracker.forget_prompt(prompt.id)

        self._deletions[prompt.id] = asyncio.get_running_loop().create_task(
            _later(), name=f"moddy-translate-button-delete-{prompt.id}")

    # ----------------------------------------------------------------- #
    # Clicks
    # ----------------------------------------------------------------- #

    def allow_click(self, user_id: int) -> bool:
        """Sliding-window limiter: ``CLICKS_PER_MINUTE`` per user and minute."""
        now = time.monotonic()
        window = self._clicks.setdefault(user_id, deque())
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= CLICKS_PER_MINUTE:
            return False
        window.append(now)
        if len(self._clicks) > 5000:
            for uid in [u for u, w in self._clicks.items() if not w or now - w[-1] > 60]:
                self._clicks.pop(uid, None)
        return True

    @staticmethod
    def deepl_target(interaction: discord.Interaction) -> str:
        """DeepL target language for whoever clicked.

        The language forced in /preferences wins, else the Discord client
        language (any of them — not only the five Moddy is translated into).
        """
        from cogs.translate import Translate
        from utils.user_preferences import get_language_override

        override = get_language_override(getattr(interaction.user, "id", None))
        # ``locale_to_deepl_lang`` does not use ``self``: reuse /translate's table.
        return Translate.locale_to_deepl_lang(None, override or str(interaction.locale))

    async def collect_messages(self, channel, first_id: int, last_id: int) -> List[discord.Message]:
        """The messages a prompt stands for: its author's, from first to last."""
        try:
            first = await channel.fetch_message(first_id)
        except (discord.NotFound, discord.Forbidden):
            raise TranslateError("gone")
        except discord.HTTPException:
            raise TranslateError("failed")
        if last_id <= first_id:
            return [first]

        found = [first]
        try:
            async for message in channel.history(limit=HISTORY_SCAN, oldest_first=True,
                                                 after=discord.Object(first_id)):
                if message.id > last_id:
                    break
                if message.author.id == first.author.id and ld.eligible_text(message.content):
                    found.append(message)
                if len(found) >= BURST_MAX_MESSAGES:
                    break
        except discord.HTTPException:
            logger.debug("Translate button: could not read the history of a burst")
        return found

    async def translate_messages(self, interaction: discord.Interaction, channel,
                                 first_id: int, last_id: int) -> TranslationResult:
        """Translate a prompt's messages into the clicker's language."""
        messages = [m for m in await self.collect_messages(channel, first_id, last_id)
                    if (m.content or "").strip()]
        if not messages:
            raise TranslateError("empty")
        if not self.available():
            raise TranslateError("unavailable")

        target = self.deepl_target(interaction)
        limit = min(MAX_SOURCE_CHARS, CARD_TEXT_BUDGET // len(messages))
        results = await asyncio.gather(
            *(self._translate_one(interaction, m, target, limit) for m in messages))
        stats = getattr(self.bot, "stats", None)
        if stats is not None:
            stats.incr("translate_button.clicked")
        return TranslationResult(
            author_id=messages[0].author.id,
            target=target,
            items=[item for item, _ in results],
            from_cache=all(cached for _, cached in results),
        )

    async def _translate_one(self, interaction: discord.Interaction, message: discord.Message,
                             target: str, limit: int) -> Tuple[TranslatedItem, bool]:
        edited = int(message.edited_at.timestamp()) if message.edited_at else 0
        cache_key = f"{_KEY_CACHE}:{message.id}:{edited}:{target}"
        redis = getattr(self.bot, "redis", None)
        if redis is not None:
            try:
                raw = await redis.get(cache_key)
                if raw:
                    data = json.loads(raw)
                    stats = getattr(self.bot, "stats", None)
                    if stats is not None:
                        stats.incr("translate_button.cache_hit")
                    return TranslatedItem(message.id, data["text"][:limit * 2], data.get("source"),
                                          message.jump_url), True
            except Exception as exc:  # noqa: BLE001 — a cache miss is not an error
                logger.debug("Translate button: cache read failed: %s", exc)

        source_text = self.neutralize_mentions(message.content, message.guild)[:limit]
        data = await self._deepl(interaction.user.id, source_text, target)
        if redis is not None:
            try:
                await redis.set(cache_key, json.dumps(data, ensure_ascii=False), ex=CACHE_TTL)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Translate button: cache write failed: %s", exc)
        return TranslatedItem(message.id, data["text"], data.get("source"), message.jump_url), False

    async def _deepl(self, user_id: int, text: str, target: str) -> Dict[str, Any]:
        from gateway.errors import (
            APIUnavailableError, ConfigurationError, GatewayError, ModelRateLimitError,
            QuotaExceededError, RateLimitError,
        )
        from gateway.spec import QuotaTarget

        try:
            result = await self.bot.gateway.translation.translate(
                text, target,
                quota=[QuotaTarget.user(user_id, "translation")],
                call_type="translation",
                metadata={"user_id": user_id, "source": "translate_button"},
            )
        except QuotaExceededError as exc:
            raise TranslateError("quota") from exc
        except (ModelRateLimitError, RateLimitError) as exc:
            raise TranslateError("rate_limited") from exc
        except (APIUnavailableError, ConfigurationError) as exc:
            raise TranslateError("unavailable") from exc
        except GatewayError as exc:
            logger.warning("Translate button: DeepL call failed: %s", exc)
            raise TranslateError("failed") from exc
        translated = (result or {}).get("text") or ""
        if not translated.strip():
            raise TranslateError("failed")
        return {"text": translated, "source": (result or {}).get("detected_source_language")}

    def neutralize_mentions(self, text: str, guild: Optional[discord.Guild]) -> str:
        """Mentions -> plain names before DeepL sees them (reuses /translate's rule)."""
        cog = self.bot.get_cog("Translate") if hasattr(self.bot, "get_cog") else None
        if cog is not None:
            return cog.sanitize_mentions(text, guild)
        text = text.replace("@everyone", "@​everyone").replace("@here", "@​here")
        return re.sub(r"<@[!&]?\d+>", "@user", text)
