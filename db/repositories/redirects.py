"""
Redirect links repository.
Manages short/vanity redirect links (domain + path → target URL).
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger('moddy.database.redirects')


class RedirectRepository:
    """CRUD for redirect_links table."""

    async def add_redirect(
        self,
        domain: str,
        path: str,
        target: str,
        description: str,
        added_by: int,
    ) -> Dict[str, Any]:
        if not path.startswith('/'):
            path = '/' + path
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO redirect_links (domain, path, target, description, added_by, added_at)
                VALUES ($1, $2, $3, $4, $5, NOW())
                RETURNING id, domain, path, target, description, added_by, added_at
                """,
                domain, path, target, description, added_by,
            )
        return dict(row)

    async def list_redirects(self) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, domain, path, target, description, added_by, added_at FROM redirect_links ORDER BY added_at DESC"
            )
        return [dict(r) for r in rows]

    async def get_redirect(self, redirect_id: int) -> Optional[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, domain, path, target, description, added_by, added_at FROM redirect_links WHERE id = $1",
                redirect_id,
            )
        return dict(row) if row else None

    async def update_redirect(
        self,
        redirect_id: int,
        domain: str,
        path: str,
        target: str,
        description: str,
    ) -> Optional[Dict[str, Any]]:
        if not path.startswith('/'):
            path = '/' + path
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE redirect_links
                SET domain = $1, path = $2, target = $3, description = $4
                WHERE id = $5
                RETURNING id, domain, path, target, description, added_by, added_at
                """,
                domain, path, target, description, redirect_id,
            )
        return dict(row) if row else None

    async def delete_redirect(self, redirect_id: int) -> bool:
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM redirect_links WHERE id = $1", redirect_id
            )
        return result == "DELETE 1"
