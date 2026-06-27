"""
Case service — the single, scalable entry point for recording sanctions.

Every sanction in Moddy is a **case**. Subsystems never write to the cases
tables directly: they call :class:`CaseService`, naming a *source*. A source
describes how that subsystem maps onto the case model (case type, scope,
subject and which sanction actions are allowed).

Adding a new kind of sanction later is a one-liner: register a new
:class:`CaseSource` (see :func:`register_source`) — no schema change, no new
table, nothing else to touch.

Two sources ship today:

- ``global`` — Moddy-wide sanctions (Moddy-team blacklists, global sanctions).
- ``guild``  — per-server sanctions (bans / mutes / kicks), typically
  auto-recorded from Discord audit-log events even when the action did not go
  through Moddy itself.

The service de-duplicates by default: a new sanction for a subject that already
has an *open* case of the same (type, scope) is appended to that case instead
of opening a second folder.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Union

from utils.moderation_cases import (
    CaseType, ScopeType, SubjectType, IssuerType, SanctionAction,
)

logger = logging.getLogger('moddy.case_service')


@dataclass(frozen=True)
class CaseSource:
    """Describes how a subsystem's sanctions map onto the case model."""
    key: str
    case_type: CaseType
    scope_type: ScopeType
    subject_type: SubjectType = SubjectType.DISCORD_USER
    #: Sanction actions this source is allowed to issue.
    actions: List[SanctionAction] = field(default_factory=list)
    #: Whether ``/mod case create`` lets a moderator open this kind by hand.
    manual: bool = False
    #: Whether a ``scope_id`` is required (e.g. the guild id for ``guild``).
    requires_scope_id: bool = False


# --------------------------------------------------------------------------- #
# Source registry (scalable: add an entry to support a new sanction kind)
# --------------------------------------------------------------------------- #

SOURCES: Dict[str, CaseSource] = {
    "global": CaseSource(
        key="global",
        case_type=CaseType.GLOBAL,
        scope_type=ScopeType.PLATFORM,
        subject_type=SubjectType.DISCORD_USER,
        actions=[SanctionAction.WARN, SanctionAction.RESTRICT, SanctionAction.BAN],
        manual=True,
        requires_scope_id=False,
    ),
    "guild": CaseSource(
        key="guild",
        case_type=CaseType.GUILD,
        scope_type=ScopeType.DISCORD_GUILD,
        subject_type=SubjectType.DISCORD_USER,
        actions=[
            SanctionAction.WARN, SanctionAction.MUTE,
            SanctionAction.KICK, SanctionAction.BAN,
        ],
        manual=False,            # opened automatically from Discord events
        requires_scope_id=True,
    ),
}


def register_source(source: CaseSource) -> None:
    """Register a new sanction source. The scalable extension point."""
    SOURCES[source.key] = source


def get_source(key: str) -> CaseSource:
    return SOURCES[key]


def manual_sources_for(subject_type: SubjectType) -> List[CaseSource]:
    """Sources a moderator may open by hand for a given subject nature."""
    return [
        s for s in SOURCES.values()
        if s.manual and s.subject_type == subject_type
    ]


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #

class CaseService:
    """Records sanctions as cases through the source registry."""

    def __init__(self, bot):
        self.bot = bot

    @property
    def db(self):
        return self.bot.db

    async def record_sanction(
        self,
        source: str,
        *,
        subject_id: Union[str, int],
        action: Union[str, SanctionAction],
        reason: str,
        issuer_type: Union[str, IssuerType] = IssuerType.SYSTEM,
        issuer_id: Optional[Union[str, int]] = None,
        scope_id: Optional[Union[str, int]] = None,
        expires_at: Optional[datetime] = None,
        note: Optional[str] = None,
        link_open: bool = True,
        group_id=None,
    ) -> Optional[Dict]:
        """Record a sanction, opening or extending the matching case.

        Returns ``{"id": UUID, "reference": str, "created": bool}`` or ``None``
        if the source is unknown / db unavailable.
        """
        if not self.db:
            return None
        spec = SOURCES.get(source)
        if spec is None:
            logger.warning("Unknown case source %r", source)
            return None

        action_value = action.value if isinstance(action, SanctionAction) else action
        issuer_type_value = issuer_type.value if isinstance(issuer_type, IssuerType) else issuer_type
        scope_id = str(scope_id) if scope_id is not None else None

        if spec.requires_scope_id and scope_id is None:
            logger.warning("Source %r requires a scope_id", source)
            return None

        # Link onto an existing open case of the same (subject, type, scope).
        if link_open:
            existing = await self.db.find_open_case(
                spec.subject_type.value, subject_id,
                spec.case_type.value, spec.scope_type.value, scope_id,
            )
            if existing:
                await self.db.add_sanction(
                    existing["id"], action_value, issuer_type_value, issuer_id,
                    expires_at=expires_at, note=note,
                )
                return {"id": existing["id"], "reference": existing["reference"], "created": False}

        result = await self.db.create_case(
            case_type=spec.case_type.value,
            subject_type=spec.subject_type.value,
            subject_id=subject_id,
            issuer_type=issuer_type_value,
            issuer_id=issuer_id,
            scope_type=spec.scope_type.value,
            scope_id=scope_id,
            reason=reason,
            action=action_value,
            sanction_expires_at=expires_at,
            sanction_note=note,
            group_id=group_id,
        )
        result["created"] = True
        return result

    async def revoke_sanction(
        self,
        source: str,
        *,
        subject_id: Union[str, int],
        action: Union[str, SanctionAction],
        scope_id: Optional[Union[str, int]] = None,
        by_type: Union[str, IssuerType] = IssuerType.SYSTEM,
        by_id: Optional[Union[str, int]] = None,
    ) -> int:
        """Revoke active sanctions matching a source/action for a subject.

        Used when an external action is lifted (unban, timeout cleared). Returns
        the number of sanctions revoked.
        """
        if not self.db:
            return 0
        spec = SOURCES.get(source)
        if spec is None:
            return 0
        action_value = action.value if isinstance(action, SanctionAction) else action
        by_type_value = by_type.value if isinstance(by_type, IssuerType) else by_type
        return await self.db.revoke_active_sanctions_for(
            spec.subject_type.value, subject_id, spec.case_type.value,
            spec.scope_type.value, str(scope_id) if scope_id is not None else None,
            action_value, by_type_value, by_id,
        )
