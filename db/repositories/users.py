import json
import copy
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List

from db.repositories._utils import set_nested_value

logger = logging.getLogger('moddy.database')


class UserRepository:
    """User management database operations"""

    async def get_user(self, user_id: int) -> Dict[str, Any]:
        """Récupère ou crée un utilisateur"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM users WHERE user_id = $1",
                user_id
            )

            if not row:
                # Crée l'utilisateur s'il n'existe pas, gère la concurrence
                await conn.execute(
                    "INSERT INTO users (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING",
                    user_id
                )
                # Re-fetch pour être sûr d'avoir les données
                row = await conn.fetchrow(
                    "SELECT * FROM users WHERE user_id = $1",
                    user_id
                )

            return {
                'user_id': row['user_id'],
                'attributes': self._parse_jsonb(row['attributes']),
                'data': self._parse_jsonb(row['data']),
                'created_at': row.get('created_at', datetime.now(timezone.utc)),
                'updated_at': row.get('updated_at', datetime.now(timezone.utc))
            }

    async def update_user_data(self, user_id: int, path: str, value: Any):
        """Met à jour une partie spécifique de la data utilisateur"""
        await self._update_entity_data('users', 'user_id', user_id, path, value)

    async def get_users_with_attribute(self, attribute: str, value: Any = None) -> List[int]:
        """Récupère tous les utilisateurs ayant un attribut spécifique

        Si value est None, cherche juste la présence de l'attribut
        Si value est fournie, cherche cette valeur spécifique
        """
        async with self.pool.acquire() as conn:
            if value is None:
                # Cherche juste la présence de l'attribut
                rows = await conn.fetch("""
                    SELECT user_id FROM users
                    WHERE attributes ? $1
                """, attribute)
            else:
                # Cherche une valeur spécifique
                rows = await conn.fetch("""
                    SELECT user_id FROM users
                    WHERE attributes @> $1
                """, json.dumps({attribute: value}))

            return [row['user_id'] for row in rows]
