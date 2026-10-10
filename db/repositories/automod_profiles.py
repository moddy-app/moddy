"""Automod profile holds — members timed out until they fix their avatar / banner.

One table (``automod_profile_holds``), see docs/AUTOMOD_AI.md "Profiles". A row
exists while the Discord timeout opened by the ``hold`` profile action is being
kept in force: it is renewed by the profile sweep while the profile is still
flagged and deleted the moment the profile is fixed (or the team revokes the
sanction). Decisions live in ``modules/automod_ai.py``; this only reads/writes.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger("moddy.database")

HOLD_KINDS = ("avatar", "banner")


def _uuid(value: Union[str, uuid.UUID, None]) -> Optional[uuid.UUID]:
    if value is None:
        return None
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


def _hold(row) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return {
        "guild_id": row["guild_id"], "user_id": row["user_id"], "kind": row["kind"],
        "categorie": row["categorie"], "asset_key": row["asset_key"],
        "case_id": row["case_id"], "sanction_id": row["sanction_id"],
        "created_at": row["created_at"], "timeout_until": row["timeout_until"],
    }


class AutomodProfileRepository:
    """CRUD for ``automod_profile_holds``."""

    async def upsert_profile_hold(
        self, guild_id: int, user_id: int, kind: str, *,
        categorie: Optional[str] = None, asset_key: Optional[str] = None,
        case_id: Optional[Union[str, uuid.UUID]] = None,
        sanction_id: Optional[Union[str, uuid.UUID]] = None,
        timeout_until: Optional[datetime] = None,
    ) -> None:
        if kind not in HOLD_KINDS:
            raise ValueError(f"invalid profile hold kind: {kind}")
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO automod_profile_holds
                    (guild_id, user_id, kind, categorie, asset_key, case_id,
                     sanction_id, timeout_until)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (guild_id, user_id, kind) DO UPDATE SET
                    categorie = EXCLUDED.categorie,
                    asset_key = EXCLUDED.asset_key,
                    case_id = COALESCE(EXCLUDED.case_id, automod_profile_holds.case_id),
                    sanction_id = COALESCE(EXCLUDED.sanction_id,
                                           automod_profile_holds.sanction_id),
                    timeout_until = EXCLUDED.timeout_until
                """,
                int(guild_id), int(user_id), kind, categorie, asset_key,
                _uuid(case_id), _uuid(sanction_id), timeout_until,
            )

    async def get_profile_hold(self, guild_id: int, user_id: int,
                               kind: str) -> Optional[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM automod_profile_holds "
                "WHERE guild_id = $1 AND user_id = $2 AND kind = $3",
                int(guild_id), int(user_id), kind)
        return _hold(row)

    async def list_member_profile_holds(self, guild_id: int,
                                        user_id: int) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM automod_profile_holds "
                "WHERE guild_id = $1 AND user_id = $2 ORDER BY kind",
                int(guild_id), int(user_id))
        return [_hold(r) for r in rows]

    async def list_profile_holds_due(self, before: datetime,
                                     limit: int = 200) -> List[Dict[str, Any]]:
        """Holds whose timeout ends before ``before`` (sweep input)."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM automod_profile_holds "
                "WHERE timeout_until IS NULL OR timeout_until < $1 "
                "ORDER BY timeout_until NULLS FIRST LIMIT $2",
                before, int(limit))
        return [_hold(r) for r in rows]

    async def delete_profile_hold(self, guild_id: int, user_id: int,
                                  kind: str) -> bool:
        async with self.pool.acquire() as conn:
            res = await conn.execute(
                "DELETE FROM automod_profile_holds "
                "WHERE guild_id = $1 AND user_id = $2 AND kind = $3",
                int(guild_id), int(user_id), kind)
        return res.endswith(" 1")

    async def delete_member_profile_holds(self, guild_id: int, user_id: int) -> int:
        async with self.pool.acquire() as conn:
            res = await conn.execute(
                "DELETE FROM automod_profile_holds WHERE guild_id = $1 AND user_id = $2",
                int(guild_id), int(user_id))
        try:
            return int(res.rsplit(" ", 1)[-1])
        except ValueError:
            return 0

    async def delete_profile_hold_by_sanction(self, sanction_id: Union[str, uuid.UUID]) -> int:
        """Drop the hold(s) opened by this sanction (appeal accepted / team revoke)."""
        sid = _uuid(sanction_id)
        if sid is None:
            return 0
        async with self.pool.acquire() as conn:
            res = await conn.execute(
                "DELETE FROM automod_profile_holds WHERE sanction_id = $1", sid)
        try:
            return int(res.rsplit(" ", 1)[-1])
        except ValueError:
            return 0
