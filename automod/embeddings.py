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

import json
import logging
import math
from pathlib import Path
from typing import Awaitable, Callable, List, Optional, Tuple

from . import constants
from .normalize import collapse_repeats, normalize_spaced

logger = logging.getLogger("moddy.automod.embeddings")

_REFERENCES_PATH = Path(__file__).parent / "data" / "references.json"


def _segment(content: str) -> List[str]:
    """Return the texts to embed for one message (usually just the message).

    A spammed/concatenated message ("je vais te tuer" ×40) embeds poorly: the
    repetition dilutes the vector below threshold. So we only do something extra
    **when an actual repetition is detected** — in that case we additionally
    embed the single de-duplicated unit, scored on its own. Normal messages
    cost exactly one embedding, as before.
    """
    candidates: List[str] = [content]
    norm = normalize_spaced(content)
    collapsed = collapse_repeats(content)
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


class EmbeddingEngine:
    """Holds the normalized reference vectors and scores incoming messages."""

    def __init__(self, embed_fn: EmbedFn):
        self._embed_fn = embed_fn
        self._ref_vectors: List[List[float]] = []
        self._ref_categories: List[str] = []
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

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
