"""
Difficulty routing — put the expensive model only where it earns its keep (S6).

Structurally: **route before you judge**, rather than judge and second-guess. A
free heuristic classifies each message that reaches the decision step into

* ``evident`` — clear-cut; the cheap ``nano`` model handles it (current
  behaviour), with the initial context window;
* ``ambigu`` — subtle (very short, banter between regulars, laughter markers, a
  grey-zone embedding score); the smarter, pricier ``mini`` model handles it with
  twice the context.

No AI call is spent to route — the heuristics below cover the vast majority, and
the golden set (session 3) is what lets us refine them. If one day they plateau,
a 3-token nano "evident/ambigu" pre-call is the planned upgrade — **not**
implemented here (TODO session 6+).

This module is **pure** (no I/O, no Discord, no gateway): it reads the message
text, the routing :class:`~automod.schemas.Signal` and the optional trusted
``relation`` block, and returns a difficulty label. The engine
(``automod/engine.py``) owns *acting* on that label (model + context choice).
See docs/AUTOMOD_AI.md §2quater and docs/AUTOMOD_V2_PLAN.md (Session 6.1/6.2).
"""

from __future__ import annotations

from typing import Optional

from . import constants
from .normalize import has_laughter, normalize_spaced


def _word_count(contenu: str) -> int:
    """Number of meaningful (normalised) words in the message."""
    return len(normalize_spaced(contenu).split())


def difficulte(
    contenu: str,
    signal,
    relation: Optional[dict] = None,
    *,
    severity: int = constants.SEVERITY_DEFAULT,
) -> str:
    """Classify a message routed to the decider as ``evident`` or ``ambigu``.

    ``signal`` is the routing :class:`~automod.schemas.Signal` (its
    ``score_confiance`` is the embedding cosine, or the indicative regex score).
    ``relation`` is the optional trusted relationship block (familiarity +
    reaction) — a familiar pair makes banter plausible, hence "ambigu".

    Order matters: a flagrant regex hit short-circuits to ``evident`` first (a
    clear-cut slur / threat never needs the expensive re-read), then the
    ambiguity signals are tried; anything left is ``evident``.
    """
    threshold = constants.embedding_threshold_for(severity)
    source = getattr(signal, "source", "")
    score = float(getattr(signal, "score_confiance", 0.0) or 0.0)

    # A clear-cut regex hit (indicative score well above threshold): evident.
    if source == constants.SOURCE_REGEX \
            and score >= threshold + constants.ROUTING_EVIDENT_REGEX_MARGIN:
        return constants.DIFFICULTE_EVIDENT

    # Very short messages carry too little to judge cheaply.
    if _word_count(contenu) <= constants.ROUTING_AMBIGU_MAX_WORDS:
        return constants.DIFFICULTE_AMBIGU

    # A familiar pair → banter is plausible; give it the smart model.
    if relation and relation.get("familiarite") in (
            constants.FAMILIARITE_HAUTE, constants.FAMILIARITE_MOYENNE):
        return constants.DIFFICULTE_AMBIGU

    # Laughter markers ("mdr", "😂"…) → probably banter, worth a careful read.
    if has_laughter(contenu):
        return constants.DIFFICULTE_AMBIGU

    # An embedding score sitting right on the threshold is a coin-flip → ambigu.
    if source == constants.SOURCE_EMBEDDING \
            and abs(score - threshold) <= constants.ROUTING_GRAY_ZONE_MARGIN:
        return constants.DIFFICULTE_AMBIGU

    return constants.DIFFICULTE_EVIDENT


def is_ambigu(niveau: str) -> bool:
    return niveau == constants.DIFFICULTE_AMBIGU


def contexte_initial_for(niveau: str) -> int:
    """Initial context window size for a difficulty level (ambigu = ×2)."""
    base = constants.CONTEXTE_INITIAL
    if niveau == constants.DIFFICULTE_AMBIGU:
        return min(base * constants.AMBIGU_CONTEXT_MULTIPLIER, constants.CONTEXTE_MAX)
    return base
