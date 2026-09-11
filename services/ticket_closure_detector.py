"""
Ticket closure detection — noticing that a conversation has run its course.

When someone writes "merci, c'est bon" the ticket is almost always over, and
nobody closes it. This spots that and *offers* the closure; it never performs
one. What the offer can actually do is decided at click time from the clicker's
own permissions (``utils/ticket_views.TicketClosureSuggestionView``), so
proposing a closure to someone who may not close tickets turns into a close
request, exactly as if they had run ``/ticket close-request`` themselves.

The cost model matters more than the accuracy here, because this runs on every
message of every ticket of every server:

1. ``looks_like_closure`` is a lexical test over ~90 multilingual roots. It is
   free, it runs in the cog before anything else, and it rules out the
   overwhelming majority of messages. **No keyword hit, no API call, ever.**
2. What survives goes through structural checks (length, questions, mentions,
   how young the ticket is) that are also free.
3. Only then is a message embedded, and even then the engine memoises scores
   and coalesces identical concurrent requests (``automod.embeddings``).

The embedding engine, its cosine maths and its LRU+TTL cache are reused from
``automod`` rather than rewritten; only the reference phrases and the threshold
are this module's own. The threshold is deliberately higher than automod's: a
false positive there routes a message to a second model, a false positive here
puts a card in front of everybody in the channel.

Everything this keeps between messages is in memory. Losing a debounce timer to
a redeploy costs one slightly-early suggestion, which is not worth a table.

See docs/TICKETS.md.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import discord

from automod.cache import LruTtlCache
from automod.embeddings import EmbeddingEngine

logger = logging.getLogger('moddy.tickets.closure')

_REFERENCES_PATH = Path(__file__).parent / "data" / "ticket_closure_references.json"

# Cosine above which a message reads as "we're done here". Higher than
# automod's 0.45 on purpose — see the module docstring.
CLOSURE_THRESHOLD = 0.62

# Structural prefilter. A closing line is short, is not a question, and does
# not address anybody in particular.
MIN_LENGTH = 8
MAX_LENGTH = 160

# A ticket has to have been a conversation before it can have ended.
MIN_TICKET_AGE = 120        # seconds
MIN_HUMAN_MESSAGES = 3

# Wait for the conversation to settle before suggesting anything: "merci" two
# seconds before "attends en fait non" must not produce a card.
DEBOUNCE_SECONDS = 20

# One suggestion per ticket per window. Dismissing re-arms it from now.
SUGGESTION_COOLDOWN = 6 * 3600

# Small: this only has to cover the handful of distinct closing lines a server
# sees in an hour, on top of the engine's own score cache.
_CACHE_MAX_ENTRIES = 512
_CACHE_TTL = 1800.0


def _strip_accents(text: str) -> str:
    """Lowercase and drop diacritics, so "résolu" matches the root "resolu"."""
    decomposed = unicodedata.normalize('NFD', text.lower())
    return ''.join(c for c in decomposed if unicodedata.category(c) != 'Mn')


class _ClosureReferences(EmbeddingEngine):
    """The automod engine, pointed at the ticket-closure phrases.

    Subclassed rather than copied: the cosine maths, the score cache and the
    single-flight coalescing are all worth reusing, and only the corpus differs.
    """

    @staticmethod
    def load_reference_texts() -> Tuple[List[str], List[str]]:
        with open(_REFERENCES_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        texts: List[str] = []
        categories: List[str] = []
        for category, payload in data.get('categories', {}).items():
            for example in payload.get('exemples', []):
                texts.append(example)
                categories.append(category)
        return texts, categories


def load_keywords() -> List[str]:
    """Every lexical root of the prefilter, flattened and accent-stripped."""
    with open(_REFERENCES_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    roots: List[str] = []
    for language_roots in data.get('keywords', {}).values():
        roots.extend(_strip_accents(root) for root in language_roots)
    return roots


class _ChannelState:
    """What the detector remembers about one open ticket."""

    __slots__ = ('timer', 'last_suggestion', 'human_messages')

    def __init__(self):
        self.timer: Optional[asyncio.TimerHandle] = None
        self.last_suggestion: float = 0.0
        self.human_messages: int = 0


class TicketClosureDetector:
    """Watches ticket messages and offers a closure. ``bot.ticket_closure``."""

    def __init__(self, bot):
        self.bot = bot
        self._keywords: Optional[List[str]] = None
        self._engine: Optional[_ClosureReferences] = None
        self._engine_lock = asyncio.Lock()
        self._states: Dict[int, _ChannelState] = {}

    # ------------------------------------------------------------------ #
    # The free prefilter
    # ------------------------------------------------------------------ #
    @property
    def keywords(self) -> List[str]:
        if self._keywords is None:
            try:
                self._keywords = load_keywords()
            except (OSError, ValueError) as e:
                logger.error(f"[Tickets] Could not load closure keywords: {e}")
                self._keywords = []
        return self._keywords

    def looks_like_closure(self, content: Optional[str]) -> bool:
        """Whether this message is even worth considering. Free, no I/O.

        Called from the message listener before anything else: a message that
        fails here never reaches the database, let alone an embedding.
        """
        if not content:
            return False
        stripped = content.strip()
        if not (MIN_LENGTH <= len(stripped) <= MAX_LENGTH):
            return False
        if '?' in stripped:
            return False            # a question is not a goodbye
        if '<@' in stripped or '<#' in stripped:
            return False            # addressed to someone, still in progress
        if stripped.startswith(('/', '!', '.', '>')):
            return False            # a command, not a sentence
        haystack = _strip_accents(stripped)
        return any(root in haystack for root in self.keywords)

    # ------------------------------------------------------------------ #
    # State
    # ------------------------------------------------------------------ #
    def _state(self, channel_id: int) -> _ChannelState:
        state = self._states.get(channel_id)
        if state is None:
            state = self._states[channel_id] = _ChannelState()
        return state

    def forget(self, channel_id: int) -> None:
        """Drop everything known about a channel — closed, reopened or gone."""
        state = self._states.pop(int(channel_id), None)
        if state is not None and state.timer is not None:
            state.timer.cancel()

    def note_dismissed(self, channel_id: int) -> None:
        """Someone said "not yet": hold off for a full cooldown from now."""
        self._state(int(channel_id)).last_suggestion = time.monotonic()

    # ------------------------------------------------------------------ #
    # Consideration
    # ------------------------------------------------------------------ #
    async def consider(self, message: discord.Message,
                       ticket: Dict[str, Any]) -> None:
        """Schedule a scored look at this message, debounced per channel.

        Returns immediately: the actual scoring happens once the channel has
        been quiet for :data:`DEBOUNCE_SECONDS`.
        """
        state = self._state(message.channel.id)
        state.human_messages += 1

        now = time.monotonic()
        if now - state.last_suggestion < SUGGESTION_COOLDOWN and state.last_suggestion:
            return
        if state.human_messages < MIN_HUMAN_MESSAGES:
            return

        opened_at = ticket.get('opened_at')
        if opened_at is not None:
            age = (discord.utils.utcnow() - opened_at).total_seconds()
            if age < MIN_TICKET_AGE:
                return

        if state.timer is not None:
            state.timer.cancel()
        loop = asyncio.get_running_loop()
        state.timer = loop.call_later(
            DEBOUNCE_SECONDS,
            lambda: asyncio.ensure_future(self._score_and_offer(message)))

    async def _engine_ready(self) -> Optional[_ClosureReferences]:
        """The engine, references embedded. One API call per process."""
        gateway = getattr(self.bot, 'gateway', None)
        if gateway is None or getattr(gateway, 'ai', None) is None:
            return None
        async with self._engine_lock:
            if self._engine is None:
                self._engine = _ClosureReferences(
                    gateway.ai.embed,
                    cache=LruTtlCache(max_entries=_CACHE_MAX_ENTRIES,
                                      ttl_seconds=_CACHE_TTL),
                )
            if not self._engine.ready:
                try:
                    if not await self._engine.ensure_ready():
                        return None
                except Exception as e:  # noqa: BLE001 - a suggestion is optional
                    logger.error(f"[Tickets] Could not embed the closure "
                                 f"references: {e}")
                    return None
        return self._engine

    async def score(self, content: str) -> Optional[float]:
        """Cosine of this message against the closure corpus, or ``None``."""
        engine = await self._engine_ready()
        if engine is None:
            return None
        try:
            result = await engine.score(content)
        except Exception as e:  # noqa: BLE001
            logger.error(f"[Tickets] Closure scoring failed: {e}")
            return None
        return result[0] if result else None

    async def _score_and_offer(self, message: discord.Message) -> None:
        """The debounced half: score, and post the card if it clears the bar."""
        channel = message.channel
        state = self._state(channel.id)
        state.timer = None

        service = getattr(self.bot, 'tickets', None)
        if service is None or not service.is_open_ticket_channel(channel.id):
            return

        score = await self.score(message.content)
        if score is None or score < CLOSURE_THRESHOLD:
            return

        # Re-read the ticket: the debounce window is twenty seconds, and
        # somebody may well have closed it in the meantime.
        try:
            ticket, _panel, category = await service.resolve(channel)
        except Exception:
            return
        if ticket['status'] != 'open':
            return

        from utils.ticket_views import build_closure_suggestion
        locale = await service.ticket_locale(channel.guild)
        try:
            await channel.send(view=build_closure_suggestion(locale=locale))
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.warning(f"[Tickets] Could not post the closure suggestion "
                           f"in {channel.id}: {e}")
            return

        state.last_suggestion = time.monotonic()
        state.human_messages = 0
        logger.info(f"[Tickets] Closure suggested in #{ticket['number']} "
                    f"(score {score:.2f})")
        stats = getattr(self.bot, 'stats', None)
        if stats is not None:
            stats.incr("ticket.closure_suggested", guild_id=channel.guild.id)
