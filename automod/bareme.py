"""
Deterministic sanction scale (barème) — session 2.

nano **qualifies** a message (category + gravity + confidence); this module
**computes the sanction**. The result is 100 % reproducible, auditable and
explainable: given the same verdict, the same recidivism history and the same
guild config, the barème always returns the same *cran* (rung) on a single
sanction ladder, together with a line-by-line breakdown of how it got there.

The module is **pure**: no I/O, no Discord, no DB. The caller
(``modules/automod.py``) gathers the inputs (the member's past sanctions, their
age on the server, the guild severity/config) and translates the returned cran
into Discord actions. This keeps the whole thing table-testable to a dry.

See docs/AUTOMOD.md §2bis and docs/AUTOMOD_V2_PLAN.md (Session 2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 1. The ladder — every sanction is one rung on a single scale
# ---------------------------------------------------------------------------
#
# ``supprimer`` is ALWAYS included: in the ``content`` feature the message is
# always the problem, so it always goes. A cran maps to the Discord actions the
# module applies and the timeout duration in hours (``None`` = no timed
# component, ``0`` = permanent for ban).

CRAN_MIN = 0
CRAN_MAX = 7

# cran -> (actions, duree_heures)
LADDER: Dict[int, Tuple[List[str], Optional[int]]] = {
    0: (["supprimer"], None),            # deletion only
    1: (["warn", "supprimer"], 0),       # warn
    2: (["mute", "supprimer"], 2),       # short mute
    3: (["mute", "supprimer"], 12),      # medium mute
    4: (["mute", "supprimer"], 48),      # long mute
    5: (["mute", "supprimer"], 168),     # very long mute (7 d)
    6: (["mute", "supprimer"], 672),     # maximal mute (28 d)
    7: (["ban", "supprimer"], 0),        # ban (permanent)
}


def ladder_for(cran: int) -> Tuple[List[str], Optional[int]]:
    """Return ``(actions, duree_heures)`` for a cran (clamped to 0..7)."""
    cran = max(CRAN_MIN, min(CRAN_MAX, int(cran)))
    actions, duree = LADDER[cran]
    return list(actions), duree


# ---------------------------------------------------------------------------
# 2. Floor cran per (category × gravity) — the "cold" first-offence policy
# ---------------------------------------------------------------------------

PLANCHER: Dict[Tuple[str, str], int] = {
    ("insulte", "basse"): 1,    ("insulte", "moyenne"): 1,
    ("insulte", "haute"): 2,    ("insulte", "critique"): 3,

    ("harcelement", "basse"): 1,    ("harcelement", "moyenne"): 2,
    ("harcelement", "haute"): 3,    ("harcelement", "critique"): 5,

    ("menace", "basse"): 2,     ("menace", "moyenne"): 3,
    ("menace", "haute"): 6,     ("menace", "critique"): 7,

    ("haine_discrimination", "basse"): 2,    ("haine_discrimination", "moyenne"): 4,
    ("haine_discrimination", "haute"): 6,    ("haine_discrimination", "critique"): 7,

    ("incitation_automutilation", "basse"): 3,    ("incitation_automutilation", "moyenne"): 5,
    ("incitation_automutilation", "haute"): 7,    ("incitation_automutilation", "critique"): 7,

    ("harcelement_sexuel", "basse"): 3,    ("harcelement_sexuel", "moyenne"): 5,
    ("harcelement_sexuel", "haute"): 7,    ("harcelement_sexuel", "critique"): 7,

    ("doxxing", "basse"): 4,    ("doxxing", "moyenne"): 6,
    ("doxxing", "haute"): 7,    ("doxxing", "critique"): 7,

    ("arnaque_scam", "basse"): 2,    ("arnaque_scam", "moyenne"): 4,
    ("arnaque_scam", "haute"): 7,    ("arnaque_scam", "critique"): 7,

    ("violation_indications", "basse"): 0,    ("violation_indications", "moyenne"): 1,
    ("violation_indications", "haute"): 2,    ("violation_indications", "critique"): 4,
}

# Fallback floor when nano returns an unknown/empty (category, gravity) pair but
# still flagged the message sanctionnable — deletion only, never a heavy action.
PLANCHER_DEFAUT = 0

# Categories that are never softened and always act: the veteran-clemency bonus
# is refused for these at gravity haute+, and (deletion aside) they are the hard
# floor of the policy.
CATEGORIES_SENSIBLES = frozenset({
    "incitation_automutilation", "doxxing", "harcelement_sexuel",
})


# ---------------------------------------------------------------------------
# 3. Recidivism — weighted points with exponential (half-life) decay
# ---------------------------------------------------------------------------

POINTS_GRAVITE = {"basse": 1.0, "moyenne": 3.0, "haute": 7.0, "critique": 15.0}
DEMI_VIE_JOURS = 45.0

# Reliability of the sanction's source: a human weighs more than an automod, and
# an appeal is the tie-breaker.
POIDS_SOURCE = {
    "manuel":                1.5,   # posted by a human moderator
    "automod_confirme":      1.25,  # automod sanction whose appeal was REFUSED
    "automod":               1.0,   # automod sanction, never contested
    "automod_appel_accepte": 0.0,   # appeal ACCEPTED => false positive => 0 point
}

# Specialisation aggravates: re-offending in the same category counts for more.
MULT_MEME_CATEGORIE = 1.5


@dataclass
class SanctionPassee:
    """One past sanction, as consumed by the recidivism engine.

    ``source_fiabilite`` is a key of :data:`POIDS_SOURCE` (an accepted appeal
    yields ``automod_appel_accepte`` → weight 0, so a proven false positive never
    counts). The caller derives it from the sanction issuer + the appeal state.
    """
    categorie: str
    gravite: str
    date: datetime
    source_fiabilite: str = "automod"


def points_actifs(
    sanctions: List[SanctionPassee],
    categorie_courante: str,
    now: Optional[datetime] = None,
) -> float:
    """Sum of decayed, source-weighted recidivism points for a member."""
    now = now or datetime.now(timezone.utc)
    total = 0.0
    for s in sanctions:
        base = POINTS_GRAVITE.get(s.gravite, 0.0)
        if base <= 0.0:
            continue
        age_jours = (now - _aware(s.date)).total_seconds() / 86400.0
        if age_jours < 0:
            age_jours = 0.0
        decay = 0.5 ** (age_jours / DEMI_VIE_JOURS)
        poids = POIDS_SOURCE.get(s.source_fiabilite, 1.0)
        meme_cat = MULT_MEME_CATEGORIE if (s.categorie and s.categorie == categorie_courante) else 1.0
        total += base * decay * poids * meme_cat
    return total


def crans_recidive(points: float) -> int:
    """How many extra crans the active recidivism points add to the floor."""
    if points >= 40:
        return 3
    if points >= 15:
        return 2
    if points >= 5:
        return 1
    return 0


# ---------------------------------------------------------------------------
# 4. Guild config knobs
# ---------------------------------------------------------------------------

# Hard ceiling a guild can impose on what the automod is allowed to do.
PLAFOND_CONFIG = {"warn": 1, "mute": 6, "ban": 7}
MAX_ACTION_DEFAULT = "ban"

# Severity (1–5) global shift.
_SEVERITE_SHIFT = {1: -1, 2: 0, 3: 0, 4: 0, 5: 1}


@dataclass
class MembreInfo:
    """The member facts the barème needs (all trivially available in the event)."""
    anciennete_jours: float = 0.0


@dataclass
class ConfigBareme:
    """Guild-level barème configuration."""
    max_action: str = MAX_ACTION_DEFAULT
    #: Categories the guild opted out of AI sanctioning (kill-switch, annexe 5):
    #: a disabled category is capped to deletion only (cran 0).
    categories_desactivees: Tuple[str, ...] = ()

    @property
    def plafond_cran(self) -> int:
        return PLAFOND_CONFIG.get(self.max_action, CRAN_MAX)


# ---------------------------------------------------------------------------
# 5. The computation + its explainable breakdown
# ---------------------------------------------------------------------------

@dataclass
class Composante:
    """One line of the sanction breakdown (a step in the computation)."""
    code: str          # stable machine code (mapped to i18n by the caller)
    delta: int         # signed change this step applied to the running cran
    detail: str = ""   # short human hint (e.g. "insulte/moyenne", "8.2 pts")


@dataclass
class ResultatBareme:
    """The barème's output: the final cran, its actions and how it was reached."""
    cran: int
    actions: List[str]
    duree_heures: Optional[int]
    categorie: str
    gravite: str
    points_recidive: float
    composantes: List[Composante] = field(default_factory=list)
    #: cran ≥ 6 (mute 672 h / ban) decided purely by automod → flag for review
    #: (session-6 mini confirmation; today it only highlights the alert card).
    needs_review: bool = False

    @property
    def sanction_reelle(self) -> bool:
        """Whether a real sanction (beyond deletion) is applied."""
        return any(a in ("warn", "mute", "ban") for a in self.actions)


def calculer(
    verdict,
    sanctions_passees: List[SanctionPassee],
    membre: MembreInfo,
    *,
    severite_guild: int = 3,
    config: Optional[ConfigBareme] = None,
    now: Optional[datetime] = None,
) -> ResultatBareme:
    """Compute the sanction cran for a qualified verdict.

    ``verdict`` is any object carrying ``categorie`` / ``gravite`` / ``confiance``
    (an :class:`automod.schemas.Decision` fits). ``sanctions_passees`` is the
    member's recent history, ``membre`` their server tenure, ``severite_guild``
    the 1–5 dial and ``config`` the guild ceiling / kill-switches.

    Returns a :class:`ResultatBareme` whose ``composantes`` explain the cran
    line by line — the whole point of the deterministic barème.
    """
    config = config or ConfigBareme()
    now = now or datetime.now(timezone.utc)
    categorie = getattr(verdict, "categorie", "") or ""
    gravite = getattr(verdict, "gravite", "basse") or "basse"
    confiance = getattr(verdict, "confiance", "low") or "low"

    composantes: List[Composante] = []

    # Kill-switch: a disabled category never earns more than deletion.
    if categorie in set(config.categories_desactivees):
        composantes.append(Composante("categorie_desactivee", 0, categorie))
        actions, duree = ladder_for(0)
        return ResultatBareme(
            cran=0, actions=actions, duree_heures=duree,
            categorie=categorie, gravite=gravite, points_recidive=0.0,
            composantes=composantes,
        )

    # -- Floor -----------------------------------------------------------
    plancher = PLANCHER.get((categorie, gravite), PLANCHER_DEFAUT)
    cran = plancher
    composantes.append(Composante("plancher", plancher, f"{categorie or '?'}/{gravite}"))

    # -- Recidivism ------------------------------------------------------
    points = points_actifs(sanctions_passees, categorie, now)
    add_recidive = crans_recidive(points)
    cran += add_recidive
    if add_recidive:
        composantes.append(Composante("recidive", add_recidive, f"{points:.1f} pts"))

    # -- a) Guild severity (global shift) --------------------------------
    sev = _clamp_severite(severite_guild)
    shift = _SEVERITE_SHIFT[sev]
    cran += shift
    if shift:
        composantes.append(Composante("severite", shift, f"serveur {sev}"))

    # -- b) nano confidence cap (never mute/ban on weak confidence) ------
    #    low  => at worst a warn (cran 1); medium => never mute >48 h nor ban.
    ceiling = {"low": 1, "medium": 4}.get(confiance)
    if ceiling is not None and cran > ceiling:
        composantes.append(Composante("confiance", ceiling - cran, confiance))
        cran = ceiling

    # -- c) Veteran clemency (never on sensitive categories / high gravity)
    veteran = (
        membre.anciennete_jours >= 90
        and not sanctions_passees
        and gravite in ("basse", "moyenne")
        and categorie not in CATEGORIES_SENSIBLES
    )
    if veteran and cran > 0:
        cran -= 1
        composantes.append(Composante("veteran", -1, "≥90 j, casier vierge"))

    # -- d) Fresh-account malus ------------------------------------------
    if membre.anciennete_jours < 7:
        cran += 1
        composantes.append(Composante("compte_recent", 1, "<7 j"))

    # -- e) Guild hard ceiling (max_action) ------------------------------
    plafond = config.plafond_cran
    if cran > plafond:
        delta = plafond - cran
        cran = plafond
        composantes.append(Composante("plafond", delta, config.max_action))

    # -- Clamp (keep the breakdown honest: record it if it bites) --------
    borne = max(CRAN_MIN, min(CRAN_MAX, cran))
    if borne != cran:
        composantes.append(Composante("borne", borne - cran, ""))
        cran = borne

    actions, duree = ladder_for(cran)
    return ResultatBareme(
        cran=cran, actions=actions, duree_heures=duree,
        categorie=categorie, gravite=gravite, points_recidive=points,
        composantes=composantes, needs_review=cran >= 6,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clamp_severite(value) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return 3
    return max(1, min(5, v))


def _aware(dt: datetime) -> datetime:
    """Coerce a naive datetime to UTC-aware so arithmetic never raises."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


