"""
Relationship graph & target-reaction signals (session 5).

Two facts the raw message text never carries: **who is talking to whom**
(familiarity) and **how the target reacted**. Per-pair counters are fed passively
by existing Discord listeners (a reply / mention, a laughter-style reaction —
~0 cost) and stored in Redis with a 60-day TTL; on read they decay (half-life 30
days) into a single readable ``familiarite`` level. A short post-message window
then classifies the target's reaction. Both are injected into nano's payload as
**trusted server data** (never user text) so nano can tell humour from a genuine
intent to harm — the "banter vs will to hurt" problem.

Design: the scoring (:func:`familiarite`) and the reaction classifier
(:func:`classify_target_reaction`) are **pure and table-testable**; only
:class:`RelationStore` touches Redis, and it is inert (returns neutral data)
without it. Familiarity only ever **attenuates** a verdict — never aggravates —
so a missing / empty relation can only make nano stricter, never laxer.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable, List, Optional

from . import constants
from .normalize import has_laughter as _has_laughter

# --------------------------------------------------------------------------- #
# Positive-reaction markers
# --------------------------------------------------------------------------- #
#
# The laughter *markers* themselves (word + emoji forms) live in
# ``constants.RIRE_MOTS`` / ``RIRE_EMOJIS`` — the single source of truth shared
# with the difficulty router (§6.1). ``normalize.has_laughter`` is the one
# detector both call. Only the friendly-*reaction* set (a superset that also
# counts 👍❤️… as a positive signal on a message) is local to this module.

# Positive / laughter reactions that count as a friendly signal on a message
# (plan §5.1: 😂 👍 ❤️ 😭 …). Kept small and unambiguous.
_POSITIVE_REACTIONS = frozenset({
    "😂", "🤣", "😭", "👍", "❤️", "❤", "🥰", "😍", "🙏", "🤝", "💀", "😹",
})


def is_positive_emoji(emoji: str) -> bool:
    """True if a reaction emoji is a friendly / laughter signal (unicode only)."""
    return bool(emoji) and emoji in _POSITIVE_REACTIONS


def classify_target_reaction(
    reply_texts: Iterable[str],
    *,
    target_gone: bool = False,
    aggressive_hits: int = 0,
) -> str:
    """Classify the target's reaction into one of the four signals.

    Priority: a target who deleted their messages / left the channel signals
    ``detresse_possible`` (treat the message strictly); otherwise a laughter
    marker in any reply means ``banter_reciproque`` (the target laughed along);
    otherwise a reply that itself trips the blocklist means ``conflit_reciproque``
    (same aggressive tone, no laughter); otherwise ``aucune``.
    """
    if target_gone:
        return constants.REACTION_DETRESSE
    replies = list(reply_texts or [])
    if any(_has_laughter(r) for r in replies):
        return constants.REACTION_BANTER
    if aggressive_hits > 0:
        return constants.REACTION_CONFLIT
    return constants.REACTION_AUCUNE


# --------------------------------------------------------------------------- #
# Per-pair counters + decayed familiarity score (pure)
# --------------------------------------------------------------------------- #

@dataclass
class RelationCounters:
    """Raw per-pair counters (already summed, not yet decayed)."""
    interactions: float = 0.0
    reponses_mutuelles: float = 0.0
    reactions_positives: float = 0.0
    premier_contact_ts: float = 0.0
    dernier_contact_ts: float = 0.0


def _decay_factor(dernier_contact_ts: float, now: float) -> float:
    """Exponential decay from the last contact (half-life 30 days)."""
    if not dernier_contact_ts:
        return 1.0
    days = max(0.0, (now - dernier_contact_ts) / 86400.0)
    return 0.5 ** (days / constants.RELATION_DECAY_HALFLIFE_DAYS)


def familiarite(counters: RelationCounters, now: Optional[float] = None) -> str:
    """Derive a readable familiarity level from decayed weighted counters.

    ``score = (interactions + 2·mutual replies + 3·positive reactions) · decay``.
    ``haute`` additionally requires the pair to be at least
    ``RELATION_MIN_AGE_DAYS_FOR_HAUTE`` days old.
    """
    now = time.time() if now is None else now
    decay = _decay_factor(counters.dernier_contact_ts, now)
    score = (
        counters.interactions
        + 2.0 * counters.reponses_mutuelles
        + 3.0 * counters.reactions_positives
    ) * decay
    age_days = (
        (now - counters.premier_contact_ts) / 86400.0
        if counters.premier_contact_ts else 0.0
    )
    anciennete_ok = age_days >= constants.RELATION_MIN_AGE_DAYS_FOR_HAUTE
    if score >= constants.RELATION_SCORE_HAUTE and anciennete_ok:
        return constants.FAMILIARITE_HAUTE
    if score >= constants.RELATION_SCORE_MOYENNE:
        return constants.FAMILIARITE_MOYENNE
    if score >= constants.RELATION_SCORE_FAIBLE:
        return constants.FAMILIARITE_FAIBLE
    return constants.FAMILIARITE_AUCUNE


def build_relation_payload(
    counters: RelationCounters,
    reaction_cible: str,
    now: Optional[float] = None,
) -> dict:
    """The ``relation`` object injected into nano's user payload (trusted data)."""
    return {
        "familiarite": familiarite(counters, now),
        "interactions_30j": int(round(counters.interactions)),
        "reciprocite": counters.reponses_mutuelles > 0,
        "reaction_cible": reaction_cible or constants.REACTION_AUCUNE,
    }


# --------------------------------------------------------------------------- #
# Redis-backed store (I/O; inert without Redis)
# --------------------------------------------------------------------------- #

class RelationStore:
    """Reads/writes the per-pair relationship counters in Redis.

    Key ``rel:{guild}:{min(a,b)}:{max(a,b)}`` → a hash of counters plus the two
    directed last-contact timestamps (``lc:{uid}``) used to detect reciprocity.
    Every write refreshes the 60-day TTL. Without a Redis client the store is
    inert: reads return neutral counters, writes are no-ops.
    """

    def __init__(self, redis):
        self.redis = redis

    @staticmethod
    def _key(guild_id: int, a, b) -> str:
        lo, hi = sorted((int(a), int(b)))
        return f"rel:{guild_id}:{lo}:{hi}"

    @staticmethod
    def _decode(mapping) -> dict:
        out = {}
        for k, v in (mapping or {}).items():
            k = k.decode() if isinstance(k, bytes) else str(k)
            v = v.decode() if isinstance(v, bytes) else v
            out[k] = v
        return out

    async def get(self, guild_id: int, a, b) -> RelationCounters:
        if self.redis is None or int(a) == int(b):
            return RelationCounters()
        try:
            raw = self._decode(await self.redis.hgetall(self._key(guild_id, a, b)))
        except Exception:
            return RelationCounters()

        def _f(name: str) -> float:
            try:
                return float(raw.get(name, 0) or 0)
            except (TypeError, ValueError):
                return 0.0

        return RelationCounters(
            interactions=_f("interactions"),
            reponses_mutuelles=_f("reponses_mutuelles"),
            reactions_positives=_f("reactions_positives"),
            premier_contact_ts=_f("premier_contact_ts"),
            dernier_contact_ts=_f("dernier_contact_ts"),
        )

    async def record_message(self, guild_id: int, author, other,
                             now: Optional[float] = None) -> None:
        """``author`` addressed ``other`` (a reply or a mention).

        Bumps ``interactions``; if ``other`` had addressed ``author`` within the
        mutual window just before, also bumps ``reponses_mutuelles`` (a genuine
        back-and-forth). Records the directed last-contact timestamp so the next
        message the other way can detect reciprocity.
        """
        if self.redis is None or int(author) == int(other):
            return
        now = time.time() if now is None else now
        key = self._key(guild_id, author, other)
        try:
            await self.redis.hincrby(key, "interactions", 1)
            await self.redis.hsetnx(key, "premier_contact_ts", str(now))
            last_other = await self.redis.hget(key, f"lc:{int(other)}")
            if last_other is not None:
                last_other = last_other.decode() if isinstance(last_other, bytes) else last_other
                try:
                    delta = now - float(last_other)
                except (TypeError, ValueError):
                    delta = None
                if delta is not None and 0 <= delta <= constants.RELATION_MUTUAL_WINDOW_SECONDS:
                    await self.redis.hincrby(key, "reponses_mutuelles", 1)
            await self.redis.hset(key, f"lc:{int(author)}", str(now))
            await self.redis.hset(key, "dernier_contact_ts", str(now))
            await self.redis.expire(key, constants.RELATION_TTL_SECONDS)
        except Exception:
            pass

    async def record_positive_reaction(self, guild_id: int, reactor, author,
                                       now: Optional[float] = None) -> None:
        """``reactor`` put a friendly / laughter reaction on ``author``'s message."""
        if self.redis is None or int(reactor) == int(author):
            return
        now = time.time() if now is None else now
        key = self._key(guild_id, reactor, author)
        try:
            await self.redis.hincrby(key, "reactions_positives", 1)
            await self.redis.hsetnx(key, "premier_contact_ts", str(now))
            await self.redis.hset(key, "dernier_contact_ts", str(now))
            await self.redis.expire(key, constants.RELATION_TTL_SECONDS)
        except Exception:
            pass
