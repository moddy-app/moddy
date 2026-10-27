"""Bump prospects — servers the team may pitch Moddy's Bump Reminder to.

One row per server, keyed by ``guild_id``, created the first time a scan sees a
bump directory there with no reminder at all. The key is the whole dedup: a
server rescanned a hundred times still gets one card. A row is never deleted,
so a server the team closed stays closed even if the next scan agrees with the
first one.

Lifecycle::

    open ──claim──▶ claimed ──close──▶ closed ──reopen──▶ open / claimed
      └─────────────────close─────────────▲
    open / claimed ──(module enabled)──▶ converted
    open / claimed ──(Moddy removed)───▶ left

``converted`` and ``left`` are set by the bot, never by a click.

See docs/features/BUMP_PROSPECTS.md.
"""

import logging
from typing import Any, Dict, List, Optional, Sequence, Set

logger = logging.getLogger('moddy.database')

STATUS_OPEN = "open"
STATUS_CLAIMED = "claimed"
STATUS_CLOSED = "closed"
STATUS_CONVERTED = "converted"
STATUS_LEFT = "left"
ACTIVE_STATUSES = (STATUS_OPEN, STATUS_CLAIMED)


def _row_to_dict(row) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return {
        "guild_id": row["guild_id"],
        "guild_name": row["guild_name"],
        "directories": list(row["directories"] or []),
        "status": row["status"],
        "claimed_by": row["claimed_by"],
        "claimed_at": row["claimed_at"],
        "closed_by": row["closed_by"],
        "closed_at": row["closed_at"],
        "channel_id": row["channel_id"],
        "message_id": row["message_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


class BumpProspectRepository:
    """CRUD for ``bump_prospects``. Every transition is one guarded UPDATE."""

    async def get_bump_prospect(self, guild_id: int) -> Optional[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM bump_prospects WHERE guild_id = $1", guild_id)
        return _row_to_dict(row)

    async def known_bump_prospect_guilds(self) -> Set[int]:
        """Every server that already has a row, whatever its status."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT guild_id FROM bump_prospects")
        return {row["guild_id"] for row in rows}

    async def list_active_bump_prospects(self) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM bump_prospects WHERE status = ANY($1::text[])",
                list(ACTIVE_STATUSES))
        return [_row_to_dict(row) for row in rows]

    async def create_bump_prospect(self, guild_id: int, guild_name: str,
                                   directories: Sequence[str]) -> Optional[Dict[str, Any]]:
        """Insert the row, or ``None`` when the server already has one."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO bump_prospects (guild_id, guild_name, directories)
                VALUES ($1, $2, $3)
                ON CONFLICT (guild_id) DO NOTHING
                RETURNING *
            """, guild_id, guild_name, list(directories))
        return _row_to_dict(row)

    async def set_bump_prospect_card(self, guild_id: int, channel_id: int,
                                     message_id: int) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE bump_prospects
                SET channel_id = $2, message_id = $3, updated_at = now()
                WHERE guild_id = $1
            """, guild_id, channel_id, message_id)

    async def claim_bump_prospect(self, guild_id: int,
                                  staff_id: int) -> Optional[Dict[str, Any]]:
        """``open`` → ``claimed``; ``None`` when someone was faster."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                UPDATE bump_prospects
                SET status = 'claimed', claimed_by = $2, claimed_at = now(),
                    updated_at = now()
                WHERE guild_id = $1 AND status = 'open'
                RETURNING *
            """, guild_id, staff_id)
        return _row_to_dict(row)

    async def unclaim_bump_prospect(self, guild_id: int,
                                    staff_id: int) -> Optional[Dict[str, Any]]:
        """``claimed`` → ``open``, only for the staffer holding it."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                UPDATE bump_prospects
                SET status = 'open', claimed_by = NULL, claimed_at = NULL,
                    updated_at = now()
                WHERE guild_id = $1 AND status = 'claimed' AND claimed_by = $2
                RETURNING *
            """, guild_id, staff_id)
        return _row_to_dict(row)

    async def close_bump_prospect(self, guild_id: int,
                                  staff_id: int) -> Optional[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                UPDATE bump_prospects
                SET status = 'closed', closed_by = $2, closed_at = now(),
                    updated_at = now()
                WHERE guild_id = $1 AND status = ANY($3::text[])
                RETURNING *
            """, guild_id, staff_id, list(ACTIVE_STATUSES))
        return _row_to_dict(row)

    async def reopen_bump_prospect(self, guild_id: int) -> Optional[Dict[str, Any]]:
        """``closed`` → back where it was: ``claimed`` if someone held it."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                UPDATE bump_prospects
                SET status = CASE WHEN claimed_by IS NULL THEN 'open' ELSE 'claimed' END,
                    closed_by = NULL, closed_at = NULL, updated_at = now()
                WHERE guild_id = $1 AND status = 'closed'
                RETURNING *
            """, guild_id)
        return _row_to_dict(row)

    async def settle_bump_prospect(self, guild_id: int,
                                   status: str) -> Optional[Dict[str, Any]]:
        """Bot-side ending (``converted`` / ``left``) of a still-active row."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                UPDATE bump_prospects
                SET status = $2, updated_at = now()
                WHERE guild_id = $1 AND status = ANY($3::text[])
                RETURNING *
            """, guild_id, status, list(ACTIVE_STATUSES))
        return _row_to_dict(row)
