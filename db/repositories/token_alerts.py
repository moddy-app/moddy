import json
import logging
from typing import Optional

logger = logging.getLogger('moddy.database')


class TokenAlertRepository:
    """Persists token-alert metadata (never the token itself) for button state
    survival across bot restarts."""

    async def save_token_alert(self, ck: str, payload: dict) -> None:
        state = payload.get("state", {"deleted": False, "invalidated": False})
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO token_alerts (
                    ck, masked_content, msg_id, channel_id, channel_name,
                    guild_id, guild_name, author_id, author_name, alert_timestamp,
                    bot_id, bot_name, state, dm_message_id, dm_channel_id
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
                ON CONFLICT (ck) DO NOTHING
                """,
                ck,
                payload.get("masked_content"),
                payload.get("msg_id"),
                payload.get("channel_id"),
                payload.get("channel_name"),
                payload.get("guild_id"),
                payload.get("guild_name"),
                payload.get("author_id"),
                payload.get("author_name"),
                payload.get("timestamp"),
                payload.get("bot_id"),
                payload.get("bot_name"),
                json.dumps(state),
                payload.get("dm_message_id"),
                payload.get("dm_channel_id"),
            )

    async def get_token_alert(self, ck: str) -> Optional[dict]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM token_alerts WHERE ck = $1", ck
            )
        if not row:
            return None
        state = row["state"]
        if isinstance(state, str):
            state = json.loads(state)
        elif state is None:
            state = {"deleted": False, "invalidated": False}
        return {
            "token": "",  # token is never stored in DB
            "email": None,
            "masked_content": row["masked_content"],
            "msg_id": row["msg_id"],
            "channel_id": row["channel_id"],
            "channel_name": row["channel_name"],
            "guild_id": row["guild_id"],
            "guild_name": row["guild_name"],
            "author_id": row["author_id"],
            "author_name": row["author_name"],
            "timestamp": row["alert_timestamp"],
            "bot_id": row["bot_id"],
            "bot_name": row["bot_name"],
            "state": state,
            "dm_message_id": row["dm_message_id"],
            "dm_channel_id": row["dm_channel_id"],
        }

    async def update_token_alert_state(self, ck: str, state: dict) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE token_alerts SET state = $2::jsonb WHERE ck = $1",
                ck,
                json.dumps(state),
            )

    async def update_token_alert_dm(
        self, ck: str, dm_message_id: int, dm_channel_id: int
    ) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE token_alerts SET dm_message_id = $2, dm_channel_id = $3 WHERE ck = $1",
                ck,
                dm_message_id,
                dm_channel_id,
            )
