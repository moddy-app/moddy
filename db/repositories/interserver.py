import json
import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger('moddy.database')


class InterserverRepository:
    """Interserver message management database operations"""

    async def create_interserver_message(self, moddy_id: str, original_message_id: int,
                                        original_guild_id: int, original_channel_id: int,
                                        author_id: int, author_username: str, content: str,
                                        is_moddy_team: bool = False) -> bool:
        """Crée un enregistrement de message inter-serveur"""
        async with self.pool.acquire() as conn:
            try:
                await conn.execute("""
                    INSERT INTO interserver_messages (
                        moddy_id, original_message_id, original_guild_id, original_channel_id,
                        author_id, author_username, content, is_moddy_team
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """, moddy_id, original_message_id, original_guild_id, original_channel_id,
                    author_id, author_username, content, is_moddy_team)
                return True
            except Exception as e:
                logger.error(f"Error creating interserver message: {e}")
                return False

    async def add_relayed_message(self, moddy_id: str, guild_id: int, channel_id: int, message_id: int):
        """Ajoute un message relayé à l'enregistrement"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT relayed_messages FROM interserver_messages WHERE moddy_id = $1",
                moddy_id
            )
            if not row:
                return

            relayed = self._parse_jsonb_list(row['relayed_messages'])
            relayed.append({
                'guild_id': guild_id,
                'channel_id': channel_id,
                'message_id': message_id
            })

            await conn.execute(
                "UPDATE interserver_messages SET relayed_messages = $1::jsonb WHERE moddy_id = $2",
                json.dumps(relayed),
                moddy_id
            )

    async def get_interserver_message(self, moddy_id: str) -> Optional[Dict[str, Any]]:
        """Récupère un message inter-serveur par son ID Moddy"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM interserver_messages WHERE moddy_id = $1",
                moddy_id
            )
            if not row:
                return None

            msg_dict = dict(row)
            msg_dict['relayed_messages'] = self._parse_jsonb_list(msg_dict['relayed_messages'])
            return msg_dict

    async def get_interserver_message_by_original(self, original_message_id: int) -> Optional[Dict[str, Any]]:
        """Récupère un message inter-serveur par l'ID du message original"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM interserver_messages WHERE original_message_id = $1",
                original_message_id
            )
            if not row:
                return None

            msg_dict = dict(row)
            msg_dict['relayed_messages'] = self._parse_jsonb_list(msg_dict['relayed_messages'])
            return msg_dict

    async def delete_interserver_message(self, moddy_id: str) -> bool:
        """Supprime un message inter-serveur (change le status à 'deleted')"""
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE interserver_messages SET status = 'deleted' WHERE moddy_id = $1",
                moddy_id
            )
            return result == "UPDATE 1"

    async def get_interserver_messages_by_author(self, author_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        """Récupère les messages inter-serveur d'un auteur"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM interserver_messages
                WHERE author_id = $1 AND status = 'active'
                ORDER BY created_at DESC
                LIMIT $2
            """, author_id, limit)

            result = []
            for row in rows:
                msg_dict = dict(row)
                msg_dict['relayed_messages'] = self._parse_jsonb_list(msg_dict['relayed_messages'])
                result.append(msg_dict)
            return result
