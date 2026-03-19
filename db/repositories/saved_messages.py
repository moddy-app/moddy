import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List

logger = logging.getLogger('moddy.database')


class SavedMessageRepository:
    """Saved message management database operations"""

    async def save_message(self, user_id: int, message_id: int, channel_id: int,
                          guild_id: int, author_id: int, author_username: str, content: str,
                          attachments: List[Dict], embeds: List[Dict],
                          created_at: datetime, message_url: str, raw_message_data: Dict,
                          note: str = None) -> int:
        """Sauvegarde un message dans la bibliothèque de l'utilisateur"""
        async with self.pool.acquire() as conn:
            # Vérifie si le message n'est pas déjà sauvegardé
            existing = await conn.fetchrow(
                "SELECT id FROM saved_messages WHERE user_id = $1 AND message_id = $2",
                user_id, message_id
            )
            if existing:
                return existing['id']

            # Sauvegarde le message
            row = await conn.fetchrow("""
                INSERT INTO saved_messages (
                    user_id, message_id, channel_id, guild_id, author_id, author_username,
                    content, attachments, embeds, created_at, message_url, note, raw_message_data
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                RETURNING id
            """, user_id, message_id, channel_id, guild_id, author_id, author_username,
                content, json.dumps(attachments), json.dumps(embeds),
                created_at, message_url, note, json.dumps(raw_message_data))
            return row['id']

    def _parse_saved_message(self, row) -> Dict[str, Any]:
        """Parse a saved message row into a dict with JSON fields decoded"""
        msg_dict = dict(row)
        msg_dict['attachments'] = self._parse_jsonb_list(msg_dict['attachments'])
        msg_dict['embeds'] = self._parse_jsonb_list(msg_dict['embeds'])
        msg_dict['raw_message_data'] = self._parse_jsonb(msg_dict.get('raw_message_data'))
        return msg_dict

    async def get_saved_messages(self, user_id: int, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        """Récupère les messages sauvegardés d'un utilisateur"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM saved_messages
                WHERE user_id = $1
                ORDER BY saved_at DESC
                LIMIT $2 OFFSET $3
            """, user_id, limit, offset)

            return [self._parse_saved_message(row) for row in rows]

    async def get_saved_message(self, saved_id: int, user_id: int) -> Optional[Dict[str, Any]]:
        """Récupère un message sauvegardé spécifique"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM saved_messages WHERE id = $1 AND user_id = $2",
                saved_id, user_id
            )
            if not row:
                return None

            return self._parse_saved_message(row)

    async def delete_saved_message(self, saved_id: int, user_id: int) -> bool:
        """Supprime un message sauvegardé"""
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM saved_messages WHERE id = $1 AND user_id = $2",
                saved_id, user_id
            )
            return result == "DELETE 1"

    async def update_saved_message_note(self, saved_id: int, user_id: int, note: str) -> bool:
        """Met à jour la note d'un message sauvegardé"""
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE saved_messages SET note = $1 WHERE id = $2 AND user_id = $3",
                note, saved_id, user_id
            )
            return result == "UPDATE 1"

    async def count_saved_messages(self, user_id: int) -> int:
        """Compte le nombre de messages sauvegardés par un utilisateur"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COUNT(*) as count FROM saved_messages WHERE user_id = $1",
                user_id
            )
            return row['count']

    async def search_saved_messages(self, user_id: int, query: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Recherche dans les messages sauvegardés"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM saved_messages
                WHERE user_id = $1 AND (
                    content ILIKE $2 OR
                    note ILIKE $2
                )
                ORDER BY saved_at DESC
                LIMIT $3
            """, user_id, f"%{query}%", limit)

            result = []
            for row in rows:
                msg_dict = dict(row)
                msg_dict['attachments'] = self._parse_jsonb_list(msg_dict['attachments'])
                msg_dict['embeds'] = self._parse_jsonb_list(msg_dict['embeds'])
                result.append(msg_dict)
            return result
