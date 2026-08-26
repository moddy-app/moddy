"""
Support requests repository — what users send *to the Moddy team*.

Two features share one table because they are the same object seen from two
angles: a **bug report** (``/bug-report``) and a **configuration-help request**
(the button under Moddy's own announcements). Both are "a user wrote to the
team about one server, the team answers, the exchange is kept".

``support_requests``
    One row per request: who opened it, about which server, its wording, its
    status, and the staff card it was posted to (so a click on that card can
    refresh it years later).

``support_request_messages``
    The conversation: every staff reply and every follow-up from the reporter,
    in order. It is what the DM the reporter receives is built from, and what a
    second staffer reads before answering.

Both are addressed by uuid — the reference printed on the card and in the DM,
and what the persistent buttons carry in their ``custom_id``.
"""

from __future__ import annotations

import json
import logging
import uuid as uuid_module
from typing import Any, Dict, List, Optional

logger = logging.getLogger('moddy.database')

#: Kinds of request the table holds.
KIND_BUG = "bug"
KIND_CONFIG_HELP = "config_help"
KINDS = (KIND_BUG, KIND_CONFIG_HELP)

#: Lifecycle. ``claimed`` means a staffer took it; ``resolved`` closes it.
STATUS_OPEN = "open"
STATUS_CLAIMED = "claimed"
STATUS_RESOLVED = "resolved"
STATUSES = (STATUS_OPEN, STATUS_CLAIMED, STATUS_RESOLVED)

#: Who wrote one message of the exchange.
AUTHOR_STAFF = "staff"
AUTHOR_USER = "user"


def _as_uuid(value: Any) -> Optional[uuid_module.UUID]:
    if value is None:
        return None
    if isinstance(value, uuid_module.UUID):
        return value
    try:
        return uuid_module.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


class SupportRequestRepository:
    """CRUD for ``support_requests`` and ``support_request_messages``."""

    def _request_row(self, row) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        return {
            "id": row["id"],
            "kind": row["kind"],
            "user_id": row["user_id"],
            "guild_id": row["guild_id"],
            "guild_name": row["guild_name"],
            "locale": row["locale"],
            "subject": row["subject"],
            "body": row["body"],
            "details": self._parse_jsonb(row["details"]),
            "status": row["status"],
            "claimed_by": row["claimed_by"],
            "claimed_at": row["claimed_at"],
            "resolved_by": row["resolved_by"],
            "resolved_at": row["resolved_at"],
            "channel_id": row["channel_id"],
            "message_id": row["message_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    # ------------------------------------------------------------------ #
    # Creation
    # ------------------------------------------------------------------ #

    async def create_support_request(
        self,
        *,
        kind: str,
        user_id: int,
        guild_id: Optional[int] = None,
        guild_name: Optional[str] = None,
        locale: Optional[str] = None,
        subject: Optional[str] = None,
        body: str = "",
        details: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Open a request and return the stored row (uuid included).

        The uuid is generated here because the staff card's buttons carry it in
        their ``custom_id`` — the row must exist before the card is posted.
        """
        request_id = uuid_module.uuid4()
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO support_requests (
                    id, kind, user_id, guild_id, guild_name, locale,
                    subject, body, details
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
                RETURNING *
            """, request_id, kind, user_id, guild_id, guild_name, locale,
                 subject, body, json.dumps(details or {}, ensure_ascii=False))
        return self._request_row(row)

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #

    async def get_support_request(self, request_id: Any) -> Optional[Dict[str, Any]]:
        uid = _as_uuid(request_id)
        if uid is None:
            return None
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM support_requests WHERE id = $1", uid)
        return self._request_row(row)

    async def count_recent_support_requests(self, user_id: int, kind: str,
                                            minutes: int = 10) -> int:
        """How many requests of this kind the user opened recently.

        The anti-spam counter behind ``/bug-report``: the command is open to
        everyone, and one frustrated user must not be able to fill the team's
        channel.
        """
        async with self.pool.acquire() as conn:
            return await conn.fetchval("""
                SELECT COUNT(*) FROM support_requests
                WHERE user_id = $1 AND kind = $2
                  AND created_at > now() - ($3 || ' minutes')::interval
            """, user_id, kind, str(int(minutes))) or 0

    async def list_support_requests(self, *, kind: Optional[str] = None,
                                    status: Optional[str] = None,
                                    user_id: Optional[int] = None,
                                    limit: int = 25) -> List[Dict[str, Any]]:
        clauses, args = [], []
        for column, value in (("kind", kind), ("status", status), ("user_id", user_id)):
            if value is not None:
                args.append(value)
                clauses.append(f"{column} = ${len(args)}")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        args.append(limit)
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT * FROM support_requests {where} "
                f"ORDER BY created_at DESC LIMIT ${len(args)}", *args)
        return [self._request_row(row) for row in rows]

    # ------------------------------------------------------------------ #
    # Updates
    # ------------------------------------------------------------------ #

    async def set_support_request_card(self, request_id: Any, channel_id: int,
                                       message_id: int) -> None:
        """Remember where the staff card was posted, so it can be refreshed."""
        uid = _as_uuid(request_id)
        if uid is None:
            return
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE support_requests
                   SET channel_id = $2, message_id = $3, updated_at = now()
                 WHERE id = $1
            """, uid, channel_id, message_id)

    async def claim_support_request(self, request_id: Any,
                                    staff_id: int) -> Optional[Dict[str, Any]]:
        """Assign an unclaimed request. ``None`` when someone got there first."""
        uid = _as_uuid(request_id)
        if uid is None:
            return None
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                UPDATE support_requests
                   SET claimed_by = $2, claimed_at = now(),
                       status = CASE WHEN status = 'open' THEN 'claimed' ELSE status END,
                       updated_at = now()
                 WHERE id = $1 AND claimed_by IS NULL AND status <> 'resolved'
             RETURNING *
            """, uid, staff_id)
        return self._request_row(row)

    async def resolve_support_request(self, request_id: Any,
                                      staff_id: int) -> Optional[Dict[str, Any]]:
        uid = _as_uuid(request_id)
        if uid is None:
            return None
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                UPDATE support_requests
                   SET status = 'resolved', resolved_by = $2, resolved_at = now(),
                       updated_at = now()
                 WHERE id = $1 AND status <> 'resolved'
             RETURNING *
            """, uid, staff_id)
        return self._request_row(row)

    # ------------------------------------------------------------------ #
    # Conversation
    # ------------------------------------------------------------------ #

    async def add_support_request_message(
        self, *, request_id: Any, author: str, author_id: int, body: str,
        notification_id: Optional[Any] = None,
    ) -> Optional[Dict[str, Any]]:
        """Append one message of the exchange (a staff reply or a follow-up)."""
        uid = _as_uuid(request_id)
        if uid is None:
            return None
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO support_request_messages (
                    request_id, author, author_id, body, notification_id
                )
                VALUES ($1, $2, $3, $4, $5)
                RETURNING *
            """, uid, author, author_id, body, _as_uuid(notification_id))
            await conn.execute(
                "UPDATE support_requests SET updated_at = now() WHERE id = $1", uid)
        if row is None:
            return None
        return {
            "id": row["id"],
            "request_id": row["request_id"],
            "author": row["author"],
            "author_id": row["author_id"],
            "body": row["body"],
            "notification_id": row["notification_id"],
            "created_at": row["created_at"],
        }

    async def get_support_request_messages(self, request_id: Any,
                                           limit: int = 50) -> List[Dict[str, Any]]:
        uid = _as_uuid(request_id)
        if uid is None:
            return []
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM support_request_messages
                 WHERE request_id = $1
              ORDER BY created_at ASC
                 LIMIT $2
            """, uid, limit)
        return [{
            "id": row["id"],
            "request_id": row["request_id"],
            "author": row["author"],
            "author_id": row["author_id"],
            "body": row["body"],
            "notification_id": row["notification_id"],
            "created_at": row["created_at"],
        } for row in rows]
