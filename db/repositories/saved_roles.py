import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger('moddy.database')


class SavedRolesRepository:
    """Saved roles (auto restore roles module) database operations"""

    async def save_user_roles(
        self,
        guild_id: int,
        user_id: int,
        roles: List[int],
        username: str
    ) -> bool:
        """
        Save user roles when they leave the server

        Args:
            guild_id: Guild ID
            user_id: User ID
            roles: List of role IDs to save
            username: Username for logging

        Returns:
            True if successful, False otherwise
        """
        try:
            async with self.pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO saved_roles (guild_id, user_id, roles, username, saved_at)
                    VALUES ($1, $2, $3, $4, NOW())
                    ON CONFLICT (guild_id, user_id)
                    DO UPDATE SET
                        roles = EXCLUDED.roles,
                        username = EXCLUDED.username,
                        saved_at = NOW()
                """, guild_id, user_id, roles, username)

                logger.info(f"[OK] Saved {len(roles)} roles for user {user_id} in guild {guild_id}")
                return True

        except Exception as e:
            logger.error(f"[ERROR] Error saving user roles: {e}", exc_info=True)
            return False

    async def get_saved_roles(self, guild_id: int, user_id: int) -> Optional[Dict[str, Any]]:
        """
        Get saved roles for a specific user in a guild

        Args:
            guild_id: Guild ID
            user_id: User ID

        Returns:
            Dict with roles, username, and saved_at, or None if not found
        """
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT roles, username, saved_at
                    FROM saved_roles
                    WHERE guild_id = $1 AND user_id = $2
                """, guild_id, user_id)

                if row:
                    return {
                        'roles': list(row['roles']),
                        'username': row['username'],
                        'saved_at': row['saved_at'].isoformat() if row['saved_at'] else None
                    }
                return None

        except Exception as e:
            logger.error(f"[ERROR] Error getting saved roles: {e}", exc_info=True)
            return None

    async def delete_saved_roles(self, guild_id: int, user_id: int) -> bool:
        """
        Delete saved roles for a specific user in a guild

        Args:
            guild_id: Guild ID
            user_id: User ID

        Returns:
            True if deleted, False otherwise
        """
        try:
            async with self.pool.acquire() as conn:
                result = await conn.execute("""
                    DELETE FROM saved_roles
                    WHERE guild_id = $1 AND user_id = $2
                """, guild_id, user_id)

                # Check if row was deleted
                deleted = result.split()[-1] == '1'
                if deleted:
                    logger.info(f"[OK] Deleted saved roles for user {user_id} in guild {guild_id}")
                return deleted

        except Exception as e:
            logger.error(f"[ERROR] Error deleting saved roles: {e}", exc_info=True)
            return False

    async def get_all_saved_roles_for_guild(self, guild_id: int) -> List[Dict[str, Any]]:
        """
        Get all users with saved roles in a specific guild

        Args:
            guild_id: Guild ID

        Returns:
            List of dicts with user_id, roles, username, and saved_at
        """
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT user_id, roles, username, saved_at
                    FROM saved_roles
                    WHERE guild_id = $1
                    ORDER BY saved_at DESC
                """, guild_id)

                result = []
                for row in rows:
                    result.append({
                        'user_id': row['user_id'],
                        'roles': list(row['roles']),
                        'username': row['username'],
                        'saved_at': row['saved_at'].isoformat() if row['saved_at'] else None
                    })

                return result

        except Exception as e:
            logger.error(f"[ERROR] Error getting all saved roles for guild: {e}", exc_info=True)
            return []

    async def get_saved_roles_count(self, guild_id: int) -> int:
        """
        Get the count of users with saved roles in a specific guild

        Args:
            guild_id: Guild ID

        Returns:
            Number of users with saved roles
        """
        try:
            async with self.pool.acquire() as conn:
                count = await conn.fetchval("""
                    SELECT COUNT(*)
                    FROM saved_roles
                    WHERE guild_id = $1
                """, guild_id)

                return count or 0

        except Exception as e:
            logger.error(f"[ERROR] Error getting saved roles count: {e}", exc_info=True)
            return 0
