"""
Dataclasses for the automod pipeline.

These types are deliberately Discord-agnostic: the calling module converts
``discord.Message`` objects into :class:`TargetMessage` / :class:`ContextMessage`
and provides :class:`AuthorHistory`. The pipeline only ever sees plain strings
and opaque ids, which keeps it testable and limits the prompt-injection surface.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class TargetMessage:
    """The single message under judgement."""
    id: str
    author_id: str
    content: str


@dataclass
class ContextMessage:
    """A message preceding the target in the same channel (opaque ids)."""
    id: str
    author_id: str
    content: str


@dataclass
class AuthorHistory:
    """Moderation history of the target's author, supplied by the caller."""
    cases_total: int = 0
    # Each item: {"type": str, "date": "YYYY-MM-DD", "raison": str}
    sanctions_recentes: List[dict] = field(default_factory=list)
    # Messages of this author that ALREADY received a sanction (so nano never
    # re-punishes the current message for conduct that belongs to an earlier
    # one). Each item: {"id": str, "extrait": str}.
    messages_deja_moderes: List[dict] = field(default_factory=list)

    def to_payload(self) -> dict:
        return {
            "cases_total": self.cases_total,
            "sanctions_recentes": self.sanctions_recentes,
            "messages_deja_moderes": self.messages_deja_moderes,
        }


@dataclass
class Signal:
    """A detection signal handed to nano. Never decisional on its own."""
    source: str            # "regex" | "embedding" | "signalé_par_nano"
    categorie: str
    score_confiance: float

    def to_payload(self) -> dict:
        return {
            "source": self.source,
            "categorie": self.categorie,
            "score_confiance": round(float(self.score_confiance), 4),
        }


@dataclass
class BlocklistEntry:
    """A compiled regex entry of the explicit-term blocklist."""
    pattern: re.Pattern
    categorie: str
    gravite_indicative: str   # "basse" | "moyenne" | "haute"
    compact: bool = False     # match against the separator-stripped form


@dataclass
class Decision:
    """The pipeline's output contract (see docs/AUTOMOD.md §2)."""
    message_id: str
    auteur_id: str
    sanctionnable: bool
    # ⊆ {"ban", "mute", "warn", "supprimer"}. In v2 these are decided by the
    # deterministic barème (automod/bareme.py), NOT by nano: the pipeline leaves
    # them empty and the module fills them from the barème's cran.
    actions: List[str]
    categorie: str              # canonical FR category (see nano.CATEGORIES)
    gravite: str                # basse | moyenne | haute | critique
    raison: str                 # FACTUAL only: what the message contains / which rule it breaks
    explication: str            # 1–2 sentences: WHY this decision (the reasoning)
    confiance: str              # low | medium | high
    signal_source: str          # "regex" | "embedding" | "signalé_par_nano"
    score_detecteur: float      # detector input score
    a_reverifier: List[str] = field(default_factory=list)
    duree_heures: int = 0       # sanction duration in hours (0 = permanent); barème-owned in v2
    # v2 grounding contract (docs/AUTOMOD.md §2).
    citation: str = ""          # verbatim substring of the target that justifies the category
    cible: str = "aucune"       # "membre" | "auteur_lui_meme" | "groupe" | "aucune"
    # Set when a deterministic grounding guard voided an otherwise-sanctionnable
    # verdict (motif, e.g. "grounding_citation_absente"). None when clean. Kept
    # for logs / the alert card so avoided false positives are observable.
    rejet_grounding: Optional[str] = None
    # Anti-fragmentation (session 4): when this decision was reached by judging
    # the CONCATENATION of several consecutive messages by the same author
    # (fragmented harassment), these carry the fragment message ids the caller
    # must act on (delete all N) and the combined text that was judged. Empty for
    # an ordinary single-message decision.
    agregat_de: List[str] = field(default_factory=list)
    agregat_contenu: str = ""
    # Difficulty routing (session 6): which model produced this verdict —
    # "nano" (evident) or "mini" (ambigu). Drives the heavy-sanction confirmation
    # (§6.3): only a nano-decided heavy cran needs a mini senior review.
    decideur: str = "nano"
    # Calibrated confidence (annexe A.2, TODO): if the gateway ever exposes token
    # logprobs, derive confidence from the probability of the `sanctionnable`
    # token instead of the model's self-report. Interface only — left None until
    # the gateway can supply logprobs.
    confiance_calibree: Optional[float] = None
    # Server precedents (session 7): set when a near-identical `non_sanctionnable`
    # precedent short-circuited the decision before any model call (deterministic
    # shortcut, §7.3). Carries {similarite, message} for logs / the card. None
    # when no precedent shortcut fired.
    precedent_applique: Optional[dict] = None
