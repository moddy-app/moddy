"""
Step 4 — semantic detection via embeddings.

Reached only when the regex blocklist did not match. Captures toxicity without
keywords (veiled threats, "polite" harassment, calmly-worded incitement).

* Model: ``text-embedding-3-small`` (via ``bot.gateway.ai.embed`` — never the
  provider SDK directly).
* Reference phrases (``data/references.json``) are embedded **once** on first
  use and kept in memory, normalized.
* Score = max cosine similarity against the references.
* Single threshold ``SEUIL_EMBEDDING``: ``>=`` routes to nano, ``<`` stops.

Cosine is computed in pure Python (normalized dot product) to keep the package
dependency-free — no numpy.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

from . import constants
from .cache import MISS, LruTtlCache
from .normalize import collapse_repeats, normalize_spaced

logger = logging.getLogger("moddy.automod.embeddings")

_REFERENCES_PATH = Path(__file__).parent / "data" / "references.json"


def cache_key(content: str) -> str:
    """The key a message's score is memoised under.

    Collapsing + normalisation run **before** the key is derived (session 4.4)
    so a padded/repeated variant of a message shares its cache entry: "aaaa" and
    "aaaaa" both key onto "a", "Con" and "con" onto "con". Falls back to the raw
    text when normalisation empties it (e.g. an emoji-only message).
    """
    return collapse_repeats(content) or content


def _segment(content: str) -> List[str]:
    """Return the texts to embed for one message (usually just the message).

    A spammed/concatenated message ("je vais te tuer" ×40) embeds poorly: the
    repetition dilutes the vector below threshold. So we only do something extra
    **when an actual repetition is detected** — in that case we additionally
    embed the single de-duplicated unit, scored on its own. Normal messages
    cost exactly one embedding, as before.

    A message longer than ``PREFILTRE_MAX_CHARS`` is embedded as its de-spammed
    (collapsed) form only, truncated to the cap: a wall of text never needs its
    full length embedded, and the toxic phrase, if any, survives collapsing.
    """
    norm = normalize_spaced(content)
    collapsed = collapse_repeats(content)
    if len(content) > constants.PREFILTRE_MAX_CHARS:
        reduced = (collapsed or norm or content)[: constants.PREFILTRE_MAX_CHARS]
        return [reduced] if reduced else []
    candidates: List[str] = [content]
    # A genuine repetition shrank the text → score the bare unit too.
    if collapsed and collapsed != norm and len(collapsed) < len(norm):
        candidates.append(collapsed)
    return candidates

# An embed function: takes a list of texts, returns a list of vectors.
EmbedFn = Callable[[List[str]], Awaitable[List[List[float]]]]


def _normalize_vec(vec: List[float]) -> List[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


def _dot(a: List[float], b: List[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _retrieve_result(fut: "asyncio.Future") -> None:
    """Done-callback that marks a single-flight future's result as retrieved.

    Guarantees the "Future exception was never retrieved" warning never fires
    when a scoring computation raised and no waiter happened to be attached.
    """
    if not fut.cancelled():
        fut.exception()  # retrieving is enough; the value/None is discarded


class EmbeddingEngine:
    """Holds the normalized reference vectors and scores incoming messages."""

    def __init__(
        self,
        embed_fn: EmbedFn,
        *,
        cache: Optional[LruTtlCache] = None,
    ):
        self._embed_fn = embed_fn
        self._ref_vectors: List[List[float]] = []
        self._ref_categories: List[str] = []
        self._ready = False
        # Score cache: exact message text → (score, category). Deterministic for
        # the process lifetime (references are embedded once), so memoising it is
        # safe and never alters a decision. ``None`` builds the default cache
        # from constants; a disabled cache is a max_entries=0 instance (no-op).
        if cache is None:
            cache = LruTtlCache(
                max_entries=(
                    constants.EMBED_CACHE_MAX_ENTRIES
                    if constants.EMBED_CACHE_ENABLED
                    else 0
                ),
                ttl_seconds=constants.EMBED_CACHE_TTL_SECONDS,
            )
        self._cache: LruTtlCache = cache
        # Single-flight: coalesce concurrent identical scoring requests (a burst
        # of identical raid messages) onto one embedding call.
        self._inflight: Dict[str, "asyncio.Future"] = {}

    @property
    def ready(self) -> bool:
        return self._ready

    def cache_stats(self) -> dict:
        """Score-cache counters plus the current in-flight coalescing count."""
        stats = self._cache.stats()
        stats["inflight"] = len(self._inflight)
        return stats

    @staticmethod
    def load_reference_texts() -> Tuple[List[str], List[str]]:
        """Return (texts, categories) from references.json."""
        with open(_REFERENCES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        texts: List[str] = []
        categories: List[str] = []
        for category, payload in data.get("categories", {}).items():
            for example in payload.get("exemples", []):
                texts.append(example)
                categories.append(category)
        return texts, categories

    async def ensure_ready(self) -> bool:
        """Embed the reference phrases once. Safe to call repeatedly."""
        if self._ready:
            return True
        texts, categories = self.load_reference_texts()
        if not texts:
            logger.warning("automod: no embedding reference phrases found")
            return False
        vectors = await self._embed_fn(texts)
        if not vectors or len(vectors) != len(texts):
            logger.error("automod: reference embedding returned unexpected size")
            return False
        self._ref_vectors = [_normalize_vec(v) for v in vectors]
        self._ref_categories = categories
        self._ready = True
        logger.info("automod: embedded %d reference phrases", len(texts))
        return True

    async def score(self, content: str) -> Optional[Tuple[float, str]]:
        """Return (max_cosine, best_category) or None if scoring unavailable.

        Results are memoised on the exact message text (deterministic for the
        process lifetime) and concurrent identical requests are coalesced onto a
        single embedding call, so repeated/flooded messages cost one call, not N.
        """
        if not self._ready:
            return None

        # The cache key is the collapsed/normalised form (session 4.4), so padded
        # or re-cased variants of a message share one entry and one embed call.
        key = cache_key(content)

        cached = self._cache.get(key)
        if cached is not MISS:
            return cached

        # Coalesce a burst of identical in-flight requests onto one computation.
        pending = self._inflight.get(key)
        if pending is not None:
            return await pending

        loop = asyncio.get_event_loop()
        future: "asyncio.Future" = loop.create_future()
        future.add_done_callback(_retrieve_result)
        self._inflight[key] = future
        try:
            result = await self._compute_score(content)
        except BaseException as exc:  # propagate to this caller and any waiters
            self._inflight.pop(key, None)
            if not future.done():
                future.set_exception(exc)
            raise
        else:
            # Only cache real results — never a transient None (not-ready / empty
            # embedding response), so a blip doesn't get pinned in the cache.
            if result is not None:
                self._cache.set(key, result)
            self._inflight.pop(key, None)
            if not future.done():
                future.set_result(result)
            return result

    async def _compute_score(self, content: str) -> Optional[Tuple[float, str]]:
        """Embed ``content`` and return (max_cosine, best_category) or None.

        Long content is segmented (sentences + windows + de-spammed form) and
        the **max** cosine across every segment is returned, so a toxic phrase
        buried in or diluted by a long/spam message is still caught.
        """
        if not self._ready:
            return None
        segments = _segment(content)
        if not segments:
            return None
        vectors = await self._embed_fn(segments)
        if not vectors:
            return None
        best_score = -1.0
        best_cat = ""
        for raw in vectors:
            query = _normalize_vec(raw)
            for vec, cat in zip(self._ref_vectors, self._ref_categories):
                sim = _dot(vec, query)
                if sim > best_score:
                    best_score = sim
                    best_cat = cat
        return best_score, best_cat

    @staticmethod
    def passes_threshold(score: float, threshold: Optional[float] = None) -> bool:
        return score >= (constants.SEUIL_EMBEDDING if threshold is None else threshold)
