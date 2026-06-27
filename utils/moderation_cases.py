"""
Moderation Cases System — domain model & enums.

A **case** is a moderation folder, intentionally decoupled from the Discord
context: it may concern a regular guild, the Moddy inter-server network, the
Moddy platform itself, or an external moderation service.

Three notions must never be confused:
- ``subject``  : who/what the case is about (the sanctioned user/guild).
- ``issuer``   : who created the case.
- ``scope``    : *where* the case applies.

Every actor/target is referenced by a ``*_type`` (enum describing the nature of
the identifier) + ``*_id`` (the value, stored as TEXT). This makes the model
extensible without structural migrations.

A case carries one or more **sanctions** (each with its own lifecycle) and a
chronological **event timeline** (comments, evidence, system events).

This module is pure Python (no discord import) so it can be shared by the bot,
the internal API and any worker.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from utils.emojis import (
    SANCTION_ACTION_EMOJIS,
    SANCTION_ACTION_EMOJI_DEFAULT,
    CASE_TYPE_EMOJIS,
    CASE_TYPE_EMOJI_DEFAULT,
)


# =============================================================================
# ENUMS  (mirror the PostgreSQL ENUM types — keep values in sync with db/base)
# =============================================================================

class CaseType(str, Enum):
    """Where the case conceptually lives."""
    GLOBAL = "global"
    NETWORK = "network"
    GUILD = "guild"
    PLATFORM = "platform"
    EXTERNAL = "external"


class SubjectType(str, Enum):
    """Nature of the case subject (who/what is sanctioned)."""
    DISCORD_USER = "discord_user"
    DISCORD_GUILD = "discord_guild"
    MODDY_USER = "moddy_user"
    EXTERNAL = "external"


class IssuerType(str, Enum):
    """Nature of the actor that created the case / posted a sanction."""
    DISCORD_USER = "discord_user"
    MODDY_STAFF = "moddy_staff"
    AUTOMOD = "automod"
    SYSTEM = "system"
    EXTERNAL = "external"


class ScopeType(str, Enum):
    """Where the case applies."""
    DISCORD_GUILD = "discord_guild"
    NETWORK = "network"
    PLATFORM = "platform"
    EXTERNAL_SERVICE = "external_service"


class CaseStatus(str, Enum):
    """Binary case status."""
    OPEN = "open"
    CLOSED = "closed"


class SanctionAction(str, Enum):
    """The kind of sanction applied. Extensible."""
    WARN = "warn"
    MUTE = "mute"
    BAN = "ban"
    KICK = "kick"
    RESTRICT = "restrict"
    REVOKE_ACCESS = "revoke_access"


class SanctionStatus(str, Enum):
    """Lifecycle of an individual sanction."""
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


class EventType(str, Enum):
    """Timeline entry kind."""
    COMMENT = "comment"
    EVIDENCE = "evidence"
    NOTE = "note"
    SANCTION_ADDED = "sanction_added"
    SANCTION_REVOKED = "sanction_revoked"
    SANCTION_EXPIRED = "sanction_expired"
    STATUS_CHANGE = "status_change"


class AuthorType(str, Enum):
    """Author of a timeline event."""
    DISCORD_USER = "discord_user"
    MODDY_STAFF = "moddy_staff"
    SYSTEM = "system"


# `trigger` values stored in a status_change event payload.
class StatusTrigger(str, Enum):
    MANUAL = "manual"
    EXPIRATION = "expiration"
    REVOCATION = "revocation"
    SYSTEM = "system"


# =============================================================================
# REFERENCE GENERATION
# =============================================================================

# Unambiguous uppercase alphabet (no O/0, no I/1).
REFERENCE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
REFERENCE_LENGTH = 6


def generate_reference(length: int = REFERENCE_LENGTH) -> str:
    """Generate a random public case reference (e.g. ``A7F2K9``).

    Uniqueness is enforced by the UNIQUE constraint on ``cases.reference``;
    callers retry generation on a collision (see ModerationRepository).
    """
    return "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(length))


# =============================================================================
# DISPLAY HELPERS
# =============================================================================

# Available sanction actions per case type. Used to build selection UIs.
CASE_TYPE_ACTIONS: Dict[CaseType, List[SanctionAction]] = {
    CaseType.GLOBAL: [SanctionAction.WARN, SanctionAction.RESTRICT, SanctionAction.BAN],
    CaseType.PLATFORM: [SanctionAction.WARN, SanctionAction.RESTRICT, SanctionAction.BAN],
    CaseType.NETWORK: [SanctionAction.WARN, SanctionAction.MUTE, SanctionAction.BAN],
    CaseType.GUILD: [
        SanctionAction.WARN, SanctionAction.MUTE, SanctionAction.KICK,
        SanctionAction.BAN, SanctionAction.RESTRICT,
    ],
    CaseType.EXTERNAL: list(SanctionAction),
}

# Sanction actions that accept a temporary duration (``expires_at``).
TEMPORARY_ACTIONS = {SanctionAction.MUTE, SanctionAction.BAN, SanctionAction.RESTRICT}


def get_available_actions(case_type: CaseType) -> List[SanctionAction]:
    """Return the sanction actions allowed for a case type."""
    return CASE_TYPE_ACTIONS.get(case_type, list(SanctionAction))


def get_action_emoji(action: SanctionAction) -> str:
    """Return the emoji for a sanction action."""
    return SANCTION_ACTION_EMOJIS.get(action.value, SANCTION_ACTION_EMOJI_DEFAULT)


def get_case_type_emoji(case_type: CaseType) -> str:
    """Return the emoji for a case type."""
    return CASE_TYPE_EMOJIS.get(case_type.value, CASE_TYPE_EMOJI_DEFAULT)


def _coerce(enum_cls, value):
    """Coerce a raw value (str / enum) into ``enum_cls`` (or ``None``)."""
    if value is None:
        return None
    if isinstance(value, enum_cls):
        return value
    return enum_cls(value)


# =============================================================================
# DATACLASSES
# =============================================================================

@dataclass
class Sanction:
    """A single sanction attached to a case."""
    id: str
    case_id: str
    action: SanctionAction
    status: SanctionStatus
    issued_by_type: IssuerType
    issued_by_id: Optional[str]
    expires_at: Optional[datetime] = None
    note: Optional[str] = None
    created_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    revoked_by_type: Optional[IssuerType] = None
    revoked_by_id: Optional[str] = None

    @classmethod
    def from_db(cls, row: Dict[str, Any]) -> "Sanction":
        return cls(
            id=str(row["id"]),
            case_id=str(row["case_id"]),
            action=_coerce(SanctionAction, row["action"]),
            status=_coerce(SanctionStatus, row["status"]),
            issued_by_type=_coerce(IssuerType, row["issued_by_type"]),
            issued_by_id=row.get("issued_by_id"),
            expires_at=row.get("expires_at"),
            note=row.get("note"),
            created_at=row.get("created_at"),
            revoked_at=row.get("revoked_at"),
            revoked_by_type=_coerce(IssuerType, row.get("revoked_by_type")),
            revoked_by_id=row.get("revoked_by_id"),
        )

    @property
    def is_active(self) -> bool:
        return self.status == SanctionStatus.ACTIVE

    @property
    def is_permanent(self) -> bool:
        return self.expires_at is None

    def emoji(self) -> str:
        return get_action_emoji(self.action)


@dataclass
class CaseEvent:
    """A single timeline entry."""
    id: str
    case_id: str
    type: EventType
    created_at: datetime
    author_type: Optional[AuthorType] = None
    author_id: Optional[str] = None
    content: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None

    @classmethod
    def from_db(cls, row: Dict[str, Any]) -> "CaseEvent":
        return cls(
            id=str(row["id"]),
            case_id=str(row["case_id"]),
            type=_coerce(EventType, row["type"]),
            created_at=row["created_at"],
            author_type=_coerce(AuthorType, row.get("author_type")),
            author_id=row.get("author_id"),
            content=row.get("content"),
            payload=row.get("payload") or None,
        )


@dataclass
class Case:
    """A moderation case (folder)."""
    id: str
    reference: str
    type: CaseType
    subject_type: SubjectType
    subject_id: str
    issuer_type: IssuerType
    issuer_id: Optional[str]
    scope_type: ScopeType
    scope_id: Optional[str]
    reason: str
    status: CaseStatus
    status_locked: bool = False
    group_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    sanctions: List[Sanction] = field(default_factory=list)
    events: List[CaseEvent] = field(default_factory=list)

    @classmethod
    def from_db(
        cls,
        row: Dict[str, Any],
        sanctions: Optional[List[Dict[str, Any]]] = None,
        events: Optional[List[Dict[str, Any]]] = None,
    ) -> "Case":
        return cls(
            id=str(row["id"]),
            reference=row["reference"],
            type=_coerce(CaseType, row["type"]),
            subject_type=_coerce(SubjectType, row["subject_type"]),
            subject_id=row["subject_id"],
            issuer_type=_coerce(IssuerType, row["issuer_type"]),
            issuer_id=row.get("issuer_id"),
            scope_type=_coerce(ScopeType, row["scope_type"]),
            scope_id=row.get("scope_id"),
            reason=row["reason"],
            status=_coerce(CaseStatus, row["status"]),
            status_locked=bool(row.get("status_locked", False)),
            group_id=str(row["group_id"]) if row.get("group_id") else None,
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
            sanctions=[Sanction.from_db(s) for s in (sanctions or [])],
            events=[CaseEvent.from_db(e) for e in (events or [])],
        )

    @property
    def is_open(self) -> bool:
        return self.status == CaseStatus.OPEN

    @property
    def active_sanctions(self) -> List[Sanction]:
        return [s for s in self.sanctions if s.is_active]

    def type_emoji(self) -> str:
        return get_case_type_emoji(self.type)
