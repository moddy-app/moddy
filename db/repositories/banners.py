"""
Banners repository.
Manages dynamic information banners displayed on web surfaces.
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger('moddy.database.banners')

VALID_TYPES = ('announcement', 'incident', 'maintenance', 'information', 'warning', 'resolved')


class BannerRepository:
    """CRUD for banners table."""

    async def create_banner(
        self,
        message: str,
        banner_type: Optional[str],
        icon_svg: Optional[str],
        color: Optional[str],
        show_dashboard: bool = True,
        show_website: bool = True,
    ) -> Dict[str, Any]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO banners
                    (message, type, icon_svg, color, show_dashboard, show_website, is_active, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, FALSE, NOW(), NOW())
                RETURNING id, message, type, icon_svg, color, show_dashboard, show_website, is_active, created_at, updated_at
                """,
                message, banner_type, icon_svg, color, show_dashboard, show_website,
            )
        return dict(row)

    async def list_banners(self) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, message, type, icon_svg, color, show_dashboard, show_website, is_active, created_at, updated_at FROM banners ORDER BY created_at DESC"
            )
        return [dict(r) for r in rows]

    async def get_banner(self, banner_id: int) -> Optional[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, message, type, icon_svg, color, show_dashboard, show_website, is_active, created_at, updated_at FROM banners WHERE id = $1",
                banner_id,
            )
        return dict(row) if row else None

    async def activate_banner(self, banner_id: int) -> bool:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("UPDATE banners SET is_active = FALSE, updated_at = NOW() WHERE is_active = TRUE")
                result = await conn.execute(
                    "UPDATE banners SET is_active = TRUE, updated_at = NOW() WHERE id = $1",
                    banner_id,
                )
        return result == "UPDATE 1"

    async def deactivate_banner(self) -> int:
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE banners SET is_active = FALSE, updated_at = NOW() WHERE is_active = TRUE"
            )
        try:
            return int(result.split()[-1])
        except Exception:
            return 0

    async def update_banner(
        self,
        banner_id: int,
        message: str,
        banner_type: Optional[str],
        icon_svg: Optional[str],
        color: Optional[str],
        show_dashboard: bool,
        show_website: bool,
    ) -> Optional[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE banners
                SET message = $2, type = $3, icon_svg = $4, color = $5,
                    show_dashboard = $6, show_website = $7, updated_at = NOW()
                WHERE id = $1
                RETURNING id, message, type, icon_svg, color, show_dashboard, show_website, is_active, created_at, updated_at
                """,
                banner_id, message, banner_type, icon_svg, color, show_dashboard, show_website,
            )
        return dict(row) if row else None

    async def delete_banner(self, banner_id: int) -> bool:
        async with self.pool.acquire() as conn:
            result = await conn.execute("DELETE FROM banners WHERE id = $1", banner_id)
        return result == "DELETE 1"

    async def get_active_banner(self) -> Optional[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, message, type, icon_svg, color, show_dashboard, show_website, is_active, created_at, updated_at FROM banners WHERE is_active = TRUE LIMIT 1"
            )
        return dict(row) if row else None
