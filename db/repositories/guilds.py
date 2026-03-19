import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List

logger = logging.getLogger('moddy.database')


class GuildRepository:
    """Guild management database operations"""

    async def get_guild(self, guild_id: int) -> Dict[str, Any]:
        """Récupère ou crée un serveur"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM guilds WHERE guild_id = $1",
                guild_id
            )

            if not row:
                # Crée le serveur s'il n'existe pas, gère la concurrence
                await conn.execute(
                    "INSERT INTO guilds (guild_id) VALUES ($1) ON CONFLICT (guild_id) DO NOTHING",
                    guild_id
                )
                # Re-fetch pour être sûr d'avoir les données
                row = await conn.fetchrow(
                    "SELECT * FROM guilds WHERE guild_id = $1",
                    guild_id
                )

            return {
                'guild_id': row['guild_id'],
                'attributes': self._parse_jsonb(row['attributes']),
                'data': self._parse_jsonb(row['data']),
                'created_at': row.get('created_at', datetime.now(timezone.utc)),
                'updated_at': row.get('updated_at', datetime.now(timezone.utc))
            }

    async def update_guild_data(self, guild_id: int, path: str, value: Any):
        """Met à jour une partie spécifique de la data serveur"""
        await self._update_entity_data('guilds', 'guild_id', guild_id, path, value)

    async def get_guilds_with_attribute(self, attribute: str, value: Any = None) -> List[int]:
        """Récupère tous les serveurs ayant un attribut spécifique"""
        async with self.pool.acquire() as conn:
            if value is None:
                rows = await conn.fetch("""
                    SELECT guild_id FROM guilds
                    WHERE attributes ? $1
                """, attribute)
            else:
                rows = await conn.fetch("""
                    SELECT guild_id FROM guilds
                    WHERE attributes @> $1
                """, json.dumps({attribute: value}))

            return [row['guild_id'] for row in rows]
