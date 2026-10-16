"""Automod moderator approvals — sanctions waiting for a human "go".

An infraction can be configured with ``require_approval``: the automod then
posts a card with Approve / Reject buttons in the alert channel instead of
sanctioning on its own. This table is the durable half of that card: one row
per pending decision, holding everything needed to apply (or discard) the
sanction later, so the buttons keep working across restarts and the message
that triggered it (already deleted) is not needed any more.

Lifecycle: ``pending`` -> ``approved`` | ``rejected`` | ``expired``. The status
change is a single guarded ``UPDATE ... WHERE status = 'pending'`` so two
moderators clicking at once cannot both win (:meth:`claim_approval`).

See docs/features/AUTOMOD.md ("Moderator approval").
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger("moddy.database")

APPROVAL_STATUSES = ("pending", "approved", "rejected", "expired")


def _row(row) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    data = dict(row)
    for key in ("payload", "result"):
        if isinstance(data.get(key), str):
            try:
                data[key] = json.loads(data[key])
            except ValueError:
                data[key] = {}
    return data


class AutomodApprovalRepository:
    """CRUD for ``automod_pending_approvals``."""

    async def create_automod_approval(self, *, guild_id: int, user_id: int,
                                      payload: Dict[str, Any],
                                      expires_at: datetime) -> Optional[int]:
        """Insert a pending approval; returns its id (``None`` on failure)."""
        try:
            async with self.pool.acquire() as conn:
                return await conn.fetchval(
                    """
                    INSERT INTO automod_pending_approvals
                        (guild_id, user_id, payload, expires_at)
                    VALUES ($1, $2, $3::jsonb, $4)
                    RETURNING id
                    """,
                    int(guild_id), int(user_id), json.dumps(payload), expires_at)
        except Exception as e:  # noqa: BLE001
            logger.error("automod approval insert failed: %s", e)
            return None

    async def set_automod_approval_card(self, approval_id: int, channel_id: int,
                                        message_id: int,
                                        payload: Optional[Dict[str, Any]] = None) -> None:
        """Remember where the card was posted (and, optionally, its attachments)."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE automod_pending_approvals
                SET channel_id = $2, card_message_id = $3,
                    payload = COALESCE($4::jsonb, payload)
                WHERE id = $1
                """,
                int(approval_id), int(channel_id), int(message_id),
                json.dumps(payload) if payload is not None else None)

    async def get_automod_approval(self, approval_id: int) -> Optional[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM automod_pending_approvals WHERE id = $1", int(approval_id))
        return _row(row)

    async def claim_automod_approval(self, approval_id: int, status: str,
                                     resolved_by: Optional[int]) -> Optional[Dict[str, Any]]:
        """Atomically move a ``pending`` approval to ``status``.

        Returns the updated row, or ``None`` when it was not pending any more
        (already resolved by someone else) — the caller must then do nothing.
        """
        if status not in ("approved", "rejected", "expired"):
            return None
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE automod_pending_approvals
                SET status = $2, resolved_by = $3, resolved_at = now()
                WHERE id = $1 AND status = 'pending'
                RETURNING *
                """,
                int(approval_id), status, int(resolved_by) if resolved_by else None)
        return _row(row)

    async def set_automod_approval_result(self, approval_id: int,
                                          result: Dict[str, Any]) -> None:
        """Store the outcome (applied / failed actions, case) of a resolved approval."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE automod_pending_approvals SET result = $2::jsonb WHERE id = $1",
                int(approval_id), json.dumps(result))

    async def list_expired_automod_approvals(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Pending approvals whose deadline has passed (for the housekeeping sweep)."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM automod_pending_approvals
                WHERE status = 'pending' AND expires_at < now()
                ORDER BY expires_at LIMIT $1
                """, int(limit))
        return [_row(r) for r in rows]

    async def purge_automod_approvals(self, older_than_days: int = 30) -> int:
        """Delete resolved approvals older than ``older_than_days``."""
        async with self.pool.acquire() as conn:
            res = await conn.execute(
                """
                DELETE FROM automod_pending_approvals
                WHERE status <> 'pending'
                  AND COALESCE(resolved_at, created_at) < now() - make_interval(days => $1)
                """, int(older_than_days))
        try:
            return int(res.split()[-1])
        except (ValueError, IndexError):
            return 0
