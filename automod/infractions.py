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
        "mode": "auto" | "perso",     # absent entry == "auto" == Moddy's ladder
        "actions": ["delete", "warn", "timeout", "kick", "ban"],  # perso: the set applied
        "timeout_seconds": 60780,     # perso + "timeout": exact duration, 60..2419200
        "recidive": true,             # perso: repeat offences escalate the set
        "require_approval": false,    # a moderator approves in the alert channel first
        "by_gravity": {"haute": {"actions": [...], "timeout_seconds": 3600}},  # optional
        "action_profil": "hold" | "alert" | "rename_warn" | "bareme"   # profil types
    } }

``mode: "auto"`` uses Moddy's ladder (cran from the barème, one sanction per
cran); ``mode: "perso"`` applies the server's own ``actions`` (see
``automod/actions.py`` for the vocabulary and conflict rules) as the floor of
the ladder: recidivism escalation, the confidence cap and the ``max_action``
ceiling still apply on top. ``by_gravity`` is an optional per-gravity override
(produced by the migration of the legacy ``crans`` shape; a UI may ignore it but
must not drop it unless it rewrites ``actions`` via :func:`set_entry_actions`).

Legacy shape (still read, migrated on load by :func:`normalize_sanctions`)::

    "crans": {"basse": 0..7, "moyenne": 0..7, "haute": 0..7, "critique": 0..7}

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

from . import actions as actions_mod

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
#: its own (the ladder floors of an ordinary rule violation).
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


def _plan_of(raw_actions: Any, raw_timeout: Any) -> Tuple[List[str], Optional[int]]:
    """Normalised ``(actions, timeout_seconds)`` of one stored plan."""
    acts = actions_mod.normalize_actions(raw_actions)
    timeout = (actions_mod.coerce_timeout_seconds(raw_timeout)
               if "timeout" in acts else None)
    return acts, timeout


def _cran_plan(cran: int) -> Dict[str, Any]:
    acts, timeout = actions_mod.cran_to_plan(cran)
    return {"actions": acts, "timeout_seconds": timeout}


def migrate_entry(cat: str, entry: dict) -> dict:
    """Upgrade a legacy ``mode: perso`` + ``crans`` entry to ``actions``.

    Idempotent: an entry that already has ``actions`` is returned unchanged.
    The base plan is the one of gravity ``moyenne`` (the middle of the scale);
    every gravity whose legacy cran maps to a different plan is kept in
    ``by_gravity`` so no behaviour is lost. ``crans`` is dropped.
    """
    if not isinstance(entry, dict) or "actions" in entry or entry.get("mode") != "perso":
        return entry
    typ = get(cat)
    crans_raw = entry.get("crans") if isinstance(entry.get("crans"), dict) else {}
    defaults = typ.plancher if typ else PERSO_DEFAULT_PLANCHER
    crans = {g: _coerce_cran(crans_raw.get(g), defaults[g]) for g in GRAVITES}
    base = _cran_plan(crans["moyenne"])
    out = {k: v for k, v in entry.items() if k != "crans"}
    out.update(base)
    by_gravity = {g: _cran_plan(c) for g, c in crans.items() if _cran_plan(c) != base}
    if by_gravity:
        out["by_gravity"] = by_gravity
    return out


def _default_plan(typ: InfractionType, gravite: str = "moyenne") -> Dict[str, Any]:
    return _cran_plan(typ.plancher.get(gravite, 0))


def normalize_sanctions(raw: Any, known_perso_ids: Iterable[str] = ()) -> Dict[str, dict]:
    """Return a clean, fully-populated ``sanctions`` map.

    Unknown ids and non-dict entries are dropped (perso ids must be well formed
    and, when ``known_perso_ids`` is non-empty, listed there). Missing fields
    take their defaults (enabled, auto, recidive on, approval off, the type's
    profile action). The legacy ``crans`` shape is migrated (:func:`migrate_entry`);
    in ``perso`` mode ``actions`` are conflict-resolved and ``timeout_seconds``
    is kept inside Discord's bounds (only when ``timeout`` is an action). The
    approval option is dropped for profile types and for plans without a
    warn / timeout / kick / ban.
    """
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, dict] = {}
    for cat, entry in raw.items():
        if not _id_accepted(cat, known_perso_ids) or not isinstance(entry, dict):
            continue
        typ = get(cat)
        mode = entry.get("mode", "auto")
        mode = mode if mode in MODES else "auto"
        entry = migrate_entry(cat, dict(entry, mode=mode))
        clean: Dict[str, Any] = {
            "enabled": entry.get("enabled", True) is not False,
            "mode": mode,
            "recidive": entry.get("recidive", True) is not False,
        }
        if mode == "perso":
            if "actions" in entry:
                acts, timeout = _plan_of(entry.get("actions"), entry.get("timeout_seconds"))
            else:
                plan = _default_plan(typ)
                acts, timeout = _plan_of(plan["actions"], plan["timeout_seconds"])
            clean["actions"] = acts
            clean["timeout_seconds"] = timeout
            by_g = entry.get("by_gravity")
            if isinstance(by_g, dict):
                clean_g = {}
                for g in GRAVITES:
                    sub = by_g.get(g)
                    if isinstance(sub, dict) and "actions" in sub:
                        a, tmo = _plan_of(sub.get("actions"), sub.get("timeout_seconds"))
                        clean_g[g] = {"actions": a, "timeout_seconds": tmo}
                if clean_g:
                    clean["by_gravity"] = clean_g
        approval = entry.get("require_approval", False) is True
        if typ.famille == "profil":
            approval = False
        elif mode == "perso" and not actions_mod.sanction_actions(clean["actions"]) \
                and not any(actions_mod.sanction_actions(p["actions"])
                            for p in clean.get("by_gravity", {}).values()):
            approval = False
        clean["require_approval"] = approval
        if typ.famille == "profil":
            ap = entry.get("action_profil")
            if ap not in ACTIONS_PROFIL or (ap == "hold" and cat not in HOLD_TYPES):
                ap = typ.default_profile_action
            clean["action_profil"] = ap
        out[cat] = clean
    return out


def _validate_plan(entry: dict) -> Optional[str]:
    if "actions" in entry:
        err = actions_mod.validate_actions(entry["actions"])
        if err:
            return err
    if "timeout_seconds" in entry and entry["timeout_seconds"] is not None:
        err = actions_mod.validate_timeout_seconds(entry["timeout_seconds"])
        if err:
            return err
    return None


def validate_sanctions(raw: Any) -> Optional[str]:
    """Strict check of a ``sanctions`` map; returns an error code or ``None``.

    Codes: ``sanctions_invalid`` (not an object), ``sanctions_unknown_id``,
    ``sanctions_entry_invalid``, ``sanctions_mode_invalid``,
    ``sanctions_crans_invalid`` / ``sanctions_cran_range`` (legacy shape, still
    accepted), ``sanctions_flag_invalid``, ``sanctions_action_profil_invalid``,
    ``sanctions_actions_invalid``, ``sanctions_action_unknown``,
    ``sanctions_timeout_invalid``, ``sanctions_timeout_range`` and
    ``sanctions_by_gravity_invalid``. Conflicting actions (kick + ban…) are
    valid: they are resolved on load.
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
        for flag in ("enabled", "recidive", "require_approval"):
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
        err = _validate_plan(entry)
        if err:
            return err
        if "by_gravity" in entry:
            by_g = entry["by_gravity"]
            if not isinstance(by_g, dict) or any(
                    g not in GRAVITES or not isinstance(p, dict) for g, p in by_g.items()):
                return "sanctions_by_gravity_invalid"
            for p in by_g.values():
                err = _validate_plan(p)
                if err:
                    return err
        if "action_profil" in entry:
            typ = get(cat)
            if typ.famille != "profil" or entry["action_profil"] not in ACTIONS_PROFIL:
                return "sanctions_action_profil_invalid"
            if entry["action_profil"] == "hold" and cat not in HOLD_TYPES:
                return "sanctions_action_profil_invalid"
    return None


# ---------------------------------------------------------------------------
# Effective policy (what the UI shows) and editing helpers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EffectivePolicy:
    """The policy an infraction really applies, defaults included."""
    mode: str                       # "auto" | "perso"
    enabled: bool
    actions: Tuple[str, ...]        # stored names, canonical order
    timeout_seconds: Optional[int]  # exact duration when "timeout" is an action
    recidive: bool
    require_approval: bool


def effective_policy(cat: str, entry: Optional[dict] = None,
                     gravite: str = "moyenne") -> Optional[EffectivePolicy]:
    """What ``cat`` applies at ``gravite`` for a first offence — even when the
    server never configured it (Moddy's ladder floor is returned as the value).

    ``entry`` is the stored entry (raw or normalised, may be ``None``).
    Returns ``None`` for an unknown infraction id.
    """
    typ = get(cat)
    if typ is None:
        return None
    norm = normalize_sanctions({cat: entry if isinstance(entry, dict) else {}}).get(cat) \
        or normalize_sanctions({cat: {}}).get(cat)
    if norm is None:           # perso id with a registry mismatch
        return None
    if norm["mode"] == "perso":
        plan = norm.get("by_gravity", {}).get(gravite) or norm
        acts, timeout = list(plan["actions"]), plan.get("timeout_seconds")
    else:
        plan = _default_plan(typ, gravite)
        acts, timeout = plan["actions"], plan["timeout_seconds"]
    if not norm["enabled"]:
        acts, timeout = ["delete"], None
    return EffectivePolicy(
        mode=norm["mode"], enabled=norm["enabled"], actions=tuple(acts),
        timeout_seconds=timeout, recidive=norm["recidive"],
        require_approval=bool(norm["require_approval"]) and norm["enabled"],
    )


def set_entry_actions(entry: Optional[dict], actions: Iterable[str],
                      timeout_seconds: Optional[int] = None) -> dict:
    """Return ``entry`` switched to ``perso`` with the given plan.

    Conflicts are resolved, the timeout is bounded (default 1 h when
    ``timeout`` is chosen without a duration) and per-gravity overrides are
    cleared (the admin just chose one plan for every gravity). Does not
    mutate ``entry``; the result is NOT normalised against a type — run it
    through :func:`normalize_sanctions` when storing.
    """
    out = {k: v for k, v in (entry or {}).items() if k not in ("by_gravity", "crans")}
    acts, timeout = _plan_of(list(actions), timeout_seconds)
    out.update(mode="perso", actions=acts, timeout_seconds=timeout)
    return out


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
