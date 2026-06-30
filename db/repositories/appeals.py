"""
Appeals repository.

A ``case_appeals`` row records a sanctioned user's appeal of an automod
sanction. An appeal targets a single sanction inside a case and is routed either
to the **server** (the guild's moderators) or to the **Moddy team**. The row
tracks its lifecycle (pending → accepted / refused / transformed / cancelled)
and the Discord message ids used to render and update the user DM and the
reviewer panel.

The case timeline itself (``case_events``) keeps the human-readable trace; this
table is the queryable state machine (e.g. "all pending team appeals").
"""

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger('moddy.database')


class AppealRepository:
    """CRUD for sanction appeals (``case_appeals``)."""

    @staticmethod
    def _new_uuid() -> uuid.UUID:
        return uuid.uuid4()

    async def create_appeal(
        self,
        *,
        case_id: uuid.UUID,
        sanction_id: Optional[uuid.UUID],
        subject_id: Union[str, int],
        guild_id: Union[str, int],
        route: str,
        action: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a pending appeal and return the row."""
        appeal_id = self._new_uuid()
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO case_appeals (
                    id, case_id, sanction_id, subject_id, guild_id,
                    action, route, status, reason
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,'pending',$8)
                RETURNING *
                """,
                appeal_id, case_id,
                sanction_id,
                str(subject_id), str(guild_id),
                action, route, reason,
            )
            return dict(row)

    async def get_appeal(self, appeal_id: Union[str, uuid.UUID]) -> Optional[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM case_appeals WHERE id = $1", uuid.UUID(str(appeal_id)))
            return dict(row) if row else None

    async def get_active_for_sanction(
        self, sanction_id: Union[str, uuid.UUID],
    ) -> Optional[Dict[str, Any]]:
        """The most recent non-final appeal for a sanction (pending), or None."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM case_appeals
                WHERE sanction_id = $1 AND status = 'pending'
                ORDER BY created_at DESC LIMIT 1
                """,
                uuid.UUID(str(sanction_id)),
            )
            return dict(row) if row else None

    async def list_for_case(self, case_id: Union[str, uuid.UUID]) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM case_appeals WHERE case_id = $1 ORDER BY created_at ASC",
                uuid.UUID(str(case_id)),
            )
            return [dict(r) for r in rows]

    async def list_pending(self, route: Optional[str] = None) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            if route:
                rows = await conn.fetch(
                    "SELECT * FROM case_appeals WHERE status = 'pending' AND route = $1 ORDER BY created_at ASC",
                    route,
                )
            else:
                rows = await conn.fetch(
                    "SELECT * FROM case_appeals WHERE status = 'pending' ORDER BY created_at ASC"
                )
            return [dict(r) for r in rows]

    async def update_message_refs(
        self,
        appeal_id: Union[str, uuid.UUID],
        *,
        dm_channel_id: Optional[Union[str, int]] = None,
        dm_message_id: Optional[Union[str, int]] = None,
        review_channel_id: Optional[Union[str, int]] = None,
        review_message_id: Optional[Union[str, int]] = None,
    ) -> None:
        """Store the rendered Discord message ids (so they can be edited later)."""
        sets: List[str] = []
        params: List[Any] = []
        for col, val in (
            ("dm_channel_id", dm_channel_id),
            ("dm_message_id", dm_message_id),
            ("review_channel_id", review_channel_id),
            ("review_message_id", review_message_id),
        ):
            if val is not None:
                params.append(str(val))
                sets.append(f"{col} = ${len(params)}")
        if not sets:
            return
        params.append(uuid.UUID(str(appeal_id)))
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"UPDATE case_appeals SET {', '.join(sets)} WHERE id = ${len(params)}",
                *params,
            )

    async def set_claim(
        self,
        appeal_id: Union[str, uuid.UUID],
        *,
        claimed_by_id: Optional[Union[str, int]],
    ) -> Optional[Dict[str, Any]]:
        """Claim (or release, when ``claimed_by_id`` is None) a pending appeal.

        Claiming only succeeds while the appeal is still pending and either
        unclaimed or already held by the same reviewer. Returns the updated row
        or None when the claim could not be taken.
        """
        async with self.pool.acquire() as conn:
            if claimed_by_id is None:
                row = await conn.fetchrow(
                    """
                    UPDATE case_appeals
                    SET claimed_by_id = NULL, claimed_at = NULL
                    WHERE id = $1 AND status = 'pending'
                    RETURNING *
                    """,
                    uuid.UUID(str(appeal_id)),
                )
            else:
                row = await conn.fetchrow(
                    """
                    UPDATE case_appeals
                    SET claimed_by_id = $2, claimed_at = now()
                    WHERE id = $1 AND status = 'pending'
                      AND (claimed_by_id IS NULL OR claimed_by_id = $2)
                    RETURNING *
                    """,
                    uuid.UUID(str(appeal_id)), str(claimed_by_id),
                )
            return dict(row) if row else None

    async def set_decision(
        self,
        appeal_id: Union[str, uuid.UUID],
        *,
        status: str,
        decided_by_type: str,
        decided_by_id: Optional[Union[str, int]],
        decision_note: Optional[str] = None,
        new_action: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Finalize an appeal (only if still pending). Returns the updated row."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE case_appeals
                SET status = $2, decided_by_type = $3, decided_by_id = $4,
                    decision_note = $5, new_action = $6, decided_at = now()
                WHERE id = $1 AND status = 'pending'
                RETURNING *
                """,
                uuid.UUID(str(appeal_id)), status, decided_by_type,
                str(decided_by_id) if decided_by_id is not None else None,
                decision_note, new_action,
            )
            return dict(row) if row else None
