"""
Ready-made protection levels for the Automod first-run wizard.

A server that opens ``/config`` -> Automod for the first time should not have
to understand the individual detections: it picks one of three *levels* and
Moddy fills in the stored config. The module is **pure** (no Discord, no I/O) so
the levels are table-testable and a future dashboard can reuse the very same
data.

Levels
------
* ``light``    — serious insults, threats, hate, scams and explicit images only
                 (severity 2; milder insults only delete the message).
* ``balanced`` — every message and image detection with Moddy's default
                 sanctions (severity 3). The recommended level.
* ``strict``   — everything of ``balanced`` plus member profiles (avatar,
                 banner, names), severity 4.

The result is the exact stored schema documented in docs/integration/AUTOMOD_CONFIG.md.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, Optional, Tuple

from . import constants as ac

PRESETS: Tuple[str, ...] = ("light", "balanced", "strict")
DEFAULT_PRESET = "balanced"

#: Every feature the panel manages, in display order.
FEATURE_IDS: Tuple[str, ...] = (
    "content", "image_nsfw", "image_gore", "image_scam", "profile")

#: preset -> (features switched on, severity)
_LEVELS: Dict[str, Tuple[Tuple[str, ...], int]] = {
    "light": (("content", "image_nsfw", "image_scam"), 2),
    "balanced": (("content", "image_nsfw", "image_gore", "image_scam"), 3),
    "strict": (FEATURE_IDS, 4),
}

#: Sanction overrides a level brings on top of Moddy's defaults. ``light`` only
#: deletes ordinary insults (serious ones keep a timeout; repeat offenders
#: still escalate through the recidivism ladder) and keeps the default sanctions for the serious types.
_LEVEL_SANCTIONS: Dict[str, Dict[str, Dict[str, Any]]] = {
    "light": {
        "insulte": {
            "enabled": True,
            "mode": "perso",
            "actions": ["delete"],
            "timeout_seconds": None,
            "recidive": True,
            "require_approval": False,
            # Serious insults keep a sanction (the old ladder: cran 2 / cran 3).
            "by_gravity": {
                "haute": {"actions": ["delete", "timeout"], "timeout_seconds": 2 * 3600},
                "critique": {"actions": ["delete", "timeout"], "timeout_seconds": 12 * 3600},
            },
        },
    },
}


def blank_config() -> Dict[str, Any]:
    """The stored schema with every key at its "nothing configured" value.

    Mirrors ``AutomodModule.get_default_config`` minus the legacy
    ``categories_desactivees`` list, which the panel never writes back.
    """
    def feature(**extra: Any) -> Dict[str, Any]:
        base: Dict[str, Any] = {"enabled": False, "exempt_roles": [], "exempt_channels": []}
        base.update(extra)
        return base

    return {
        "notify_channel_id": None,
        "ignore_moderators": True,
        "severity": ac.SEVERITY_DEFAULT,
        "max_action": "ban",
        "use_precedents": True,
        "sanctions": {},
        "infractions_perso": [],
        "features": {
            "content": feature(),
            "image_nsfw": feature(),
            "image_gore": feature(),
            "image_scam": feature(scan_all=False),
            "profile": feature(avatar=True, banner=True, pseudo=True),
        },
    }


def preset_config(preset: str, channel_id: Optional[int], *, state: str = "simulation",
                  base: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Full config of ``preset`` for ``channel_id``.

    ``state`` is the generic module state written next to the config
    (``active`` / ``simulation`` / ``paused``; simulation is the former
    ``dry_run``). ``base`` (an existing, normalised config) is kept for everything a level
    does not own: custom infractions, exemptions, the maximum
    sanction and the sanctions of types the level leaves alone. Without it the
    result is a complete fresh config.

    Raises ``KeyError`` for an unknown ``preset``.
    """
    features_on, severity = _LEVELS[preset]
    cfg = copy.deepcopy(base) if base else blank_config()
    cfg.setdefault("features", {})

    if channel_id is not None:
        cfg["notify_channel_id"] = int(channel_id)
    cfg["state"] = state
    cfg.pop("enabled", None)
    cfg.pop("dry_run", None)
    cfg["severity"] = severity

    skeleton = blank_config()["features"]
    for fid in FEATURE_IDS:
        feat = cfg["features"].get(fid)
        merged = copy.deepcopy(skeleton[fid])
        if isinstance(feat, dict):
            merged.update(feat)
        merged["enabled"] = fid in features_on
        cfg["features"][fid] = merged
    if "profile" in features_on:
        # A level that moderates profiles checks all three parts of one.
        cfg["features"]["profile"].update(avatar=True, banner=True, pseudo=True)

    sanctions = dict(cfg.get("sanctions") or {})
    # A level always starts from Moddy's defaults for every type any level
    # overrides, so switching level never leaves a stale override behind.
    for lvl in _LEVEL_SANCTIONS.values():
        for cat in lvl:
            sanctions.pop(cat, None)
    for cat, entry in _LEVEL_SANCTIONS.get(preset, {}).items():
        sanctions[cat] = copy.deepcopy(entry)
    cfg["sanctions"] = sanctions
    cfg.pop("categories_desactivees", None)
    return cfg


def features_on(preset: str) -> Tuple[str, ...]:
    """The features a level switches on (for the wizard's description)."""
    return _LEVELS[preset][0]
