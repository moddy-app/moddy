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
from typing import List


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

    def to_payload(self) -> dict:
        return {
            "cases_total": self.cases_total,
            "sanctions_recentes": self.sanctions_recentes,
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
    """The pipeline's output contract (see docs/AUTOMOD.md §7)."""
    message_id: str
    auteur_id: str
    sanctionnable: bool
    actions: List[str]          # ⊆ {"ban", "mute", "warn", "supprimer"}
    categorie: str
    gravite: str                # basse | moyenne | haute | critique
    raison: str
    confiance: str              # low | medium | high
    signal_source: str          # "regex" | "embedding" | "signalé_par_nano"
    score_detecteur: float      # detector input score
    a_reverifier: List[str] = field(default_factory=list)
