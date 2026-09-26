"""
Deterministic policies of the automod image features — pure and table-tested.

* :func:`safesearch_to_verdict` — Google SafeSearch likelihoods → an
  ``image_nsfw`` qualification (sanction / doubt / nothing). No model decides
  an NSFW image: the likelihood scale is the whole policy.
* :func:`pacing_allows` — spends the monthly SafeSearch allowance (shared by
  all of Moddy) smoothly over the month instead of burning it in three days,
  keeping headroom for high-risk posters.
* :func:`scam_risk_score` — the free pre-rules deciding whether an image is
  worth an OCR call in the crypto-scam pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from . import constants

_LIKELIHOOD_RANK = {
    "UNKNOWN": 0, "VERY_UNLIKELY": 1, "UNLIKELY": 2,
    "POSSIBLE": 3, "LIKELY": 4, "VERY_LIKELY": 5,
}


def _rank(value: Optional[str]) -> int:
    return _LIKELIHOOD_RANK.get((value or "UNKNOWN").upper(), 0)


# --------------------------------------------------------------------------- #
# SafeSearch → qualification
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class NsfwVerdict:
    sanctionnable: bool
    gravite: str = "basse"
    confiance: str = "low"
    doute: Optional[str] = None     # motif when the team should look at it

    @property
    def actionable(self) -> bool:
        """Something to do: a sanction, or a doubt for the labeling queue."""
        return self.sanctionnable or self.doute is not None


def safesearch_to_verdict(likelihoods: dict) -> NsfwVerdict:
    """Map SafeSearch likelihoods onto an automod qualification.

    * ``adult VERY_LIKELY`` → sanctionable, gravity haute, confidence high.
    * ``adult LIKELY``      → sanctionable, gravity moyenne, confidence medium
      (the barème caps a medium-confidence verdict at mute 48 h).
    * ``adult POSSIBLE`` / ``racy VERY_LIKELY`` / ``violence VERY_LIKELY`` →
      no sanction, but a doubt the Moddy team labels.
    * anything else → nothing.
    """
    adult = _rank(likelihoods.get("adult"))
    racy = _rank(likelihoods.get("racy"))
    violence = _rank(likelihoods.get("violence"))
    if adult >= _LIKELIHOOD_RANK["VERY_LIKELY"]:
        return NsfwVerdict(True, "haute", "high")
    if adult >= _LIKELIHOOD_RANK["LIKELY"]:
        return NsfwVerdict(True, "moyenne", "medium")
    if adult >= _LIKELIHOOD_RANK["POSSIBLE"]:
        return NsfwVerdict(False, doute="safesearch_adult_possible")
    if racy >= _LIKELIHOOD_RANK["VERY_LIKELY"]:
        return NsfwVerdict(False, doute="safesearch_racy")
    if violence >= _LIKELIHOOD_RANK["VERY_LIKELY"]:
        return NsfwVerdict(False, doute="safesearch_violence")
    return NsfwVerdict(False)


# --------------------------------------------------------------------------- #
# Monthly SafeSearch pacing
# --------------------------------------------------------------------------- #

TIER_PRIORITAIRE = "prioritaire"
TIER_NORMAL = "normal"


def allowance_at(cap: int, month_start: float, month_end: float, now: float,
                 burst: int = constants.SAFESEARCH_BURST) -> float:
    """Units the month may have spent by ``now`` (linear + a small head start)."""
    span = max(month_end - month_start, 1.0)
    fraction = min(max((now - month_start) / span, 0.0), 1.0)
    return min(float(cap), cap * fraction + burst)


def pacing_allows(used: float, cap: int, month_start: float, month_end: float,
                  now: float, tier: str = TIER_NORMAL, *,
                  burst: int = constants.SAFESEARCH_BURST,
                  normal_ratio: float = constants.SAFESEARCH_NORMAL_TIER_RATIO) -> bool:
    """Whether one more SafeSearch call fits the smoothed monthly budget.

    A priority image (new account / newcomer / stranger to everyone) may spend up
    to the full smoothed allowance; an ordinary one only while consumption is
    under ``normal_ratio`` of it, so the headroom is kept for the risky posters.
    The hard cap itself is enforced by the gateway (fail-closed rule).
    """
    if cap <= 0 or used >= cap:
        return False
    allowed = allowance_at(cap, month_start, month_end, now, burst)
    if tier == TIER_PRIORITAIRE:
        return used < allowed
    return used < allowed * normal_ratio


# --------------------------------------------------------------------------- #
# Scam pre-rules
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ScamRiskInput:
    account_age_days: Optional[float] = None   # Discord account age
    member_age_days: Optional[float] = None    # time on this server
    # Best known familiarity with anyone ("aucune" = a known stranger). None =
    # unknown, which adds nothing: an unknown is not evidence of anything.
    familiarite: Optional[str] = None
    image_count: int = 1
    text_empty: bool = False
    text_has_link: bool = False
    text_scam_score: float = 0.0               # anchors on the message text itself
    cross_post: int = 0                        # channels the image appeared in
    looks_like_screenshot: bool = False


def scam_risk_score(x: ScamRiskInput) -> Tuple[int, Tuple[str, ...]]:
    """Free pre-rules: the higher, the more an OCR call is worth it.

    Returns ``(score, reasons)``. The image pipeline OCRs when the score reaches
    ``SCAM_RISK_THRESHOLD`` (or the guild enabled ``scan_all``).
    """
    score = 0
    reasons = []
    if x.account_age_days is not None and x.account_age_days < 30:
        score += 2
        reasons.append("compte_recent")
    if x.member_age_days is not None and x.member_age_days < 7:
        score += 2
        reasons.append("arrivee_recente")
    if x.familiarite == "aucune":
        score += 1
        reasons.append("inconnu")
    if x.image_count >= 2:
        score += 1
        reasons.append("plusieurs_images")
    if x.text_empty or x.text_has_link or x.text_scam_score > 0:
        score += 1
        reasons.append("texte_suspect")
    if x.cross_post >= constants.CROSSPOST_MIN_CHANNELS:
        score += 3
        reasons.append("cross_post")
    if x.looks_like_screenshot:
        score += 1
        reasons.append("capture_ecran")
    return score, tuple(reasons)


def nsfw_tier(account_age_days: Optional[float], member_age_days: Optional[float],
              familiarite: Optional[str]) -> str:
    """Priority tier for SafeSearch: newcomers and strangers first."""
    if account_age_days is not None and account_age_days < 30:
        return TIER_PRIORITAIRE
    if member_age_days is not None and member_age_days < 7:
        return TIER_PRIORITAIRE
    if familiarite == "aucune":
        return TIER_PRIORITAIRE
    return TIER_NORMAL
