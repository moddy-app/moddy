"""
Moderation cases repository.

Implements the case/sanction/event model described in the moderation cases
spec: a case is a folder decoupled from the Discord context, it carries one or
more sanctions (each with its own lifecycle) and a chronological event timeline.

The bot is the source of truth here (the schema lives in ``db/base.py``); the
internal API and dashboard read through the same tables.
"""

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

import asyncpg

from utils.moderation_cases import (
    generate_reference,
    CaseStatus,
    EventType,
    StatusTrigger,
)

logger = logging.getLogger('moddy.database')

# How many times we retry reference generation on a UNIQUE collision.
_REFERENCE_MAX_RETRIES = 10


class ModerationRepository:
    """Case / sanction / event database operations."""

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _new_uuid() -> uuid.UUID:
        return uuid.uuid4()

    def _row(self, row: Optional[asyncpg.Record]) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        d = dict(row)
        if "payload" in d:
            d["payload"] = self._parse_jsonb(d["payload"]) if d["payload"] else None
        return d

    # ------------------------------------------------------------ create case
    async def create_case(
        self,
        *,
        case_type: str,
        subject_type: str,
        subject_id: Union[str, int],
        issuer_type: str,
        issuer_id: Optional[Union[str, int]],
        scope_type: str,
        scope_id: Optional[Union[str, int]] = None,
        reason: str,
        group_id: Optional[uuid.UUID] = None,
        # First sanction (a case is always opened with at least one sanction).
        action: Optional[str] = None,
        sanction_expires_at: Optional[datetime] = None,
        sanction_note: Optional[str] = None,
        issued_by_type: Optional[str] = None,
        issued_by_id: Optional[Union[str, int]] = None,
    ) -> Dict[str, Any]:
        """Create a case (optionally with its first sanction) and return it.

        Returns a dict with ``id`` (UUID) and ``reference`` (public id).
        """
        case_id = self._new_uuid()
        subject_id = str(subject_id)
        issuer_id = str(issuer_id) if issuer_id is not None else None
        scope_id = str(scope_id) if scope_id is not None else None

        # The first sanction issuer defaults to the case issuer.
        if action is not None:
            issued_by_type = issued_by_type or issuer_type
            issued_by_id = str(issued_by_id) if issued_by_id is not None else issuer_id

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # Insert the case, retrying the public reference on collision.
                reference = None
                for _ in range(_REFERENCE_MAX_RETRIES):
                    candidate = generate_reference()
                    try:
                        await conn.execute(
                            """
                            INSERT INTO cases (
                                id, reference, type, subject_type, subject_id,
                                issuer_type, issuer_id, scope_type, scope_id,
                                reason, status, group_id
                            ) VALUES (
                                $1, $2, $3::case_type, $4::subject_type, $5,
                                $6::issuer_type, $7, $8::scope_type, $9,
                                $10, 'open'::case_status, $11
                            )
                            """,
                            case_id, candidate, case_type, subject_type, subject_id,
                            issuer_type, issuer_id, scope_type, scope_id,
                            reason, group_id,
                        )
                        reference = candidate
                        break
                    except asyncpg.UniqueViolationError:
                        continue
                if reference is None:
                    raise RuntimeError("Could not generate a unique case reference")

                # Optionally add the first sanction + its event (same tx).
                sanction_id = None
                if action is not None:
                    sanction_id = await self._insert_sanction(
                        conn, case_id, action, issued_by_type, issued_by_id,
                        sanction_expires_at, sanction_note,
                    )

        return {"id": case_id, "reference": reference, "sanction_id": sanction_id}

    async def _insert_sanction(
        self, conn, case_id, action, issued_by_type, issued_by_id,
        expires_at, note,
    ) -> uuid.UUID:
        """Insert a sanction row + its ``sanction_added`` event (no recompute)."""
        sanction_id = self._new_uuid()
        await conn.execute(
            """
            INSERT INTO case_sanctions (
                id, case_id, action, expires_at, status,
                issued_by_type, issued_by_id, note
            ) VALUES (
                $1, $2, $3::sanction_action, $4, 'active'::sanction_status,
                $5::issuer_type, $6, $7
            )
            """,
            sanction_id, case_id, action, expires_at,
            issued_by_type, issued_by_id, note,
        )
        await self._insert_event(
            conn, case_id, EventType.SANCTION_ADDED.value,
            author_type=self._issuer_to_author(issued_by_type),
            author_id=issued_by_id,
            payload={"sanction_id": str(sanction_id), "action": action},
        )
        return sanction_id

    @staticmethod
    def _issuer_to_author(issuer_type: Optional[str]) -> Optional[str]:
        """Map an issuer_type to the matching author_type for events."""
        if issuer_type in ("discord_user",):
            return "discord_user"
        if issuer_type in ("moddy_staff",):
            return "moddy_staff"
        return "system"

    async def _insert_event(
        self, conn, case_id, event_type, *,
        author_type=None, author_id=None, content=None, payload=None,
    ) -> uuid.UUID:
        event_id = self._new_uuid()
        await conn.execute(
            """
            INSERT INTO case_events (
                id, case_id, type, author_type, author_id, content, payload
            ) VALUES (
                $1, $2, $3::event_type, $4::author_type, $5, $6, $7::jsonb
            )
            """,
            event_id, case_id, event_type, author_type,
            str(author_id) if author_id is not None else None,
            content, json.dumps(payload) if payload is not None else None,
        )
        return event_id

    # -------------------------------------------------------------- read case
    async def get_case_by_reference(self, reference: str) -> Optional[Dict[str, Any]]:
        """Fetch a case + its sanctions + timeline by public reference."""
        async with self.pool.acquire() as conn:
            case = await conn.fetchrow(
                "SELECT * FROM cases WHERE reference = $1", reference.strip().upper()
            )
            if not case:
                return None
            return await self._assemble_case(conn, case)

    async def get_case_by_id(self, case_id: uuid.UUID) -> Optional[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            case = await conn.fetchrow("SELECT * FROM cases WHERE id = $1", case_id)
            if not case:
                return None
            return await self._assemble_case(conn, case)

    async def _assemble_case(self, conn, case_row: asyncpg.Record) -> Dict[str, Any]:
        case_id = case_row["id"]
        sanctions = await conn.fetch(
            "SELECT * FROM case_sanctions WHERE case_id = $1 ORDER BY created_at ASC",
            case_id,
        )
        events = await conn.fetch(
            "SELECT * FROM case_events WHERE case_id = $1 ORDER BY created_at ASC",
            case_id,
        )
        return {
            "case": dict(case_row),
            "sanctions": [dict(s) for s in sanctions],
            "events": [self._row(e) for e in events],
        }

    async def list_subject_cases(
        self,
        subject_type: str,
        subject_id: Union[str, int],
        status: Optional[str] = None,
        limit: int = 25,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """List cases for a subject (most recent first)."""
        async with self.pool.acquire() as conn:
            query = "SELECT * FROM cases WHERE subject_type = $1::subject_type AND subject_id = $2"
            params: List[Any] = [subject_type, str(subject_id)]
            if status:
                query += f" AND status = ${len(params) + 1}::case_status"
                params.append(status)
            query += f" ORDER BY created_at DESC LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}"
            params.extend([limit, offset])
            rows = await conn.fetch(query, *params)
            return [dict(r) for r in rows]

    async def count_subject_cases(
        self, subject_type: str, subject_id: Union[str, int],
        status: Optional[str] = None,
    ) -> int:
        async with self.pool.acquire() as conn:
            query = "SELECT COUNT(*) FROM cases WHERE subject_type = $1::subject_type AND subject_id = $2"
            params: List[Any] = [subject_type, str(subject_id)]
            if status:
                query += f" AND status = ${len(params) + 1}::case_status"
                params.append(status)
            return await conn.fetchval(query, *params)

    async def list_scope_cases(
        self, scope_type: str, scope_id: Union[str, int],
        limit: int = 25, offset: int = 0,
    ) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM cases
                WHERE scope_type = $1::scope_type AND scope_id = $2
                ORDER BY created_at DESC LIMIT $3 OFFSET $4
                """,
                scope_type, str(scope_id), limit, offset,
            )
            return [dict(r) for r in rows]

    # ------------------------------------------------------- filtered browse
    @staticmethod
    def _case_filters(
        *,
        subject_type: Optional[str], subject_id: Optional[Union[str, int]],
        scope_type: Optional[str], scope_id: Optional[Union[str, int]],
        status: Optional[str], case_type: Optional[str], action: Optional[str],
        since: Optional[datetime], until: Optional[datetime],
    ) -> tuple:
        """Build the shared WHERE clauses + params for the case browser.

        Returns ``(conds: list[str], params: list)``. Each filter is optional so
        the same builder powers ``search_cases`` (paged rows) and
        ``count_cases`` (total) with identical semantics.
        """
        conds: List[str] = []
        params: List[Any] = []

        if subject_type is not None and subject_id is not None:
            params.append(subject_type)
            st = len(params)
            params.append(str(subject_id))
            si = len(params)
            conds.append(f"c.subject_type = ${st}::subject_type AND c.subject_id = ${si}")
        if scope_type is not None and scope_id is not None:
            params.append(scope_type)
            sct = len(params)
            params.append(str(scope_id))
            sci = len(params)
            conds.append(f"c.scope_type = ${sct}::scope_type AND c.scope_id = ${sci}")
        if status:
            params.append(status)
            conds.append(f"c.status = ${len(params)}::case_status")
        if case_type:
            params.append(case_type)
            conds.append(f"c.type = ${len(params)}::case_type")
        if action:
            params.append(action)
            conds.append(
                "EXISTS (SELECT 1 FROM case_sanctions s "
                f"WHERE s.case_id = c.id AND s.action = ${len(params)}::sanction_action)"
            )
        if since is not None:
            params.append(since)
            conds.append(f"c.created_at >= ${len(params)}")
        if until is not None:
            params.append(until)
            conds.append(f"c.created_at <= ${len(params)}")

        return conds, params

    async def search_cases(
        self,
        *,
        subject_type: Optional[str] = None,
        subject_id: Optional[Union[str, int]] = None,
        scope_type: Optional[str] = None,
        scope_id: Optional[Union[str, int]] = None,
        status: Optional[str] = None,
        case_type: Optional[str] = None,
        action: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        limit: int = 6,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Filtered, paginated case listing (most recent first).

        Each row carries two computed helpers used by the browser UI:
        ``actions`` (distinct sanction actions on the case) and ``has_active``
        (whether any sanction is currently active). One query, no N+1.
        """
        conds, params = self._case_filters(
            subject_type=subject_type, subject_id=subject_id,
            scope_type=scope_type, scope_id=scope_id, status=status,
            case_type=case_type, action=action, since=since, until=until,
        )
        where_sql = (" WHERE " + " AND ".join(conds)) if conds else ""
        params.append(limit)
        lim = len(params)
        params.append(offset)
        off = len(params)
        query = f"""
            SELECT c.*,
              COALESCE((SELECT array_agg(DISTINCT s.action::text)
                        FROM case_sanctions s WHERE s.case_id = c.id), '{{}}') AS actions,
              EXISTS(SELECT 1 FROM case_sanctions s
                     WHERE s.case_id = c.id AND s.status = 'active'::sanction_status) AS has_active
            FROM cases c
            {where_sql}
            ORDER BY c.created_at DESC
            LIMIT ${lim} OFFSET ${off}
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
            return [dict(r) for r in rows]

    async def count_cases(
        self,
        *,
        subject_type: Optional[str] = None,
        subject_id: Optional[Union[str, int]] = None,
        scope_type: Optional[str] = None,
        scope_id: Optional[Union[str, int]] = None,
        status: Optional[str] = None,
        case_type: Optional[str] = None,
        action: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> int:
        """Total number of cases matching the same filters as ``search_cases``."""
        conds, params = self._case_filters(
            subject_type=subject_type, subject_id=subject_id,
            scope_type=scope_type, scope_id=scope_id, status=status,
            case_type=case_type, action=action, since=since, until=until,
        )
        where_sql = (" WHERE " + " AND ".join(conds)) if conds else ""
        query = f"SELECT COUNT(*) FROM cases c{where_sql}"
        async with self.pool.acquire() as conn:
            return await conn.fetchval(query, *params)

    async def list_subject_scopes(
        self, subject_type: str, subject_id: Union[str, int],
    ) -> List[Dict[str, Any]]:
        """Distinct scopes a subject has cases in, with per-scope counts.

        Powers the "filter by server" picker of the personal cases browser.
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT scope_type, scope_id, COUNT(*) AS count
                FROM cases
                WHERE subject_type = $1::subject_type AND subject_id = $2
                GROUP BY scope_type, scope_id
                ORDER BY count DESC
                """,
                subject_type, str(subject_id),
            )
            return [dict(r) for r in rows]

    # --------------------------------------------------------------- mutators
    async def update_case_reason(self, case_id: uuid.UUID, reason: str) -> bool:
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE cases SET reason = $1, updated_at = now() WHERE id = $2",
                reason, case_id,
            )
            return result == "UPDATE 1"

    async def add_sanction(
        self,
        case_id: uuid.UUID,
        action: str,
        issued_by_type: str,
        issued_by_id: Optional[Union[str, int]],
        expires_at: Optional[datetime] = None,
        note: Optional[str] = None,
    ) -> Optional[uuid.UUID]:
        """Add a sanction to an existing case, then recompute status."""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                sanction_id = await self._insert_sanction(
                    conn, case_id, action, issued_by_type,
                    str(issued_by_id) if issued_by_id is not None else None,
                    expires_at, note,
                )
                await conn.execute("UPDATE cases SET updated_at = now() WHERE id = $1", case_id)
                await self._recompute_status(conn, case_id, StatusTrigger.SYSTEM)
            return sanction_id

    async def revoke_sanction(
        self,
        sanction_id: uuid.UUID,
        by_type: str,
        by_id: Optional[Union[str, int]],
    ) -> bool:
        """Revoke an active sanction, log the event and recompute case status."""
        by_id = str(by_id) if by_id is not None else None
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    UPDATE case_sanctions
                    SET status = 'revoked'::sanction_status,
                        revoked_at = now(),
                        revoked_by_type = $2::issuer_type,
                        revoked_by_id = $3
                    WHERE id = $1 AND status = 'active'::sanction_status
                    RETURNING case_id, action
                    """,
                    sanction_id, by_type, by_id,
                )
                if not row:
                    return False
                case_id = row["case_id"]
                await self._insert_event(
                    conn, case_id, EventType.SANCTION_REVOKED.value,
                    author_type=self._issuer_to_author(by_type),
                    author_id=by_id,
                    payload={"sanction_id": str(sanction_id), "by_type": by_type, "by_id": by_id},
                )
                await conn.execute("UPDATE cases SET updated_at = now() WHERE id = $1", case_id)
                await self._recompute_status(conn, case_id, StatusTrigger.REVOCATION)
                return True

    async def add_event(
        self,
        case_id: uuid.UUID,
        event_type: str,
        *,
        author_type: Optional[str] = None,
        author_id: Optional[Union[str, int]] = None,
        content: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> uuid.UUID:
        """Add a timeline event (comment / note / evidence / …)."""
        async with self.pool.acquire() as conn:
            event_id = await self._insert_event(
                conn, case_id, event_type,
                author_type=author_type, author_id=author_id,
                content=content, payload=payload,
            )
            await conn.execute("UPDATE cases SET updated_at = now() WHERE id = $1", case_id)
            return event_id

    async def set_status_manual(
        self,
        case_id: uuid.UUID,
        status: str,
        author_type: str,
        author_id: Optional[Union[str, int]],
    ) -> bool:
        """Manually open/close a case. Locks the status against auto-recompute."""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                current = await conn.fetchrow(
                    "SELECT status FROM cases WHERE id = $1", case_id
                )
                if not current:
                    return False
                old = current["status"]
                if old == status:
                    # Still lock it, but no transition event.
                    await conn.execute(
                        "UPDATE cases SET status_locked = TRUE, updated_at = now() WHERE id = $1",
                        case_id,
                    )
                    return True
                await conn.execute(
                    """
                    UPDATE cases
                    SET status = $2::case_status, status_locked = TRUE, updated_at = now()
                    WHERE id = $1
                    """,
                    case_id, status,
                )
                await self._insert_event(
                    conn, case_id, EventType.STATUS_CHANGE.value,
                    author_type=self._issuer_to_author(author_type),
                    author_id=str(author_id) if author_id is not None else None,
                    payload={"from": old, "to": status, "trigger": StatusTrigger.MANUAL.value},
                )
                return True

    async def _recompute_status(self, conn, case_id: uuid.UUID, trigger: StatusTrigger):
        """Re-derive case status from its active sanctions.

        Respects a manual ``status_locked`` override: a locked case is never
        auto-reopened/closed by sanction lifecycle changes.
        """
        case = await conn.fetchrow(
            "SELECT status, status_locked FROM cases WHERE id = $1", case_id
        )
        if not case or case["status_locked"]:
            return
        active = await conn.fetchval(
            "SELECT COUNT(*) FROM case_sanctions WHERE case_id = $1 AND status = 'active'::sanction_status",
            case_id,
        )
        new_status = CaseStatus.OPEN.value if active > 0 else CaseStatus.CLOSED.value
        if new_status == case["status"]:
            return
        await conn.execute(
            "UPDATE cases SET status = $2::case_status, updated_at = now() WHERE id = $1",
            case_id, new_status,
        )
        await self._insert_event(
            conn, case_id, EventType.STATUS_CHANGE.value,
            author_type="system", author_id=None,
            payload={"from": case["status"], "to": new_status, "trigger": trigger.value},
        )

    # ----------------------------------------------------------- expiry job
    async def expire_due_sanctions(self) -> List[Dict[str, Any]]:
        """Expire temporary sanctions whose ``expires_at`` has passed.

        Returns the list of expired sanctions (each a dict with ``action``,
        ``case_type``, ``subject_type``, ``subject_id``, ``scope_type`` and
        ``scope_id``) so the caller can reverse the matching Discord action
        (e.g. unban a temporary ban). Each expiry logs an event and recomputes
        the parent case status.
        """
        expired: List[Dict[str, Any]] = []
        async with self.pool.acquire() as conn:
            due = await conn.fetch(
                """
                SELECT s.id, s.case_id, s.action,
                       c.type AS case_type, c.subject_type, c.subject_id,
                       c.scope_type, c.scope_id
                FROM case_sanctions s
                JOIN cases c ON c.id = s.case_id
                WHERE s.status = 'active'::sanction_status
                  AND s.expires_at IS NOT NULL AND s.expires_at <= now()
                """
            )
            for row in due:
                async with conn.transaction():
                    updated = await conn.execute(
                        """
                        UPDATE case_sanctions
                        SET status = 'expired'::sanction_status
                        WHERE id = $1 AND status = 'active'::sanction_status
                        """,
                        row["id"],
                    )
                    if updated != "UPDATE 1":
                        continue
                    await self._insert_event(
                        conn, row["case_id"], EventType.SANCTION_EXPIRED.value,
                        author_type="system", author_id=None,
                        payload={"sanction_id": str(row["id"]), "action": row["action"]},
                    )
                    await self._recompute_status(conn, row["case_id"], StatusTrigger.EXPIRATION)
                    expired.append(dict(row))
            if expired:
                logger.info("[Cases] Expired %d sanction(s)", len(expired))
            return expired

    # ------------------------------------------------------ source linking
    async def find_open_case(
        self,
        subject_type: str,
        subject_id: Union[str, int],
        case_type: str,
        scope_type: str,
        scope_id: Optional[Union[str, int]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Most recent OPEN case for a (subject, type, scope), or None.

        Used by the case service to append a sanction to an existing folder
        instead of opening a duplicate.
        """
        async with self.pool.acquire() as conn:
            query = """
                SELECT * FROM cases
                WHERE subject_type = $1::subject_type AND subject_id = $2
                  AND type = $3::case_type
                  AND scope_type = $4::scope_type
                  AND status = 'open'::case_status
            """
            params: List[Any] = [subject_type, str(subject_id), case_type, scope_type]
            if scope_id is None:
                query += " AND scope_id IS NULL"
            else:
                query += f" AND scope_id = ${len(params) + 1}"
                params.append(str(scope_id))
            query += " ORDER BY created_at DESC LIMIT 1"
            row = await conn.fetchrow(query, *params)
            return dict(row) if row else None

    async def revoke_active_sanctions_for(
        self,
        subject_type: str,
        subject_id: Union[str, int],
        case_type: str,
        scope_type: str,
        scope_id: Optional[Union[str, int]],
        action: str,
        by_type: str,
        by_id: Optional[Union[str, int]],
    ) -> int:
        """Revoke every active sanction of ``action`` for a subject's matching
        open case(s). Returns the number revoked. Used for lifts (unban, etc.)."""
        by_id = str(by_id) if by_id is not None else None
        async with self.pool.acquire() as conn:
            query = """
                SELECT s.id FROM case_sanctions s
                JOIN cases c ON c.id = s.case_id
                WHERE c.subject_type = $1::subject_type AND c.subject_id = $2
                  AND c.type = $3::case_type AND c.scope_type = $4::scope_type
                  AND s.action = $5::sanction_action
                  AND s.status = 'active'::sanction_status
            """
            params: List[Any] = [subject_type, str(subject_id), case_type, scope_type, action]
            if scope_id is None:
                query += " AND c.scope_id IS NULL"
            else:
                query += f" AND c.scope_id = ${len(params) + 1}"
                params.append(str(scope_id))
            rows = await conn.fetch(query, *params)

        count = 0
        for row in rows:
            if await self.revoke_sanction(row["id"], by_type, by_id):
                count += 1
        return count

    # ----------------------------------------------------------- queries
    async def has_active_sanction(
        self,
        subject_type: str,
        subject_id: Union[str, int],
        *,
        case_type: Optional[str] = None,
        action: Optional[str] = None,
    ) -> bool:
        """Whether a subject currently has an active sanction matching filters."""
        async with self.pool.acquire() as conn:
            query = """
                SELECT EXISTS(
                    SELECT 1 FROM case_sanctions s
                    JOIN cases c ON c.id = s.case_id
                    WHERE c.subject_type = $1::subject_type
                      AND c.subject_id = $2
                      AND s.status = 'active'::sanction_status
                      AND (s.expires_at IS NULL OR s.expires_at > now())
            """
            params: List[Any] = [subject_type, str(subject_id)]
            if case_type:
                query += f" AND c.type = ${len(params) + 1}::case_type"
                params.append(case_type)
            if action:
                query += f" AND s.action = ${len(params) + 1}::sanction_action"
                params.append(action)
            query += ")"
            return await conn.fetchval(query, *params)

    async def list_automod_evidence_message_ids(
        self,
        subject_id: Union[str, int],
        guild_id: Union[str, int],
        limit: int = 25,
    ) -> List[Dict[str, Any]]:
        """Message ids already moderated by automod for a subject in a guild.

        Reads ``evidence`` events tagged ``payload.source = 'automod'`` on the
        subject's guild cases. Feeds nano's ``messages_deja_moderes`` so it never
        re-sanctions the current message for conduct already actioned earlier.
        Each item: ``{"id": <message_id>, "extrait": <short content>}``.
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT e.payload, e.content, e.created_at
                FROM case_events e
                JOIN cases c ON c.id = e.case_id
                WHERE c.subject_type = 'discord_user'::subject_type
                  AND c.subject_id = $1
                  AND c.scope_type = 'discord_guild'::scope_type
                  AND c.scope_id = $2
                  AND e.type = 'evidence'::event_type
                  AND e.payload ->> 'source' = 'automod'
                  AND e.payload ->> 'message_id' IS NOT NULL
                ORDER BY e.created_at DESC
                LIMIT $3
                """,
                str(subject_id), str(guild_id), limit,
            )
        out: List[Dict[str, Any]] = []
        for r in rows:
            payload = self._parse_jsonb(r["payload"]) if r["payload"] else {}
            mid = payload.get("message_id")
            if not mid:
                continue
            out.append({"id": str(mid), "extrait": (payload.get("extrait") or "")[:120]})
        return out

    async def get_active_subject_sanctions(
        self, subject_type: str, subject_id: Union[str, int],
        case_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """All active sanctions for a subject (joined with their case)."""
        async with self.pool.acquire() as conn:
            query = """
                SELECT s.*, c.reference, c.type AS case_type
                FROM case_sanctions s
                JOIN cases c ON c.id = s.case_id
                WHERE c.subject_type = $1::subject_type AND c.subject_id = $2
                  AND s.status = 'active'::sanction_status
            """
            params: List[Any] = [subject_type, str(subject_id)]
            if case_type:
                query += f" AND c.type = ${len(params) + 1}::case_type"
                params.append(case_type)
            query += " ORDER BY s.created_at DESC"
            rows = await conn.fetch(query, *params)
            return [dict(r) for r in rows]
