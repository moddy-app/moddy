"""
Global sanction enforcement repository.

When the Moddy team sanctions someone, some consequences are **immediate** (the
sanction applies the moment the case is written) and some are **deferred** so
the subject has a chance to appeal first:

- a **premium** subject's subscription is cancelled without refund;
- a **suspended** server is left by the bot and its stored data may be dropped.

Those deferred consequences are scheduled once per **case group** — a single
infraction usually produces several cases (the user *and* their server), and
the subject must receive one notice, not one per case. A ``case_enforcements``
row is that schedule: one deadline, one status, one DM.

``status``: ``pending`` → ``halted`` (an appeal was filed, the countdown is
stopped) | ``executed`` (the deadline passed) | ``cancelled`` (the sanction was
lifted before the deadline).

The case timeline (``case_events``) keeps the human-readable trace; this table
is the queryable state machine the sweeper reads.
"""

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger('moddy.database')


class EnforcementRepository:
    """CRUD for global sanction enforcement countdowns (``case_enforcements``)."""

    async def create_enforcement(
        self,
        *,
        group_id: Union[str, uuid.UUID],
        subject_type: str,
        subject_id: Union[str, int],
        level: str,
        deadline: datetime,
        premium: bool = False,
    ) -> Dict[str, Any]:
        """Schedule the deferred consequences of a global sanction group.

        Idempotent per group: re-applying a sanction to a group that already has
        a pending countdown keeps the original deadline (the subject was already
        notified and their grace period is already running).
        """
        if isinstance(group_id, str):
            group_id = uuid.UUID(group_id)
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO case_enforcements (
                    group_id, subject_type, subject_id, level, deadline, premium
                ) VALUES ($1,$2::subject_type,$3,$4,$5,$6)
                ON CONFLICT (group_id) DO UPDATE
                    SET level = EXCLUDED.level,
                        premium = EXCLUDED.premium,
                        updated_at = now()
                RETURNING *
                """,
                group_id, subject_type, str(subject_id), level, deadline, premium,
            )
            return dict(row)

    async def get_enforcement(
        self, group_id: Union[str, uuid.UUID]
    ) -> Optional[Dict[str, Any]]:
        if isinstance(group_id, str):
            group_id = uuid.UUID(group_id)
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM case_enforcements WHERE group_id = $1", group_id
            )
            return dict(row) if row else None

    async def mark_enforcement_notified(self, group_id: Union[str, uuid.UUID]) -> None:
        """Record that the subject received the grouped notice DM."""
        if isinstance(group_id, str):
            group_id = uuid.UUID(group_id)
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE case_enforcements SET notified = TRUE, updated_at = now() "
                "WHERE group_id = $1",
                group_id,
            )

    async def halt_enforcement(
        self,
        group_id: Union[str, uuid.UUID],
        *,
        by_id: Optional[Union[str, int]] = None,
        reason: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Stop a running countdown (an appeal was filed).

        Only a ``pending`` countdown can be halted — returns ``None`` otherwise,
        so the caller can tell "already halted / already executed" apart from
        "halted just now".
        """
        if isinstance(group_id, str):
            group_id = uuid.UUID(group_id)
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE case_enforcements
                SET status = 'halted', halted_by = $2, halted_reason = $3,
                    halted_at = now(), updated_at = now()
                WHERE group_id = $1 AND status = 'pending'
                RETURNING *
                """,
                group_id, str(by_id) if by_id is not None else None, reason,
            )
            return dict(row) if row else None

    async def resume_enforcement(
        self, group_id: Union[str, uuid.UUID], deadline: datetime
    ) -> Optional[Dict[str, Any]]:
        """Restart a halted countdown with a fresh deadline (appeal refused)."""
        if isinstance(group_id, str):
            group_id = uuid.UUID(group_id)
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE case_enforcements
                SET status = 'pending', deadline = $2, halted_by = NULL,
                    halted_reason = NULL, halted_at = NULL, updated_at = now()
                WHERE group_id = $1 AND status = 'halted'
                RETURNING *
                """,
                group_id, deadline,
            )
            return dict(row) if row else None

    async def cancel_enforcement(
        self, group_id: Union[str, uuid.UUID]
    ) -> Optional[Dict[str, Any]]:
        """Drop a countdown that no longer applies (the sanction was lifted)."""
        if isinstance(group_id, str):
            group_id = uuid.UUID(group_id)
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE case_enforcements
                SET status = 'cancelled', updated_at = now()
                WHERE group_id = $1 AND status IN ('pending','halted')
                RETURNING *
                """,
                group_id,
            )
            return dict(row) if row else None

    async def claim_due_enforcements(self, limit: int = 25) -> List[Dict[str, Any]]:
        """Atomically take the countdowns whose deadline has passed.

        The rows are flipped to ``executed`` in the same statement that returns
        them, so a second sweeper (or a restart mid-sweep) can never execute the
        same group twice.
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                UPDATE case_enforcements
                SET status = 'executed', executed_at = now(), updated_at = now()
                WHERE group_id IN (
                    SELECT group_id FROM case_enforcements
                    WHERE status = 'pending' AND deadline <= now()
                    ORDER BY deadline
                    LIMIT $1
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING *
                """,
                limit,
            )
        if rows:
            logger.info("[Enforcement] Claimed %d due countdown(s)", len(rows))
        return [dict(r) for r in rows]

    async def list_enforcements_by_status(
        self, status: str = "pending", limit: int = 25
    ) -> List[Dict[str, Any]]:
        """Countdowns in a given state, soonest deadline first.

        Feeds ``/mod global pending`` — the queue of subjects whose grace
        period is still running and who may still appeal.
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM case_enforcements
                WHERE status = $1
                ORDER BY deadline
                LIMIT $2
                """,
                status, limit,
            )
        return [dict(r) for r in rows]

    async def list_subject_enforcements(
        self,
        subject_type: str,
        subject_id: Union[str, int],
        *,
        statuses: Optional[List[str]] = None,
        limit: int = 25,
    ) -> List[Dict[str, Any]]:
        """Countdowns of a subject, most recent first."""
        async with self.pool.acquire() as conn:
            query = """
                SELECT * FROM case_enforcements
                WHERE subject_type = $1::subject_type AND subject_id = $2
            """
            params: List[Any] = [subject_type, str(subject_id)]
            if statuses:
                query += f" AND status = ANY(${len(params) + 1}::text[])"
                params.append(statuses)
            query += f" ORDER BY created_at DESC LIMIT ${len(params) + 1}"
            params.append(limit)
            rows = await conn.fetch(query, *params)
        return [dict(r) for r in rows]
