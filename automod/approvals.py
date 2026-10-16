"""
Moderator approval — the pure half (snapshots, permissions, expiry).

When an infraction has ``require_approval``, the module does not sanction on its
own: it stores a *snapshot* of the decision (this module) in
``automod_pending_approvals`` and posts a card with Approve / Reject buttons.
Whichever moderator clicks later, the sanction is applied from the snapshot —
the offending message is already gone and the process may have restarted.

No Discord, no I/O.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Optional

from . import actions as act
from . import constants as ac
from .bareme import Composante, ResultatBareme
from .schemas import Decision, ImageMeta

#: Payload layout version (bump when the snapshot shape changes).
PAYLOAD_VERSION = 1


@dataclasses.dataclass
class IncidentOutcome:
    """What really happened when an incident's actions were executed."""
    #: Internal action names that took effect (``supprimer`` / ``warn`` / ...).
    applied: list = dataclasses.field(default_factory=list)
    #: ``{action or "case": reason code}`` for everything that should have
    #: happened and did not (see ``act.FAIL_REASONS``).
    failed: Dict[str, str] = dataclasses.field(default_factory=dict)
    case_ref: Optional[str] = None
    case_id: Any = None
    primary_action: Optional[str] = None
    sanction_id: Any = None

    def to_payload(self) -> Dict[str, Any]:
        return {"applied": list(self.applied), "failed": dict(self.failed),
                "case_ref": self.case_ref,
                "case_id": str(self.case_id) if self.case_id else None,
                "primary_action": self.primary_action}

    @classmethod
    def from_payload(cls, data: Optional[Dict[str, Any]]) -> "IncidentOutcome":
        data = data or {}
        return cls(applied=list(data.get("applied") or []),
                   failed=dict(data.get("failed") or {}),
                   case_ref=data.get("case_ref"), case_id=data.get("case_id"),
                   primary_action=data.get("primary_action"))


def expiry(now: Optional[datetime] = None) -> datetime:
    """Deadline of an approval created at ``now``."""
    return (now or datetime.now(timezone.utc)) + timedelta(hours=ac.APPROVAL_TTL_HOURS)


def is_expired(expires_at: Optional[datetime], now: Optional[datetime] = None) -> bool:
    if expires_at is None:
        return False
    now = now or datetime.now(timezone.utc)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at < now


def decision_to_payload(decision: Decision) -> Dict[str, Any]:
    """JSON-safe copy of a decision (image bytes are never part of it)."""
    return dataclasses.asdict(decision)


def decision_from_payload(data: Dict[str, Any]) -> Decision:
    """Rebuild a :class:`Decision`; unknown / missing keys are tolerated."""
    fields = {f.name for f in dataclasses.fields(Decision)}
    clean = {k: v for k, v in (data or {}).items() if k in fields}
    image = clean.get("image")
    if isinstance(image, dict):
        meta_fields = {f.name for f in dataclasses.fields(ImageMeta)}
        clean["image"] = ImageMeta(**{k: v for k, v in image.items() if k in meta_fields})
    return Decision(**clean)


def bareme_to_payload(b: ResultatBareme) -> Dict[str, Any]:
    return {
        "cran": b.cran, "actions": list(b.actions), "duree_heures": b.duree_heures,
        "duree_secondes": b.duree_secondes, "categorie": b.categorie,
        "gravite": b.gravite, "points_recidive": b.points_recidive,
        "needs_review": b.needs_review, "action_profil": b.action_profil,
        "require_approval": b.require_approval,
        "composantes": [{"code": c.code, "delta": c.delta, "detail": c.detail}
                        for c in b.composantes],
    }


def bareme_from_payload(data: Dict[str, Any]) -> ResultatBareme:
    return ResultatBareme(
        cran=int(data.get("cran", 0)), actions=list(data.get("actions") or []),
        duree_heures=data.get("duree_heures"), duree_secondes=data.get("duree_secondes"),
        categorie=data.get("categorie", ""), gravite=data.get("gravite", "basse"),
        points_recidive=float(data.get("points_recidive", 0.0)),
        composantes=[Composante(c.get("code", ""), int(c.get("delta", 0)), c.get("detail", ""))
                     for c in data.get("composantes") or []],
        needs_review=bool(data.get("needs_review")),
        action_profil=data.get("action_profil"),
        require_approval=bool(data.get("require_approval")),
    )


def build_payload(decision: Decision, bareme: ResultatBareme, snapshot: Dict[str, Any],
                  *, locale: str, deleted: bool, attachments: Optional[Dict[str, Any]] = None
                  ) -> Dict[str, Any]:
    """The stored snapshot of a pending approval.

    ``snapshot`` describes the offending message (``message_id``,
    ``channel_id``, ``jump_url``, ``author_name``, ``ts``, ``context_text``,
    ``content``). ``deleted`` records whether the message was removed when the
    card was posted. ``attachments`` remembers what rode with the card
    (``text_file`` name, ``image`` name) so an edit re-renders the same view.
    """
    return {
        "v": PAYLOAD_VERSION, "locale": locale, "deleted": bool(deleted),
        "decision": decision_to_payload(decision),
        "bareme": bareme_to_payload(bareme),
        "snapshot": dict(snapshot),
        "attachments": dict(attachments or {}),
    }


def sanction_actions_of(payload: Dict[str, Any]) -> list:
    """The sanction actions (internal names) a stored approval would apply."""
    return act.sanction_actions((payload.get("bareme") or {}).get("actions") or [])


def can_resolve(permissions: Any, actions: Iterable[str]) -> bool:
    """Whether a member with ``permissions`` may approve / reject these actions.

    The matching moderation permission is required (``ban_members`` for a ban,
    ``kick_members`` for a kick, ``moderate_members`` otherwise); an
    administrator always may. ``permissions`` is a ``discord.Permissions`` (or
    anything with those attributes); ``None`` -> refused.
    """
    if permissions is None:
        return False
    if getattr(permissions, "administrator", False):
        return True
    return bool(getattr(permissions, act.approval_permission(actions), False))
