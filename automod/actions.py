"""
Automod actions — the multi-action vocabulary of an infraction's policy.

Pure module (no Discord, no I/O). It owns:

* the **stored** action names (``delete`` / ``warn`` / ``timeout`` / ``kick`` /
  ``ban``) an infraction's ``actions`` list is made of, their normalisation and
  validation (conflict rules below);
* the **exact timeout duration** helpers: :func:`parse_duration` (``"16h53"``,
  ``"1d2h"``, ``"90m"``…), :func:`format_duration` (localized) and the Discord
  bounds;
* the bridge between a set of actions and the barème's single *cran* ladder
  (:func:`floor_cran`, :func:`rebuild_for_cran`) so recidivism escalation keeps
  working with a server-chosen set of actions;
* tiny pure helpers of the runtime: :func:`approval_permission`,
  :func:`timeout_verified`.

Vocabulary
----------
Stored (config / API / UI)  ->  internal (``Decision.actions``, barème, cases)

========  ==========
delete    supprimer
warn      warn
timeout   mute
kick      kick
ban       ban
========  ==========

Conflict rules (:func:`resolve_conflicts`)
------------------------------------------
* ``ban`` supersedes ``kick`` (banning a member already removes them).
* ``kick`` / ``ban`` supersede ``timeout`` and ``warn``: a member who is removed
  gets one removal notice, not a mute or warning on top.
* ``delete`` combines with everything; ``warn`` + ``timeout`` combine.
* An empty list means "delete the message" (the minimum automod ever does).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

#: Stored action names, in canonical display / application order.
STORED_ACTIONS: Tuple[str, ...] = ("delete", "warn", "timeout", "kick", "ban")

_STORED_TO_INTERNAL = {
    "delete": "supprimer", "warn": "warn", "timeout": "mute", "kick": "kick", "ban": "ban",
}
_INTERNAL_TO_STORED = {v: k for k, v in _STORED_TO_INTERNAL.items()}

#: Internal order (matches the application order of the runtime).
INTERNAL_ORDER: Tuple[str, ...] = ("supprimer", "warn", "mute", "kick", "ban")

#: i18n key suffix (``modules.automod_ai.action.<key>``) of each stored action.
ACTION_LABEL_KEYS = {
    "delete": "supprimer", "warn": "warn", "timeout": "mute", "kick": "kick", "ban": "ban",
}

#: Discord timeout bounds (seconds). The minimum is one minute: shorter is noise.
TIMEOUT_MIN_SECONDS = 60
TIMEOUT_MAX_SECONDS = 28 * 86400
#: Used when ``timeout`` is chosen without an explicit duration.
DEFAULT_TIMEOUT_SECONDS = 3600


def to_internal(actions: Iterable[str]) -> List[str]:
    """Stored names -> internal names, in application order (unknown dropped)."""
    got = {_STORED_TO_INTERNAL[a] for a in actions if a in _STORED_TO_INTERNAL}
    return [a for a in INTERNAL_ORDER if a in got]


def to_stored(actions: Iterable[str]) -> List[str]:
    """Internal names -> stored names, canonical order (unknown dropped)."""
    got = {_INTERNAL_TO_STORED[a] for a in actions if a in _INTERNAL_TO_STORED}
    return [a for a in STORED_ACTIONS if a in got]


def resolve_conflicts(actions: Iterable[str]) -> Tuple[List[str], List[str]]:
    """Apply the conflict rules. Returns ``(clean_actions, dropped)``.

    ``actions`` are stored names; unknown names are ignored. The clean list is
    deduplicated, canonically ordered, and never empty (falls back to
    ``["delete"]``).
    """
    wanted = {a for a in actions if a in STORED_ACTIONS}
    dropped: List[str] = []
    if "ban" in wanted and "kick" in wanted:
        wanted.discard("kick")
        dropped.append("kick")
    if wanted & {"ban", "kick"}:
        for a in ("timeout", "warn"):
            if a in wanted:
                wanted.discard(a)
                dropped.append(a)
    if not wanted:
        wanted = {"delete"}
    return [a for a in STORED_ACTIONS if a in wanted], dropped


def normalize_actions(raw: Any) -> List[str]:
    """Lenient: garbage -> ``["delete"]``; otherwise :func:`resolve_conflicts`."""
    if not isinstance(raw, (list, tuple)):
        return ["delete"]
    return resolve_conflicts(a for a in raw if isinstance(a, str))[0]


def validate_actions(raw: Any) -> Optional[str]:
    """Strict check of a stored ``actions`` value; error code or ``None``.

    Codes: ``sanctions_actions_invalid`` (not a list of strings),
    ``sanctions_action_unknown``. Conflicting combinations are *valid*: they are
    resolved (ban wins) on load, see :func:`resolve_conflicts`.
    """
    if not isinstance(raw, (list, tuple)) or any(not isinstance(a, str) for a in raw):
        return "sanctions_actions_invalid"
    if any(a not in STORED_ACTIONS for a in raw):
        return "sanctions_action_unknown"
    return None


def sanction_actions(actions: Iterable[str]) -> List[str]:
    """The actions that are real sanctions (stored or internal names)."""
    return [a for a in actions if a in ("warn", "timeout", "mute", "kick", "ban")]


# ---------------------------------------------------------------------------
# Timeout duration
# ---------------------------------------------------------------------------

def coerce_timeout_seconds(value: Any, default: Optional[int] = DEFAULT_TIMEOUT_SECONDS
                           ) -> Optional[int]:
    """Clamp a stored duration into Discord's bounds; garbage -> ``default``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return max(TIMEOUT_MIN_SECONDS, min(TIMEOUT_MAX_SECONDS, int(value)))


def validate_timeout_seconds(value: Any) -> Optional[str]:
    """``None`` if ``value`` is a legal exact duration, else an error code
    (``sanctions_timeout_invalid`` / ``sanctions_timeout_range``)."""
    if isinstance(value, bool) or not isinstance(value, int):
        return "sanctions_timeout_invalid"
    if not TIMEOUT_MIN_SECONDS <= value <= TIMEOUT_MAX_SECONDS:
        return "sanctions_timeout_range"
    return None


_UNIT_SECONDS = {"d": 86400, "h": 3600, "m": 60, "s": 1}
_UNIT_ALIASES = {
    "d": "d", "day": "d", "days": "d", "j": "d", "jour": "d", "jours": "d",
    "dia": "d", "dias": "d", "tag": "d", "tage": "d", "t": "d",
    "h": "h", "hr": "h", "hrs": "h", "hour": "h", "hours": "h", "heure": "h",
    "heures": "h", "hora": "h", "horas": "h", "std": "h", "stunde": "h", "stunden": "h",
    "m": "m", "min": "m", "mins": "m", "minute": "m", "minutes": "m", "mn": "m",
    "minuto": "m", "minutos": "m", "minuten": "m",
    "s": "s", "sec": "s", "secs": "s", "second": "s", "seconds": "s", "seconde": "s",
    "secondes": "s", "seg": "s", "segundo": "s", "segundos": "s", "sek": "s", "sekunden": "s",
}
_TOKEN_RE = re.compile(r"(\d+)\s*([^\d\s,;]*)")
_FILLER_RE = re.compile(r"\b(and|et|und|y|e)\b|[,;+]", re.IGNORECASE)


def parse_duration(text: Any) -> Optional[int]:
    """Parse a human duration into seconds; ``None`` if it cannot be read.

    Accepts compound forms in several languages: ``"16h53"``, ``"16h 53m"``,
    ``"1d2h"``, ``"2 jours 3 heures"``, ``"90m"``, ``"45"`` (a lone number is
    minutes), ``"1h30"`` (a bare number after hours is minutes, after days is
    hours, after minutes is seconds). Units may not repeat. The result is NOT
    range-checked: use :func:`validate_timeout_seconds` (``0`` is returned for
    ``"0m"`` and is out of range).
    """
    if not isinstance(text, str):
        return None
    cleaned = _FILLER_RE.sub(" ", text.strip().lower().replace(" ", " "))
    cleaned = cleaned.strip()
    if not cleaned:
        return None
    tokens = _TOKEN_RE.findall(cleaned)
    # Everything must be consumed by number+unit tokens (no stray words).
    if _TOKEN_RE.sub("", cleaned).strip(" .") != "":
        return None
    total = 0
    seen: set = set()
    prev: Optional[str] = None
    follow = {"d": "h", "h": "m", "m": "s"}
    for number, unit in tokens:
        unit = unit.strip(".")
        if unit == "":
            if prev is None:
                kind = "m"                       # a lone number is minutes
            else:
                kind = follow.get(prev)
                if kind is None:
                    return None
        else:
            kind = _UNIT_ALIASES.get(unit)
            if kind is None:
                return None
        if kind in seen:
            return None
        seen.add(kind)
        total += int(number) * _UNIT_SECONDS[kind]
        prev = kind
    return total


_UNIT_LABELS = {
    "fr": ("j", "h", "min", "s"),
    "en": ("d", "h", "min", "s"),
    "es": ("d", "h", "min", "s"),
    "pt": ("d", "h", "min", "s"),
    "de": ("T", "Std.", "Min.", "s"),
}


def format_duration(seconds: Any, locale: str = "en-US") -> str:
    """Compact localized duration: ``"16 h 53 min"``, ``"1 j 2 h"``, ``"28 j"``.

    Seconds are shown only when the whole duration is under a minute or when
    they are non-zero (exact durations stay exact). Unknown locales use English.
    """
    try:
        total = max(0, int(seconds))
    except (TypeError, ValueError):
        total = 0
    lang = str(locale or "en").split("-")[0].lower()
    d_l, h_l, m_l, s_l = _UNIT_LABELS.get(lang, _UNIT_LABELS["en"])
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    parts = []
    if days:
        parts.append(f"{days} {d_l}")
    if hours:
        parts.append(f"{hours} {h_l}")
    if minutes:
        parts.append(f"{minutes} {m_l}")
    if secs or not parts:
        parts.append(f"{secs} {s_l}")
    return " ".join(parts)


def duration_input_hint(locale: str = "en-US") -> str:
    """A placeholder example for a duration input, in the locale's units."""
    lang = str(locale or "en").split("-")[0].lower()
    return {"fr": "16h53, 1j2h, 90min", "de": "16h53, 1T2h, 90min"}.get(
        lang, "16h53, 1d2h, 90m")


# ---------------------------------------------------------------------------
# Bridge with the barème's cran ladder
# ---------------------------------------------------------------------------

#: A kick sits just under the ban on the ladder (no timed ladder rung exists for it).
KICK_CRAN = 6

# Cran of a timeout by duration (matches the ladder's 2 h / 12 h / 48 h / 7 d / 28 d).
_TIMEOUT_BUCKETS = ((7200, 2), (43200, 3), (172800, 4), (604800, 5))


def timeout_cran(seconds: Optional[int]) -> int:
    """Ladder cran equivalent to a timeout of ``seconds`` (2..6)."""
    s = seconds if isinstance(seconds, int) and seconds > 0 else DEFAULT_TIMEOUT_SECONDS
    for limit, cran in _TIMEOUT_BUCKETS:
        if s <= limit:
            return cran
    return 6


def floor_cran(actions: Iterable[str], timeout_seconds: Optional[int]) -> int:
    """The ladder cran a set of stored actions is worth (0 = delete only)."""
    got = set(actions)
    if "ban" in got:
        return 7
    if "kick" in got:
        return KICK_CRAN
    cran = 0
    if "warn" in got:
        cran = 1
    if "timeout" in got:
        cran = max(cran, timeout_cran(timeout_seconds))
    return cran


def cran_to_plan(cran: int) -> Tuple[List[str], Optional[int]]:
    """A ladder cran as ``(stored_actions, timeout_seconds)`` (used by migration)."""
    from . import bareme
    internal, hours = bareme.ladder_for(cran)
    stored = to_stored(internal)
    timeout = hours * 3600 if "timeout" in stored and hours else None
    return stored, timeout


def plan_to_internal(actions: Iterable[str], timeout_seconds: Optional[int]
                     ) -> Tuple[List[str], Optional[int]]:
    """A stored plan as the barème's result shape ``(internal_actions, seconds)``."""
    stored, _ = resolve_conflicts(actions)
    seconds = None
    if "timeout" in stored:
        seconds = coerce_timeout_seconds(timeout_seconds)
    return to_internal(stored), seconds


def rebuild_for_cran(plan_actions: Iterable[str], cran: int
                     ) -> Tuple[List[str], Optional[int]]:
    """Re-derive ``(internal_actions, seconds)`` when the final cran differs from the plan.

    Used when recidivism escalated the plan, or the confidence cap / ceiling /
    heavy-sanction confirmation lowered it: the *sanction* follows the ladder at
    ``cran`` (duration included), while the lighter actions the server chose
    (``delete``, ``warn``) are kept when they still make sense.
    """
    from . import bareme
    plan = set(plan_actions)
    ladder, hours = bareme.ladder_for(cran)
    out = {a for a in ladder if a != "supprimer"}
    if "delete" in plan:
        out.add("supprimer")
    if "warn" in plan and cran >= 1 and "ban" not in out and "kick" not in out:
        out.add("warn")
    seconds = hours * 3600 if "mute" in out and hours else None
    return [a for a in INTERNAL_ORDER if a in out], seconds


# ---------------------------------------------------------------------------
# Runtime helpers (pure)
# ---------------------------------------------------------------------------

def approval_permission(actions: Iterable[str]) -> str:
    """The Discord permission a moderator needs to approve / reject these actions.

    ``ban`` -> ``ban_members``, ``kick`` -> ``kick_members``, otherwise
    ``moderate_members`` (warn / timeout). Accepts stored or internal names.
    """
    got = set(actions)
    if "ban" in got:
        return "ban_members"
    if "kick" in got:
        return "kick_members"
    return "moderate_members"


def timeout_verified(returned_until: Optional[datetime], requested_until: datetime,
                     tolerance: timedelta = timedelta(minutes=2)) -> bool:
    """Whether the member's ``timed_out_until`` proves the timeout took effect."""
    if returned_until is None:
        return False
    try:
        return returned_until >= requested_until - tolerance
    except TypeError:       # naive vs aware: be strict rather than guess
        return False


# Failure reason codes shown (briefly) on the alert card: ``failure.<code>`` keys.
FAIL_HIERARCHY = "hierarchy"
FAIL_PERMISSION = "permission"
FAIL_ABSENT = "absent"
FAIL_DISCORD = "discord"
FAIL_UNVERIFIED = "unverified"
FAIL_CASE = "case"
FAIL_REASONS = (FAIL_HIERARCHY, FAIL_PERMISSION, FAIL_ABSENT, FAIL_DISCORD,
                FAIL_UNVERIFIED, FAIL_CASE)


def summarize_outcome(wanted: Iterable[str], applied: Iterable[str],
                      failed: Dict[str, str]) -> Dict[str, Any]:
    """Consistency helper: what was wanted, applied, failed (internal names).

    ``failed`` maps an action to a reason code; any wanted action neither
    applied nor explained is reported as ``discord`` so nothing is ever
    silently presented as done.
    """
    applied_l = [a for a in INTERNAL_ORDER if a in set(applied)]
    failed_d = {a: failed[a] for a in INTERNAL_ORDER if a in failed and a not in applied_l}
    for a in INTERNAL_ORDER:
        if a in set(wanted) and a not in applied_l and a not in failed_d:
            failed_d[a] = FAIL_DISCORD
    return {"wanted": [a for a in INTERNAL_ORDER if a in set(wanted)],
            "applied": applied_l, "failed": failed_d}
