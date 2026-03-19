import logging
from datetime import datetime
from typing import Dict, Any, Optional, List

logger = logging.getLogger('moddy.database')


class ReminderRepository:
    """Reminder management database operations"""

    async def create_reminder(self, user_id: int, message: str, remind_at: datetime,
                              guild_id: int = None, channel_id: int = None,
                              send_in_channel: bool = False) -> int:
        """Crée un nouveau rappel et retourne son ID"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO reminders (user_id, guild_id, channel_id, message, remind_at, send_in_channel)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id
            """, user_id, guild_id, channel_id, message, remind_at, send_in_channel)
            return row['id']

    async def get_reminder(self, reminder_id: int) -> Optional[Dict[str, Any]]:
        """Récupère un rappel par son ID"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM reminders WHERE id = $1",
                reminder_id
            )
            if not row:
                return None
            return dict(row)

    async def get_user_reminders(self, user_id: int, include_sent: bool = False) -> List[Dict[str, Any]]:
        """Récupère tous les rappels d'un utilisateur"""
        async with self.pool.acquire() as conn:
            if include_sent:
                rows = await conn.fetch(
                    "SELECT * FROM reminders WHERE user_id = $1 ORDER BY remind_at ASC",
                    user_id
                )
            else:
                rows = await conn.fetch(
                    "SELECT * FROM reminders WHERE user_id = $1 AND sent = FALSE ORDER BY remind_at ASC",
                    user_id
                )
            return [dict(row) for row in rows]

    async def get_pending_reminders(self) -> List[Dict[str, Any]]:
        """Récupère tous les rappels non envoyés dont l'heure est passée"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM reminders
                WHERE sent = FALSE AND remind_at <= NOW()
                ORDER BY remind_at ASC
            """)
            return [dict(row) for row in rows]

    async def get_upcoming_reminders(self, limit_minutes: int = 5) -> List[Dict[str, Any]]:
        """Récupère les rappels à envoyer dans les prochaines minutes"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM reminders
                WHERE sent = FALSE AND remind_at <= NOW() + make_interval(mins => $1)
                ORDER BY remind_at ASC
            """, limit_minutes)
            return [dict(row) for row in rows]

    async def mark_reminder_sent(self, reminder_id: int, failed: bool = False):
        """Marque un rappel comme envoyé"""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE reminders
                SET sent = TRUE, sent_at = NOW(), failed = $2
                WHERE id = $1
            """, reminder_id, failed)

    async def delete_reminder(self, reminder_id: int, user_id: int) -> bool:
        """Supprime un rappel (vérifie que l'utilisateur est le propriétaire)"""
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM reminders WHERE id = $1 AND user_id = $2",
                reminder_id, user_id
            )
            return result == "DELETE 1"

    async def update_reminder(self, reminder_id: int, user_id: int,
                              message: str = None, remind_at: datetime = None) -> bool:
        """Met à jour un rappel"""
        async with self.pool.acquire() as conn:
            # Vérifie d'abord que le rappel appartient à l'utilisateur
            existing = await conn.fetchrow(
                "SELECT * FROM reminders WHERE id = $1 AND user_id = $2",
                reminder_id, user_id
            )
            if not existing:
                return False

            if message is not None:
                await conn.execute(
                    "UPDATE reminders SET message = $1 WHERE id = $2",
                    message, reminder_id
                )
            if remind_at is not None:
                await conn.execute(
                    "UPDATE reminders SET remind_at = $1 WHERE id = $2",
                    remind_at, reminder_id
                )
            return True

    async def get_user_past_reminders(self, user_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        """Récupère les rappels passés d'un utilisateur"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM reminders
                WHERE user_id = $1 AND sent = TRUE
                ORDER BY sent_at DESC
                LIMIT $2
            """, user_id, limit)
            return [dict(row) for row in rows]

    async def cleanup_old_reminders(self, days: int = 30):
        """Nettoie les rappels envoyés de plus de X jours"""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                DELETE FROM reminders
                WHERE sent = TRUE AND sent_at < NOW() - make_interval(days => $1)
            """, days)
