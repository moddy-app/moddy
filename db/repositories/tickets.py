"""
Tickets repository — the live state of every ticket channel.

The *configuration* of the Tickets module (panels, categories, permissions)
lives in ``guilds.data.modules.tickets`` like every other module. This table
holds the opposite half: one row per ticket **channel** that exists in Discord,
which is what every runtime action needs to resolve.

``channel_id`` is UNIQUE, and it is the only lookup key the runtime uses: a
ticket action always happens *inside* the ticket channel, so the channel id is
the ticket's identity. That is also why the ticket control buttons can use
static custom_ids (see ``utils/ticket_views.py``).

``number`` is a per-guild counter (``UNIQUE (guild_id, number)``) used in
channel names and references — humans say "ticket 42", not a snowflake.

See docs/TICKETS.md.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger('moddy.database')

STATUS_OPEN = "open"
STATUS_CLOSED = "closed"
STATUSES = (STATUS_OPEN, STATUS_CLOSED)

# How many times ``create_ticket`` retries when two members open a ticket at the
# exact same moment and land on the same ``number``.
_NUMBER_RETRIES = 5


def _row_to_dict(row) -> Dict[str, Any]:
    """Convert an asyncpg Record into a plain ticket dict."""
    return {
        "id": row["id"],
        "guild_id": row["guild_id"],
        "channel_id": row["channel_id"],
        "panel_id": row["panel_id"],
        "category_id": row["category_id"],
        "number": row["number"],
        "owner_id": row["owner_id"],
        "status": row["status"],
        "escalated": row["escalated"],
        "staff_thread_id": row["staff_thread_id"],
        "participants": list(row["participants"] or []),
        "participant_roles": list(row["participant_roles"] or []),
        "close_requested_by": row["close_requested_by"],
        "close_request_reason": row["close_request_reason"],
        "opened_at": row["opened_at"],
        "closed_at": row["closed_at"],
        "closed_by": row["closed_by"],
        "close_reason": row["close_reason"],
    }


class TicketsRepository:
    """CRUD for the ``tickets`` table."""

    # ------------------------------------------------------------------ #
    # Creation
    # ------------------------------------------------------------------ #

    async def next_ticket_number(self, guild_id: int) -> int:
        """The number the next ticket of this guild would get.

        Read *before* the channel is created so it can go straight into the
        channel name: renaming a channel afterwards would spend one of the two
        renames Discord allows per channel per 10 minutes, and a staffer's
        ``/ticket rename`` right after opening would then hang.

        It is a prediction, not a reservation — see ``create_ticket``.
        """
        async with self.pool.acquire() as conn:
            return (await conn.fetchval(
                "SELECT COALESCE(MAX(number), 0) + 1 FROM tickets WHERE guild_id = $1",
                guild_id) or 1)

    async def create_ticket(
        self,
        *,
        guild_id: int,
        channel_id: int,
        panel_id: str,
        category_id: str,
        owner_id: int,
        number: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Insert a ticket row, allocating the next per-guild number.

        ``number`` is the value ``next_ticket_number`` predicted, so the channel
        could already be named with it. Two simultaneous opens can still collide
        on ``UNIQUE (guild_id, number)``; the insert then falls back to a fresh
        ``MAX(number) + 1`` and retries, which is cheaper and simpler than
        holding an advisory lock for what is a rare race. The channel name is
        left as it is in that case — a ticket named one off from its number
        beats a rename that may not go through for ten minutes.
        """
        async with self.pool.acquire() as conn:
            for attempt in range(_NUMBER_RETRIES):
                try:
                    if number is not None and attempt == 0:
                        row = await conn.fetchrow("""
                            INSERT INTO tickets (
                                guild_id, channel_id, panel_id, category_id,
                                number, owner_id
                            )
                            VALUES ($1, $2, $3, $4, $5, $6)
                            RETURNING *
                        """, guild_id, channel_id, panel_id, category_id,
                             number, owner_id)
                    else:
                        row = await conn.fetchrow("""
                            INSERT INTO tickets (
                                guild_id, channel_id, panel_id, category_id,
                                number, owner_id
                            )
                            VALUES (
                                $1, $2, $3, $4,
                                (SELECT COALESCE(MAX(number), 0) + 1
                                   FROM tickets WHERE guild_id = $1),
                                $5
                            )
                            RETURNING *
                        """, guild_id, channel_id, panel_id, category_id, owner_id)
                    return _row_to_dict(row) if row else None
                except Exception as e:  # asyncpg.UniqueViolationError and friends
                    if 'tickets_guild_number' in str(e) or 'unique' in str(e).lower():
                        continue
                    logger.error(f"[Tickets] Could not create ticket: {e}")
                    return None
        logger.error(f"[Tickets] Gave up allocating a number for guild {guild_id}")
        return None

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #

    async def get_ticket_by_channel(self, channel_id: int) -> Optional[Dict[str, Any]]:
        """The ticket owning this channel, whatever its status."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM tickets WHERE channel_id = $1", channel_id)
            return _row_to_dict(row) if row else None

    async def get_ticket_by_number(self, guild_id: int, number: int) -> Optional[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM tickets WHERE guild_id = $1 AND number = $2",
                guild_id, number)
            return _row_to_dict(row) if row else None

    async def count_open_tickets(self, guild_id: int, owner_id: int,
                                 category_id: Optional[str] = None) -> int:
        """Open tickets a member owns — the per-category anti-spam limit."""
        async with self.pool.acquire() as conn:
            if category_id is None:
                return await conn.fetchval("""
                    SELECT COUNT(*) FROM tickets
                    WHERE guild_id = $1 AND owner_id = $2 AND status = 'open'
                """, guild_id, owner_id) or 0
            return await conn.fetchval("""
                SELECT COUNT(*) FROM tickets
                WHERE guild_id = $1 AND owner_id = $2 AND category_id = $3
                  AND status = 'open'
            """, guild_id, owner_id, category_id) or 0

    async def list_guild_tickets(self, guild_id: int, *, status: Optional[str] = None,
                                 limit: int = 50) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            if status:
                rows = await conn.fetch("""
                    SELECT * FROM tickets
                    WHERE guild_id = $1 AND status = $2
                    ORDER BY number DESC LIMIT $3
                """, guild_id, status, limit)
            else:
                rows = await conn.fetch("""
                    SELECT * FROM tickets WHERE guild_id = $1
                    ORDER BY number DESC LIMIT $2
                """, guild_id, limit)
            return [_row_to_dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Updates
    # ------------------------------------------------------------------ #

    async def set_ticket_status(self, channel_id: int, status: str, *,
                                actor_id: Optional[int] = None,
                                reason: Optional[str] = None) -> None:
        """Close or reopen a ticket. Reopening clears the closure metadata."""
        if status not in STATUSES:
            raise ValueError(f"unknown ticket status: {status}")
        async with self.pool.acquire() as conn:
            if status == STATUS_CLOSED:
                await conn.execute("""
                    UPDATE tickets
                    SET status = 'closed', closed_at = now(), closed_by = $2,
                        close_reason = $3, close_requested_by = NULL,
                        close_request_reason = NULL
                    WHERE channel_id = $1
                """, channel_id, actor_id, reason)
            else:
                await conn.execute("""
                    UPDATE tickets
                    SET status = 'open', closed_at = NULL, closed_by = NULL,
                        close_reason = NULL
                    WHERE channel_id = $1
                """, channel_id)

    async def set_close_request(self, channel_id: int, requester_id: Optional[int],
                                reason: Optional[str]) -> None:
        """Record (or clear, with ``requester_id=None``) a pending close request."""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE tickets
                SET close_requested_by = $2, close_request_reason = $3
                WHERE channel_id = $1
            """, channel_id, requester_id, reason)

    async def set_ticket_category(self, channel_id: int, panel_id: str,
                                  category_id: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE tickets SET panel_id = $2, category_id = $3
                WHERE channel_id = $1
            """, channel_id, panel_id, category_id)

    async def set_escalated(self, channel_id: int, escalated: bool) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE tickets SET escalated = $2 WHERE channel_id = $1",
                channel_id, escalated)

    async def set_staff_thread(self, channel_id: int,
                               thread_id: Optional[int]) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE tickets SET staff_thread_id = $2 WHERE channel_id = $1",
                channel_id, thread_id)

    async def set_participants(self, channel_id: int, *,
                               users: Optional[Sequence[int]] = None,
                               roles: Optional[Sequence[int]] = None) -> None:
        """Replace the manually added users and/or roles of a ticket."""
        async with self.pool.acquire() as conn:
            if users is not None and roles is not None:
                await conn.execute("""
                    UPDATE tickets SET participants = $2, participant_roles = $3
                    WHERE channel_id = $1
                """, channel_id, list(users), list(roles))
            elif users is not None:
                await conn.execute(
                    "UPDATE tickets SET participants = $2 WHERE channel_id = $1",
                    channel_id, list(users))
            elif roles is not None:
                await conn.execute(
                    "UPDATE tickets SET participant_roles = $2 WHERE channel_id = $1",
                    channel_id, list(roles))

    # ------------------------------------------------------------------ #
    # Deletion
    # ------------------------------------------------------------------ #

    async def delete_ticket(self, channel_id: int) -> None:
        """Forget a ticket — called when its channel is deleted in Discord."""
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM tickets WHERE channel_id = $1", channel_id)
