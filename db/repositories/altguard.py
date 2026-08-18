"""
AltGuard repository — verification verdicts and per-guild member state.

Two tables (created in ``db/base.py``):

- ``altguard_verifications`` — the immutable audit trail. One row per verdict
  published by the AltGuard service on ``altguard:verdict``. The primary key is
  the service's ``verification_id``, which makes verdict handling idempotent
  (a Pub/Sub message replayed after a reconnect inserts nothing) and gives
  Moddy staff a lookup key for ``/mod altguard refusal``.
- ``altguard_members`` — the state the bot acts on: is this member allowed
  through the gate in this guild, and who decided.

Neither table holds personal data beyond Discord ids (already public on the
server): AltGuard never sends the bot a fingerprint, an email or an IP.
See docs/ALTGUARD.md.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger('moddy.database')

# Verdicts published by the service.
VERDICT_PASSED = "passed"
VERDICT_FLAGGED = "flagged"
VERDICT_BLOCKED = "blocked"
VERDICTS = (VERDICT_PASSED, VERDICT_FLAGGED, VERDICT_BLOCKED)

# Member states the bot keeps locally.
STATUS_PENDING = "pending"
STATUS_VERIFIED = "verified"
STATUS_FLAGGED = "flagged"
STATUS_BLOCKED = "blocked"
STATUSES = (STATUS_PENDING, STATUS_VERIFIED, STATUS_FLAGGED, STATUS_BLOCKED)

# Who decided a member state.
SOURCE_SERVICE = "service"
SOURCE_SERVER_STAFF = "server_staff"
SOURCE_MODDY_STAFF = "moddy_staff"

# A verdict maps 1:1 onto a member state.
VERDICT_TO_STATUS = {
    VERDICT_PASSED: STATUS_VERIFIED,
    VERDICT_FLAGGED: STATUS_FLAGGED,
    VERDICT_BLOCKED: STATUS_BLOCKED,
}


def _parse_reasons(value: Any) -> List[str]:
    """JSONB may come back as a list or as a JSON string (see DATABASE.md)."""
    if not value:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return []
        return [str(v) for v in parsed] if isinstance(parsed, list) else []
    return []


class AltGuardRepository:
    """CRUD for ``altguard_verifications`` and ``altguard_members``."""

    # ------------------------------------------------------------------ #
    # Verifications (audit trail)
    # ------------------------------------------------------------------ #

    async def record_altguard_verification(
        self,
        *,
        verification_id: str,
        guild_id: Union[str, int],
        user_id: Union[str, int],
        verdict: str,
        score: Optional[int] = None,
        reasons: Optional[List[str]] = None,
        enforced: bool = True,
    ) -> bool:
        """Insert a verdict. Returns ``False`` when it was already recorded.

        The ``False`` return is the idempotency signal the verdict handler
        relies on: a duplicate Pub/Sub delivery must not re-apply roles, re-log
        or re-DM anyone.
        """
        if verdict not in VERDICTS:
            raise ValueError(f"Unknown AltGuard verdict: {verdict!r}")

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO altguard_verifications (
                    id, guild_id, user_id, verdict, score, reasons, enforced
                ) VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
                ON CONFLICT (id) DO NOTHING
                RETURNING id
                """,
                str(verification_id), int(guild_id), int(user_id), verdict,
                int(score) if score is not None else None,
                json.dumps(list(reasons or [])), bool(enforced),
            )
            return row is not None

    async def get_altguard_verification(self, verification_id: str) -> Optional[Dict[str, Any]]:
        """One verdict by its id (the public 'refusal id')."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM altguard_verifications WHERE id = $1",
                str(verification_id),
            )
            if not row:
                return None
            data = dict(row)
            data['reasons'] = _parse_reasons(data.get('reasons'))
            return data

    async def list_altguard_verifications(
        self, guild_id: Union[str, int], user_id: Union[str, int], limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """A member's verification history in one guild, most recent first."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM altguard_verifications
                WHERE guild_id = $1 AND user_id = $2
                ORDER BY created_at DESC
                LIMIT $3
                """,
                int(guild_id), int(user_id), int(limit),
            )
            out = []
            for row in rows:
                data = dict(row)
                data['reasons'] = _parse_reasons(data.get('reasons'))
                out.append(data)
            return out

    # ------------------------------------------------------------------ #
    # Member state
    # ------------------------------------------------------------------ #

    async def get_altguard_member(
        self, guild_id: Union[str, int], user_id: Union[str, int],
    ) -> Optional[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM altguard_members WHERE guild_id = $1 AND user_id = $2",
                int(guild_id), int(user_id),
            )
            return dict(row) if row else None

    async def is_altguard_verified(
        self, guild_id: Union[str, int], user_id: Union[str, int],
    ) -> bool:
        """True when this member passed the gate in this guild."""
        async with self.pool.acquire() as conn:
            status = await conn.fetchval(
                "SELECT status FROM altguard_members WHERE guild_id = $1 AND user_id = $2",
                int(guild_id), int(user_id),
            )
            return status == STATUS_VERIFIED

    async def set_altguard_member_status(
        self,
        guild_id: Union[str, int],
        user_id: Union[str, int],
        status: str,
        *,
        source: str = SOURCE_SERVICE,
        verification_id: Optional[str] = None,
        decided_by: Optional[Union[str, int]] = None,
    ) -> Dict[str, Any]:
        """Upsert a member's gate state and return the stored row."""
        if status not in STATUSES:
            raise ValueError(f"Unknown AltGuard status: {status!r}")

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO altguard_members (
                    guild_id, user_id, status, source, verification_id, decided_by
                ) VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (guild_id, user_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    source = EXCLUDED.source,
                    verification_id = COALESCE(EXCLUDED.verification_id,
                                               altguard_members.verification_id),
                    decided_by = EXCLUDED.decided_by,
                    updated_at = now()
                RETURNING *
                """,
                int(guild_id), int(user_id), status, source,
                str(verification_id) if verification_id else None,
                int(decided_by) if decided_by is not None else None,
            )
            return dict(row)

    async def count_altguard_members(
        self, guild_id: Union[str, int],
    ) -> Dict[str, int]:
        """Member count per status for one guild (used by the config panel)."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT status, COUNT(*) AS total FROM altguard_members "
                "WHERE guild_id = $1 GROUP BY status",
                int(guild_id),
            )
            return {row['status']: int(row['total']) for row in rows}
