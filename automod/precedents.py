"""
Server precedents (jurisprudence RAG) — the pure matching core (session 7).

The automod learns each server's **local culture** from the human rulings it
already produces (accepted / refused appeals, shadow ``✅ correct`` / ``❌ faux
positif`` clicks). Each ruling is stored as a :class:`Precedent`: the message,
the human verdict, and the embedding the funnel already computed at detection
time. Before a decision call, the current message's embedding is matched against
the guild's precedents; strong matches are injected into nano/mini as trusted
server data, and a near-identical ``non_sanctionnable`` precedent short-circuits
the call entirely.

Like the rest of ``automod/`` this module is **pure and side-effect-free**: it
does no I/O, no Discord, no DB, no embedding call. It receives an already-computed
(normalised) query vector plus the guild's already-loaded precedents and returns
ranked matches. Storage/loading lives in the repository/service; capturing the
message embedding lives in the engine — this file only ranks and decides *how*
the matches inform the verdict. See docs/AUTOMOD_AI.md §2quinquies.
"""

from __future__ import annotations

import array
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from . import constants


def _empty_vector() -> "array.array":
    return array.array("f")


@dataclass
class Precedent:
    """A past message of THIS server together with the final human ruling.

    ``vector`` is the **normalised** embedding of the message (unit length), so a
    cosine similarity against another normalised vector is a plain dot product.
    It is a float32 ``array.array`` rather than a ``list[float]`` — same precision
    as the on-disk format, ~8x less memory, and a drop-in Sequence for the
    matching below. Any Sequence of floats still works here; the type is only a
    memory choice, never a correctness one.
    """
    id: str
    message: str                 # the (normalised) message text, for display
    verdict_humain: str          # "non_sanctionnable" | "sanctionnable"
    vector: Sequence[float] = field(default_factory=_empty_vector)
    categorie: str = ""
    gravite: str = ""
    source: str = ""


@dataclass
class PrecedentMatch:
    """A precedent that matched the current message, with its similarity."""
    precedent: Precedent
    similarite: float


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def cosine(query_vec: Sequence[float], vector: Sequence[float]) -> float:
    """Cosine similarity of two **normalised** vectors (a plain dot product).

    Both operands are expected to be unit-normalised (the engine normalises the
    query; precedents are stored normalised), so no re-normalisation is done here.
    Returns ``0.0`` on a length mismatch or an empty vector rather than raising.
    """
    if not query_vec or not vector or len(query_vec) != len(vector):
        return 0.0
    return _dot(query_vec, vector)


def match(
    query_vec: Sequence[float],
    precedents: List[Precedent],
    *,
    top_k: Optional[int] = None,
    min_sim: Optional[float] = None,
) -> List[PrecedentMatch]:
    """Return the guild's precedents most similar to ``query_vec``.

    Only matches at or above ``min_sim`` (default
    :data:`constants.PRECEDENT_MIN_SIMILARITE`) are kept, sorted by descending
    similarity and capped at ``top_k`` (default
    :data:`constants.PRECEDENT_TOP_K`). Pure — no I/O, deterministic.
    """
    if not query_vec or not precedents:
        return []
    top_k = constants.PRECEDENT_TOP_K if top_k is None else top_k
    min_sim = constants.PRECEDENT_MIN_SIMILARITE if min_sim is None else min_sim

    scored: List[PrecedentMatch] = []
    for p in precedents:
        sim = cosine(query_vec, p.vector)
        if sim >= min_sim:
            scored.append(PrecedentMatch(p, sim))
    scored.sort(key=lambda m: m.similarite, reverse=True)
    return scored[:top_k]


def deterministic_shortcut(matches: List[PrecedentMatch]) -> Optional[PrecedentMatch]:
    """The top match iff it warrants stopping before the decision call.

    A precedent at/above :data:`constants.PRECEDENT_SHORTCUT_SIMILARITE` whose
    human verdict is ``non_sanctionnable`` means a moderator already ruled on
    near-identical text on this exact server — replaying the model would only
    risk contradicting them and cost a call. Only the **not-sanctionnable**
    direction short-circuits: a "sanctionnable" precedent still goes through the
    model (the barème/recidivism must be recomputed for the current author).
    """
    if not matches:
        return None
    top = matches[0]
    if (top.similarite >= constants.PRECEDENT_SHORTCUT_SIMILARITE
            and top.precedent.verdict_humain == constants.PRECEDENT_NON_SANCTIONNABLE):
        return top
    return None


def to_prompt_payload(matches: List[PrecedentMatch]) -> List[dict]:
    """Render matches for nano's user payload (``precedents_serveur``).

    Only the fields nano needs: the past message, the human ruling and the
    similarity. Ordered strongest-first. The message is truncated defensively.
    """
    return [
        {
            "message": (m.precedent.message or "")[:200],
            "verdict_moderateurs": m.precedent.verdict_humain,
            "similarite": round(float(m.similarite), 2),
        }
        for m in matches
    ]
