"""
Which Discord application is in which server (``app_guilds``), and the
ownership of a module config (the ``app`` key of ``guilds.data.modules.<id>``).

Shared by Moddy and the mini-bots process — see docs/features/MINIBOTS.md.
"""

import logging
from typing import Dict, Iterable, List, Optional, Set

from minibots.ownership import MODDY_APP

logger = logging.getLogger('moddy.database')


class AppGuildsRepository:
    """Presence of each application per guild + module ownership writes."""

    async def mark_app_present(self, app: str, guild_id: int,
                               member_count: Optional[int] = None) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO app_guilds (app, guild_id, present, member_count)
                VALUES ($1, $2, TRUE, $3)
                ON CONFLICT (app, guild_id) DO UPDATE
                SET present = TRUE, left_at = NULL,
                    joined_at = CASE WHEN app_guilds.present THEN app_guilds.joined_at
                                     ELSE now() END,
                    member_count = COALESCE(EXCLUDED.member_count, app_guilds.member_count)
            """, app, guild_id, member_count)

    async def mark_app_absent(self, app: str, guild_id: int) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE app_guilds SET present = FALSE, left_at = now()
                WHERE app = $1 AND guild_id = $2 AND present
            """, app, guild_id)

    async def reconcile_app_guilds(self, app: str, guild_ids: Iterable[int]) -> None:
        """Make the table match the guilds ``app`` is really in (boot).

        Two statements whatever the number of guilds: joins and leaves missed
        while the app was down are caught here.
        """
        ids: List[int] = sorted({int(g) for g in guild_ids})
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("""
                INSERT INTO app_guilds (app, guild_id, present)
                SELECT $1, g, TRUE FROM unnest($2::bigint[]) AS g
                ON CONFLICT (app, guild_id) DO UPDATE
                SET present = TRUE, left_at = NULL,
                    joined_at = CASE WHEN app_guilds.present THEN app_guilds.joined_at
                                     ELSE now() END
            """, app, ids)
            await conn.execute("""
                UPDATE app_guilds SET present = FALSE, left_at = now()
                WHERE app = $1 AND present AND NOT (guild_id = ANY($2::bigint[]))
            """, app, ids)

    async def apps_in_guild(self, guild_id: int) -> Set[str]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT app FROM app_guilds WHERE guild_id = $1 AND present", guild_id)
        return {row['app'] for row in rows}

    async def count_guilds_by_app(self) -> Dict[str, int]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT app, count(*) AS n FROM app_guilds WHERE present GROUP BY app")
        return {row['app']: row['n'] for row in rows}

    async def set_module_app(self, guild_id: int, module_id: str,
                             expected_app: str, new_app: str) -> bool:
        """Move a module config from ``expected_app`` to ``new_app``.

        One atomic compare-and-swap on the ``app`` key only (the config itself
        is untouched): False when the config is missing or no longer owned by
        ``expected_app`` — a second click, or a concurrent transfer.
        Moddy is the implicit owner, so moving to Moddy removes the key.
        """
        async with self.pool.acquire() as conn:
            if new_app == MODDY_APP:
                result = await conn.execute("""
                    UPDATE guilds
                    SET data = data #- ARRAY['modules', $2::text, 'app'],
                        updated_at = now()
                    WHERE guild_id = $1
                      AND jsonb_typeof(data->'modules'->$2::text) = 'object'
                      AND COALESCE(data->'modules'->$2::text->>'app', $4) = $3
                """, guild_id, module_id, expected_app, MODDY_APP)
            else:
                result = await conn.execute("""
                    UPDATE guilds
                    SET data = jsonb_set(data, ARRAY['modules', $2::text, 'app'],
                                         to_jsonb($4::text), true),
                        updated_at = now()
                    WHERE guild_id = $1
                      AND jsonb_typeof(data->'modules'->$2::text) = 'object'
                      AND COALESCE(data->'modules'->$2::text->>'app', $5) = $3
                """, guild_id, module_id, expected_app, new_app, MODDY_APP)
        return result.endswith(" 1")
