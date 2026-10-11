"""
Infraction types — the single source of truth for what the Automod AI can sanction.

Every sanction the automod applies is tied to a pre-declared *infraction type*
(an insult, a threat, an explicit avatar…). Each type carries Moddy's default
policy (the "cold" first-offence floor per gravity, whether it is sensitive or
banned on sight) and can be overridden per server through the ``sanctions``
config key (see :func:`normalize_sanctions`).

The module is **pure**: no I/O, no Discord import. ``automod/bareme.py`` derives
its legacy tables (``PLANCHER``, ``CATEGORIES_SENSIBLES``,
``CATEGORIES_BAN_DIRECT``) from the catalogue below, so adding a type here is
the only edit needed to make the barème aware of it.

Families
--------
* ``message`` / ``image``: categories nano / SafeSearch can return.
* ``profil``: avatar / display-name checks (applied by the profile feature).
* ``perso``: server-defined custom infractions, ids ``perso:<id>`` where
  ``<id>`` matches ``[a-z0-9]{1,12}``. Not in the static catalogue —
  :func:`get` synthesizes them.

Config schema (``guilds.data.modules.automod_ai.sanctions``)::

    "sanctions": { "<infraction_id>": {
        "enabled": true,              # false = deletion only (kill-switch)
        "mode": "auto" | "perso",     # absent entry == "auto" == Moddy defaults
        "crans": {"basse": 0..7, "moyenne": 0..7, "haute": 0..7, "critique": 0..7},
        "recidive": true,             # perso mode only: keep recidivism escalation
        "action_profil": "hold" | "alert" | "rename_warn" | "bareme"   # profil types
    } }

Profile actions (``action_profil``):

* ``hold``        — timeout the member until the profile is fixed.
* ``alert``       — alert card only, no sanction.
* ``rename_warn`` — reset the nickname and warn the member.
* ``bareme``      — use the cran ladder like a message infraction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

GRAVITES: Tuple[str, ...] = ("basse", "moyenne", "haute", "critique")

FAMILLES = ("message", "image", "profil", "perso")

MODES = ("auto", "perso")

#: Profile actions an admin can pick for a ``profil`` infraction.
ACTIONS_PROFIL = ("hold", "alert", "rename_warn", "bareme")
# Profile types a hold can apply to (a hold tracks an avatar/banner asset; a
# name has nothing to "fix" that Moddy could re-check by hash).
HOLD_TYPES = ("profil_avatar_nsfw", "profil_avatar_choquant")

CRAN_MIN = 0
CRAN_MAX = 7

PERSO_PREFIX = "perso:"
_PERSO_ID_RE = re.compile(r"^[a-z0-9]{1,12}$")


@dataclass(frozen=True)
class InfractionType:
    """One declared infraction type and its Moddy default policy."""
    id: str
    famille: str
    #: gravity -> default floor cran (first offence)
    plancher: Dict[str, int]
    #: never softened (no veteran clemency at haute+)
    sensible: bool = False
    #: banned on sight (medium-confidence cap and veteran clemency never apply)
    ban_direct: bool = False
    #: name of a constant in ``utils/emojis.py``
    emoji_key: str = "INFO"
    #: profil types only: default ``action_profil`` (see module docstring)
    default_profile_action: Optional[str] = None


def _p(basse: int, moyenne: int, haute: int, critique: int) -> Dict[str, int]:
    return {"basse": basse, "moyenne": moyenne, "haute": haute, "critique": critique}


_NSFW = _p(0, 2, 3, 5)
_CHOQUANT = _p(0, 1, 2, 4)

#: Floors of a custom (``perso:*``) infraction when the server does not set
#: its own: same as ``violation_indications`` (server rules).
PERSO_DEFAULT_PLANCHER: Dict[str, int] = _p(0, 1, 2, 4)

_TYPES: Tuple[InfractionType, ...] = (
    # -- message ---------------------------------------------------------
    InfractionType("insulte", "message", _p(1, 1, 2, 3), emoji_key="MESSAGE"),
    InfractionType("menace", "message", _p(2, 3, 6, 7), emoji_key="WARNING"),
    InfractionType("harcelement", "message", _p(1, 2, 3, 5), emoji_key="EXCLAMATION"),
    InfractionType("harcelement_sexuel", "message", _p(3, 5, 7, 7),
                   sensible=True, emoji_key="BLACKLIST"),
    InfractionType("haine_discrimination", "message", _p(2, 4, 6, 7), emoji_key="FLAG"),
    InfractionType("incitation_automutilation", "message", _p(3, 5, 7, 7),
                   sensible=True, emoji_key="INFO"),
    InfractionType("doxxing", "message", _p(4, 6, 7, 7),
                   sensible=True, emoji_key="SEARCH"),
    # Scams are banned on sight at every gravity.
    InfractionType("arnaque_scam", "message", _p(7, 7, 7, 7),
                   ban_direct=True, emoji_key="LINK"),
    InfractionType("violation_indications", "message", dict(PERSO_DEFAULT_PLANCHER),
                   emoji_key="NOTE"),
    # -- image -----------------------------------------------------------
    InfractionType("contenu_nsfw", "image", dict(_NSFW), emoji_key="IMAGE"),
    InfractionType("contenu_choquant", "image", dict(_CHOQUANT), emoji_key="WARNING"),
    # -- profil ----------------------------------------------------------
    InfractionType("profil_avatar_nsfw", "profil", dict(_NSFW), emoji_key="USER",
                   default_profile_action="hold"),
    InfractionType("profil_avatar_choquant", "profil", dict(_CHOQUANT), emoji_key="USER",
                   default_profile_action="hold"),
    InfractionType("profil_pseudo", "profil", _p(1, 1, 2, 3), emoji_key="EDIT",
                   default_profile_action="rename_warn"),
)

INFRACTIONS: Dict[str, InfractionType] = {t.id: t for t in _TYPES}


# ---------------------------------------------------------------------------
# Perso ids
# ---------------------------------------------------------------------------

def is_perso(cat: Any) -> bool:
    """Whether ``cat`` is a well-formed custom infraction id (``perso:<id>``)."""
    return isinstance(cat, str) and cat.startswith(PERSO_PREFIX) \
        and bool(_PERSO_ID_RE.match(cat[len(PERSO_PREFIX):]))


def perso_id(cat: Any) -> Optional[str]:
    """The ``<id>`` part of ``perso:<id>`` (``None`` if not a perso id)."""
    return cat[len(PERSO_PREFIX):] if is_perso(cat) else None


def get(cat: Any) -> Optional[InfractionType]:
    """Catalogue lookup; synthesizes a type for a ``perso:*`` id."""
    if not isinstance(cat, str):
        return None
    found = INFRACTIONS.get(cat)
    if found is not None:
        return found
    if is_perso(cat):
        return InfractionType(
            id=cat, famille="perso", plancher=dict(PERSO_DEFAULT_PLANCHER),
            emoji_key="SETTINGS",
        )
    return None


def ids_by_famille(famille: str) -> List[str]:
    """Static catalogue ids of a family, in declaration order."""
    return [t.id for t in _TYPES if t.famille == famille]


# ---------------------------------------------------------------------------
# Config normalisation / validation
# ---------------------------------------------------------------------------

def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _coerce_cran(v: Any, default: int) -> int:
    """Clamp a stored cran to 0..7; garbage falls back to ``default``."""
    if isinstance(v, bool):
        return default
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return max(CRAN_MIN, min(CRAN_MAX, n))


def _id_accepted(cat: Any, known_perso_ids: Iterable[str]) -> bool:
    if cat in INFRACTIONS:
        return True
    if is_perso(cat):
        known = set(known_perso_ids or ())
        # No registry given -> any well-formed perso id is accepted.
        return not known or perso_id(cat) in known or cat in known
    return False


def normalize_sanctions(raw: Any, known_perso_ids: Iterable[str] = ()) -> Dict[str, dict]:
    """Return a clean, fully-populated ``sanctions`` map.

    Unknown ids and non-dict entries are dropped (perso ids must be well formed
    and, when ``known_perso_ids`` is non-empty, listed there). Missing fields
    take their defaults (enabled, auto, the type's floors, recidive on, the
    type's profile action); crans are clamped to 0..7.
    """
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, dict] = {}
    for cat, entry in raw.items():
        if not _id_accepted(cat, known_perso_ids) or not isinstance(entry, dict):
            continue
        typ = get(cat)
        mode = entry.get("mode", "auto")
        crans_raw = entry.get("crans") if isinstance(entry.get("crans"), dict) else {}
        clean: Dict[str, Any] = {
            "enabled": entry.get("enabled", True) is not False,
            "mode": mode if mode in MODES else "auto",
            "crans": {g: _coerce_cran(crans_raw.get(g), typ.plancher[g]) for g in GRAVITES},
            "recidive": entry.get("recidive", True) is not False,
        }
        if typ.famille == "profil":
            ap = entry.get("action_profil")
            if ap not in ACTIONS_PROFIL or (ap == "hold" and cat not in HOLD_TYPES):
                ap = typ.default_profile_action
            clean["action_profil"] = ap
        out[cat] = clean
    return out


def validate_sanctions(raw: Any) -> Optional[str]:
    """Strict check of a ``sanctions`` map; returns an error code or ``None``.

    Codes: ``sanctions_invalid`` (not an object), ``sanctions_unknown_id``,
    ``sanctions_entry_invalid``, ``sanctions_mode_invalid``,
    ``sanctions_crans_invalid``, ``sanctions_cran_range``,
    ``sanctions_flag_invalid`` and ``sanctions_action_profil_invalid``.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        return "sanctions_invalid"
    for cat, entry in raw.items():
        if cat not in INFRACTIONS and not is_perso(cat):
            return "sanctions_unknown_id"
        if not isinstance(entry, dict):
            return "sanctions_entry_invalid"
        if "mode" in entry and entry["mode"] not in MODES:
            return "sanctions_mode_invalid"
        for flag in ("enabled", "recidive"):
            if flag in entry and not isinstance(entry[flag], bool):
                return "sanctions_flag_invalid"
        if "crans" in entry:
            crans = entry["crans"]
            if not isinstance(crans, dict) or any(g not in GRAVITES for g in crans):
                return "sanctions_crans_invalid"
            for v in crans.values():
                if not _is_int(v):
                    return "sanctions_crans_invalid"
                if not CRAN_MIN <= v <= CRAN_MAX:
                    return "sanctions_cran_range"
        if "action_profil" in entry:
            typ = get(cat)
            if typ.famille != "profil" or entry["action_profil"] not in ACTIONS_PROFIL:
                return "sanctions_action_profil_invalid"
            if entry["action_profil"] == "hold" and cat not in HOLD_TYPES:
                return "sanctions_action_profil_invalid"
    return None


def merge_legacy_disabled(sanctions: Dict[str, dict], legacy: Iterable[str],
                          raw: Any = None) -> Dict[str, dict]:
    """Fold the legacy ``categories_desactivees`` list into ``sanctions``.

    ``sanctions`` is an already-normalised map. A legacy category becomes
    ``enabled=False`` unless the raw (pre-normalisation) entry explicitly
    carries an ``enabled`` value — an explicit ``sanctions[cat].enabled`` wins.
    Returns a new dict.
    """
    out = {k: dict(v) for k, v in sanctions.items()}
    raw = raw if isinstance(raw, dict) else {}
    for cat in legacy or ():
        cat = str(cat)
        typ = get(cat)
        if typ is None:
            continue
        raw_entry = raw.get(cat)
        if isinstance(raw_entry, dict) and "enabled" in raw_entry:
            continue
        entry = out.get(cat) or normalize_sanctions({cat: {}}).get(cat)
        if entry is None:
            continue
        entry["enabled"] = False
        out[cat] = entry
    return out
