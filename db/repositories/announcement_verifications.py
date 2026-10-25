"""Authentication of the support server's announcements, and their relayed copies.

A Manager or Developer of the Moddy team authenticates an announcement from the
support server (one row in ``announcement_verifications``, keyed by the
announcement's message id). Every copy Discord relays into a following server
is recorded in ``announcement_crossposts`` the moment it arrives, so a copy that
lands *before* the announcement is authenticated is still answered once it is,
and no copy is ever answered twice.

See docs/features/ANNOUNCEMENT_VERIFICATION.md.
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger('moddy.database')


class AnnouncementVerificationRepository:
    """Announcement authentication (``announcement_verifications``, ``announcement_crossposts``)."""

    async def save_announcement_verification(
        self,
        message_id: int,
        *,
        guild_id: int,
        channel_id: int,
        content_hash: str,
        verified_by: int,
    ) -> None:
        """Mark one announcement authentic (upsert: a re-authentication replaces it)."""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO announcement_verifications (
                    message_id, guild_id, channel_id, content_hash, verified_by
                ) VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (message_id) DO UPDATE SET
                    content_hash = EXCLUDED.content_hash,
                    verified_by  = EXCLUDED.verified_by,
                    verified_at  = now()
            """, message_id, guild_id, channel_id, content_hash, verified_by)

    async def get_announcement_verification(
        self, message_id: int
    ) -> Optional[Dict[str, Any]]:
        """The authentication of one announcement, or ``None`` if it has none."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM announcement_verifications WHERE message_id = $1",
                message_id)
        return dict(row) if row else None

    async def delete_announcement_verification(self, message_id: int) -> bool:
        """Withdraw an authentication (the announcement was edited afterwards)."""
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM announcement_verifications WHERE message_id = $1",
                message_id)
        return result.endswith(" 1")

    async def record_announcement_crosspost(
        self,
        message_id: int,
        *,
        source_message_id: int,
        guild_id: int,
        channel_id: int,
    ) -> bool:
        """Remember one relayed copy. ``False`` when it was already known."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO announcement_crossposts (
                    message_id, source_message_id, guild_id, channel_id
                ) VALUES ($1, $2, $3, $4)
                ON CONFLICT (message_id) DO NOTHING
                RETURNING message_id
            """, message_id, source_message_id, guild_id, channel_id)
        return row is not None

    async def mark_announcement_crosspost_answered(self, message_id: int) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE announcement_crossposts SET answered_at = now() "
                "WHERE message_id = $1", message_id)

    async def get_unanswered_announcement_crossposts(
        self, source_message_id: int
    ) -> List[Dict[str, Any]]:
        """Copies of one announcement that have not been answered yet."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT message_id, guild_id, channel_id
                FROM announcement_crossposts
                WHERE source_message_id = $1 AND answered_at IS NULL
                ORDER BY created_at
            """, source_message_id)
        return [dict(r) for r in rows]
