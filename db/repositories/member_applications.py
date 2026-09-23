"""Membership applications Moddy has put in front of a server's staff.

Discord's "Apply to Join" gate keeps the applications themselves; this table
only remembers what Moddy did with each one — which review card it posted,
where, and how the application ended. One row per **join request id**.

Three things the shape is built around:

**The insert is the dedupe.** An application can reach the bot twice — once
from the gateway, once from the reconciliation poll — and possibly at the same
instant. ``claim_member_application`` is an ``INSERT … ON CONFLICT DO NOTHING``
and only the caller whose insert landed posts the card, so a server never sees
two cards for one applicant.

**The first decision wins.** A moderator clicking *Approve* on the card and
another one acting from Discord's own review screen race each other.
``resolve_member_application`` only moves a row out of ``SUBMITTED``, so the
second writer gets ``None`` back and simply re-renders what the first one
decided.

**The snapshot is the card.** Bots have no endpoint to fetch one join request
by id, so the request as Discord sent it (answers, applicant) is stored in
``request`` and every re-render of the card reads it from here — after a
restart as much as on the next click.

Rows are purged after 180 days, the same retention Discord applies to the
join requests themselves: the answers are personal data and outlive their use
the moment the application is closed.

See docs/MEMBER_APPLICATIONS.md.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger('moddy.database')

# Statuses Moddy stores. The first three are Discord's own; WITHDRAWN is
# Moddy's name for a request Discord deleted (the applicant took it back).
STATUS_SUBMITTED = "SUBMITTED"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"
STATUS_WITHDRAWN = "WITHDRAWN"
STATUSES = (STATUS_SUBMITTED, STATUS_APPROVED, STATUS_REJECTED, STATUS_WITHDRAWN)

RETENTION_DAYS = 180


def _parse_json(value: Any) -> Dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _row_to_dict(row) -> Dict[str, Any]:
    return {
        "request_id": row["request_id"],
        "guild_id": row["guild_id"],
        "user_id": row["user_id"],
        "status": row["status"],
        "request": _parse_json(row["request"]),
        "channel_id": row["channel_id"],
        "message_id": row["message_id"],
        "reviewed_by": row["reviewed_by"],
        "reviewed_at": row["reviewed_at"],
        "rejection_reason": row["rejection_reason"],
        "decided_in": row["decided_in"],
        "submitted_at": row["submitted_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


class MemberApplicationRepository:
    """Review cards of membership applications (``member_applications``)."""

    async def claim_member_application(self, request_id: int, guild_id: int,
                                       user_id: int, request: Dict[str, Any],
                                       submitted_at: Optional[datetime]) -> Optional[Dict[str, Any]]:
        """Record a submitted application. Returns the row only if it is new.

        ``None`` means another path (gateway or poll) already owns it — that
        caller posts the card, this one must not.
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO member_applications (
                    request_id, guild_id, user_id, status, request, submitted_at
                ) VALUES ($1, $2, $3, 'SUBMITTED', $4::jsonb, $5)
                ON CONFLICT (request_id) DO NOTHING
                RETURNING *
            """, request_id, guild_id, user_id, json.dumps(request), submitted_at)
        return _row_to_dict(row) if row else None

    async def get_member_application(self, request_id: int) -> Optional[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM member_applications WHERE request_id = $1", request_id)
        return _row_to_dict(row) if row else None

    async def set_member_application_message(self, request_id: int,
                                             channel_id: int, message_id: int) -> None:
        """Remember where the review card went, so every later event can edit it."""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE member_applications
                SET channel_id = $2, message_id = $3, updated_at = now()
                WHERE request_id = $1
            """, request_id, channel_id, message_id)

    async def list_pending_member_applications(self, guild_id: int) -> List[Dict[str, Any]]:
        """Every application of a guild still awaiting a decision."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM member_applications
                WHERE guild_id = $1 AND status = 'SUBMITTED'
                ORDER BY request_id
            """, guild_id)
        return [_row_to_dict(row) for row in rows]

    async def resolve_member_application(self, request_id: int, status: str, *,
                                         reviewed_by: Optional[int],
                                         rejection_reason: Optional[str],
                                         decided_in: str) -> Optional[Dict[str, Any]]:
        """Close a pending application. Returns the row, or ``None`` if it was
        no longer pending — somebody else decided first."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                UPDATE member_applications
                SET status = $2, reviewed_by = $3, rejection_reason = $4,
                    decided_in = $5, reviewed_at = now(), updated_at = now()
                WHERE request_id = $1 AND status = 'SUBMITTED'
                RETURNING *
            """, request_id, status, reviewed_by, rejection_reason, decided_in)
        return _row_to_dict(row) if row else None

    async def count_member_applications(self, guild_id: int, user_id: int, *,
                                        exclude_request_id: Optional[int] = None) -> Dict[str, int]:
        """Earlier applications of one person to one server, by status.

        What the card shows as history: someone applying for the fourth time
        after three rejections is information the reviewer should not have to
        dig for.
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT status, COUNT(*) AS n FROM member_applications
                WHERE guild_id = $1 AND user_id = $2
                  AND ($3::bigint IS NULL OR request_id <> $3)
                GROUP BY status
            """, guild_id, user_id, exclude_request_id)
        return {row["status"]: row["n"] for row in rows}

    async def purge_member_applications(self, days: int = RETENTION_DAYS) -> int:
        """Drop applications older than Discord's own retention for them."""
        async with self.pool.acquire() as conn:
            result = await conn.execute("""
                DELETE FROM member_applications
                WHERE created_at < now() - make_interval(days => $1)
            """, days)
        try:
            return int(result.split()[-1])
        except (ValueError, IndexError, AttributeError):
            return 0
