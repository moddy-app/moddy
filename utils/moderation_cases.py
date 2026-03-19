"""
Moderation Cases System - Models and Enums
Unified sanction system for MODDY bot
"""

from enum import Enum
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
import logging

from utils.emojis import SANCTION_EMOJIS as _EMOJI_SANCTION_EMOJIS, SANCTION_EMOJI_DEFAULT

logger = logging.getLogger('moddy.moderation_cases')


class CaseType(Enum):
    """Type of moderation case"""
    INTERSERVER = "interserver"  # Inter-server sanctions
    GLOBAL = "global"  # Global bot sanctions


class SanctionType(Enum):
    """Types of sanctions available"""
    # Inter-server sanctions
    INTERSERVER_WARN = "interserver_warn"
    INTERSERVER_TIMEOUT = "interserver_timeout"
    INTERSERVER_BLACKLIST = "interserver_blacklist"

    # Global bot sanctions
    GLOBAL_WARN = "global_warn"
    GLOBAL_LIMITED = "global_limited"  # Very limited bot usage
    GLOBAL_BLACKLIST = "global_blacklist"


class CaseStatus(Enum):
    """Status of a case"""
    OPEN = "open"  # Case is active
    CLOSED = "closed"  # Case is closed (sanction revoked)


class EntityType(Enum):
    """Type of entity the case applies to"""
    USER = "user"
    GUILD = "guild"


# Mapping case types to available sanctions
CASE_TYPE_SANCTIONS = {
    CaseType.INTERSERVER: [
        SanctionType.INTERSERVER_WARN,
        SanctionType.INTERSERVER_TIMEOUT,
        SanctionType.INTERSERVER_BLACKLIST
    ],
    CaseType.GLOBAL: [
        SanctionType.GLOBAL_WARN,
        SanctionType.GLOBAL_LIMITED,
        SanctionType.GLOBAL_BLACKLIST
    ]
}


# Sanction names for display (English)
SANCTION_NAMES = {
    SanctionType.INTERSERVER_WARN: "Inter-Server Warning",
    SanctionType.INTERSERVER_TIMEOUT: "Inter-Server Time-Out",
    SanctionType.INTERSERVER_BLACKLIST: "Inter-Server Blacklist",
    SanctionType.GLOBAL_WARN: "Global Warning",
    SanctionType.GLOBAL_LIMITED: "Limited Bot Access",
    SanctionType.GLOBAL_BLACKLIST: "Global Blacklist"
}


# Sanction descriptions
SANCTION_DESCRIPTIONS = {
    SanctionType.INTERSERVER_WARN: "Warning for inter-server chat violations",
    SanctionType.INTERSERVER_TIMEOUT: "Temporary timeout from inter-server chat",
    SanctionType.INTERSERVER_BLACKLIST: "Permanent ban from inter-server chat",
    SanctionType.GLOBAL_WARN: "Warning for bot usage violations",
    SanctionType.GLOBAL_LIMITED: "Very limited access to bot features",
    SanctionType.GLOBAL_BLACKLIST: "Complete ban from using the bot"
}


# Sanction emojis - built from centralized registry
SANCTION_EMOJIS = {
    SanctionType(k.lower()): v
    for k, v in _EMOJI_SANCTION_EMOJIS.items()
}


def get_sanction_emoji(sanction_type: SanctionType) -> str:
    """Get emoji for a sanction type"""
    return SANCTION_EMOJIS.get(sanction_type, SANCTION_EMOJI_DEFAULT)


def get_sanction_name(sanction_type: SanctionType) -> str:
    """Get display name for a sanction type"""
    return SANCTION_NAMES.get(sanction_type, sanction_type.value)


def get_sanction_description(sanction_type: SanctionType) -> str:
    """Get description for a sanction type"""
    return SANCTION_DESCRIPTIONS.get(sanction_type, "")


def get_available_sanctions(case_type: CaseType) -> List[SanctionType]:
    """Get available sanctions for a case type"""
    return CASE_TYPE_SANCTIONS.get(case_type, [])


def validate_sanction_for_case_type(case_type: CaseType, sanction_type: SanctionType) -> bool:
    """Validate that a sanction type is valid for a case type"""
    available = CASE_TYPE_SANCTIONS.get(case_type, [])
    return sanction_type in available


class ModerationCase:
    """Represents a moderation case"""

    def __init__(
        self,
        case_id: str,
        case_type: CaseType,
        sanction_type: SanctionType,
        entity_type: EntityType,
        entity_id: int,
        status: CaseStatus,
        reason: str,
        evidence: Optional[str] = None,
        duration: Optional[int] = None,  # Duration in seconds for timeout
        staff_notes: Optional[List[Dict[str, Any]]] = None,
        created_by: int = None,
        created_at: datetime = None,
        updated_by: int = None,
        updated_at: datetime = None,
        closed_by: int = None,
        closed_at: datetime = None,
        close_reason: Optional[str] = None
    ):
        self.case_id = case_id
        self.case_type = case_type if isinstance(case_type, CaseType) else CaseType(case_type)
        self.sanction_type = sanction_type if isinstance(sanction_type, SanctionType) else SanctionType(sanction_type)
        self.entity_type = entity_type if isinstance(entity_type, EntityType) else EntityType(entity_type)
        self.entity_id = entity_id
        self.status = status if isinstance(status, CaseStatus) else CaseStatus(status)
        self.reason = reason
        self.evidence = evidence
        self.duration = duration
        self.staff_notes = staff_notes or []
        self.created_by = created_by
        self.created_at = created_at or datetime.now(timezone.utc)
        self.updated_by = updated_by
        self.updated_at = updated_at or datetime.now(timezone.utc)
        self.closed_by = closed_by
        self.closed_at = closed_at
        self.close_reason = close_reason

    @classmethod
    def from_db(cls, row: Dict[str, Any]) -> 'ModerationCase':
        """Create a ModerationCase from a database row"""
        return cls(
            case_id=row['case_id'],
            case_type=CaseType(row['case_type']),
            sanction_type=SanctionType(row['sanction_type']),
            entity_type=EntityType(row['entity_type']),
            entity_id=row['entity_id'],
            status=CaseStatus(row['status']),
            reason=row['reason'],
            evidence=row.get('evidence'),
            duration=row.get('duration'),
            staff_notes=row.get('staff_notes', []),
            created_by=row.get('created_by'),
            created_at=row.get('created_at'),
            updated_by=row.get('updated_by'),
            updated_at=row.get('updated_at'),
            closed_by=row.get('closed_by'),
            closed_at=row.get('closed_at'),
            close_reason=row.get('close_reason')
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert case to dictionary"""
        return {
            'case_id': self.case_id,
            'case_type': self.case_type.value,
            'sanction_type': self.sanction_type.value,
            'entity_type': self.entity_type.value,
            'entity_id': self.entity_id,
            'status': self.status.value,
            'reason': self.reason,
            'evidence': self.evidence,
            'duration': self.duration,
            'staff_notes': self.staff_notes,
            'created_by': self.created_by,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_by': self.updated_by,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'closed_by': self.closed_by,
            'closed_at': self.closed_at.isoformat() if self.closed_at else None,
            'close_reason': self.close_reason
        }

    def is_active(self) -> bool:
        """Check if case is currently active"""
        return self.status == CaseStatus.OPEN

    def get_sanction_emoji(self) -> str:
        """Get emoji for this case's sanction"""
        return get_sanction_emoji(self.sanction_type)

    def get_sanction_name(self) -> str:
        """Get display name for this case's sanction"""
        return get_sanction_name(self.sanction_type)
