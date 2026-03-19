import json
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger('moddy.database')


class ErrorRepository:
    """Error management database operations"""

    async def log_error(self, error_code: str, error_data: Dict[str, Any]):
        """Enregistre une erreur dans la base de données"""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO errors (error_code, error_type, message, file_source,
                                    line_number, traceback, user_id, guild_id,
                                    command, context, sentry_event_id, sentry_issue_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            """,
                error_code,
                error_data.get('type'),
                error_data.get('message'),
                error_data.get('file'),
                error_data.get('line'),
                error_data.get('traceback'),
                error_data.get('user_id'),
                error_data.get('guild_id'),
                error_data.get('command'),
                error_data.get('context', {}),
                error_data.get('sentry_event_id'),
                error_data.get('sentry_issue_id')
            )

    async def update_error_sentry_ids(self, error_code: str, sentry_event_id: Optional[str] = None, sentry_issue_id: Optional[str] = None):
        """Met à jour les IDs Sentry d'une erreur"""
        async with self.pool.acquire() as conn:
            if sentry_event_id and sentry_issue_id:
                await conn.execute("""
                    UPDATE errors
                    SET sentry_event_id = $2, sentry_issue_id = $3
                    WHERE error_code = $1
                """, error_code, sentry_event_id, sentry_issue_id)
            elif sentry_event_id:
                await conn.execute("""
                    UPDATE errors
                    SET sentry_event_id = $2
                    WHERE error_code = $1
                """, error_code, sentry_event_id)
            elif sentry_issue_id:
                await conn.execute("""
                    UPDATE errors
                    SET sentry_issue_id = $2
                    WHERE error_code = $1
                """, error_code, sentry_issue_id)

    async def get_error(self, error_code: str) -> Optional[Dict[str, Any]]:
        """Récupère une erreur par son code"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM errors WHERE error_code = $1",
                error_code
            )
            if not row:
                return None

            error_data = dict(row)
            # Compatibility: if context is a string, load it as JSON
            if isinstance(error_data.get('context'), str):
                try:
                    error_data['context'] = json.loads(error_data['context'])
                except (json.JSONDecodeError, TypeError):
                    error_data['context'] = {}  # Fallback to empty dict

            return error_data
