"""
Social subscriptions repository.

Stores the mapping between a Discord guild/channel and a *canonical* social
target (resolved by the external ``moddy-feeds`` service). A single social
target (e.g. a YouTube channel) can be followed by many guilds; this table is
the bot's source of truth for "who follows what" and is used to:

  - render the configuration panel (`/config` → Social Notifications),
  - dispatch incoming notifications to every guild that follows a target,
  - decide when to send a `subscribe`/`unsubscribe` command to the service
    (subscribe on the first follow, unsubscribe only when the last guild drops
    the target — see docs/SOCIAL_NOTIFICATIONS.md).

IMPORTANT: We always store the *canonical* ``target_id`` returned by the
service, never the raw user input.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger('moddy.database')

# Columns that may be updated through ``update_subscription`` (whitelist guards
# against SQL injection on identifiers).
_UPDATABLE_COLUMNS = {
    "channel_id",
    "message",
    "mention_role_ids",
    "poll_interval",
    "enabled",
    "display_name",
    "avatar_url",
    "identifier",
}


def _row_to_dict(row) -> Dict[str, Any]:
    """Convert an asyncpg Record into a plain subscription dict."""
    return {
        "id": row["id"],
        "guild_id": row["guild_id"],
        "platform": row["platform"],
        "target_id": row["target_id"],
        "identifier": row["identifier"],
        "display_name": row["display_name"],
        "avatar_url": row["avatar_url"],
        "channel_id": row["channel_id"],
        "message": row["message"],
        "mention_role_ids": list(row["mention_role_ids"] or []),
        "poll_interval": row["poll_interval"],
        "enabled": row["enabled"],
        "created_by": row["created_by"],
    }


class SocialSubscriptionsRepository:
    """Database operations for the Social Notifications module."""

    async def add_social_subscription(
        self,
        *,
        guild_id: int,
        platform: str,
        target_id: str,
        channel_id: int,
        identifier: Optional[str] = None,
        display_name: Optional[str] = None,
        avatar_url: Optional[str] = None,
        message: Optional[str] = None,
        mention_role_ids: Optional[List[int]] = None,
        poll_interval: Optional[int] = None,
        created_by: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Insert or update a guild's subscription to a target.

        Uniqueness is on (guild_id, platform, target_id): re-adding an existing
        target updates its channel/metadata instead of duplicating it.
        """
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO social_subscriptions (
                        guild_id, platform, target_id, identifier, display_name,
                        avatar_url, channel_id, message, mention_role_ids,
                        poll_interval, enabled, created_by, created_at, updated_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, TRUE, $11, NOW(), NOW())
                    ON CONFLICT (guild_id, platform, target_id) DO UPDATE SET
                        identifier = EXCLUDED.identifier,
                        display_name = EXCLUDED.display_name,
                        avatar_url = EXCLUDED.avatar_url,
                        channel_id = EXCLUDED.channel_id,
                        message = EXCLUDED.message,
                        mention_role_ids = EXCLUDED.mention_role_ids,
                        poll_interval = EXCLUDED.poll_interval,
                        enabled = TRUE,
                        updated_at = NOW()
                    RETURNING *
                    """,
                    guild_id, platform, target_id, identifier, display_name,
                    avatar_url, channel_id, message, list(mention_role_ids or []),
                    poll_interval, created_by,
                )
                return _row_to_dict(row) if row else None
        except Exception as e:
            logger.error(f"[ERROR] add_social_subscription failed: {e}", exc_info=True)
            return None

    async def update_social_subscription(
        self,
        guild_id: int,
        platform: str,
        target_id: str,
        **fields: Any,
    ) -> bool:
        """Update specific columns of an existing subscription.

        Only whitelisted columns are accepted.
        """
        updates = {k: v for k, v in fields.items() if k in _UPDATABLE_COLUMNS}
        if not updates:
            return False
        try:
            set_parts = []
            values: List[Any] = []
            for idx, (col, val) in enumerate(updates.items(), start=4):
                set_parts.append(f"{col} = ${idx}")
                values.append(val)
            query = (
                "UPDATE social_subscriptions SET "
                + ", ".join(set_parts)
                + ", updated_at = NOW() "
                + "WHERE guild_id = $1 AND platform = $2 AND target_id = $3"
            )
            async with self.pool.acquire() as conn:
                result = await conn.execute(query, guild_id, platform, target_id, *values)
            return result.split()[-1] != '0'
        except Exception as e:
            logger.error(f"[ERROR] update_social_subscription failed: {e}", exc_info=True)
            return False

    async def remove_social_subscription(self, guild_id: int, platform: str, target_id: str) -> bool:
        """Delete one guild's subscription to a target."""
        try:
            async with self.pool.acquire() as conn:
                result = await conn.execute(
                    """
                    DELETE FROM social_subscriptions
                    WHERE guild_id = $1 AND platform = $2 AND target_id = $3
                    """,
                    guild_id, platform, target_id,
                )
            return result.split()[-1] != '0'
        except Exception as e:
            logger.error(f"[ERROR] remove_social_subscription failed: {e}", exc_info=True)
            return False

    async def get_social_subscription(
        self, guild_id: int, platform: str, target_id: str
    ) -> Optional[Dict[str, Any]]:
        """Fetch a single subscription for a guild."""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT * FROM social_subscriptions
                    WHERE guild_id = $1 AND platform = $2 AND target_id = $3
                    """,
                    guild_id, platform, target_id,
                )
            return _row_to_dict(row) if row else None
        except Exception as e:
            logger.error(f"[ERROR] get_social_subscription failed: {e}", exc_info=True)
            return None

    async def list_social_subscriptions(self, guild_id: int) -> List[Dict[str, Any]]:
        """List all subscriptions configured in a guild (for the config panel)."""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM social_subscriptions
                    WHERE guild_id = $1
                    ORDER BY platform, created_at
                    """,
                    guild_id,
                )
            return [_row_to_dict(r) for r in rows]
        except Exception as e:
            logger.error(f"[ERROR] list_social_subscriptions failed: {e}", exc_info=True)
            return []

    async def count_social_subscriptions(self, guild_id: int) -> int:
        """Count subscriptions configured in a guild."""
        try:
            async with self.pool.acquire() as conn:
                count = await conn.fetchval(
                    "SELECT COUNT(*) FROM social_subscriptions WHERE guild_id = $1",
                    guild_id,
                )
            return count or 0
        except Exception as e:
            logger.error(f"[ERROR] count_social_subscriptions failed: {e}", exc_info=True)
            return 0

    async def get_target_followers(self, platform: str, target_id: str) -> List[Dict[str, Any]]:
        """All *enabled* subscriptions following a target — used to dispatch an event."""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM social_subscriptions
                    WHERE platform = $1 AND target_id = $2 AND enabled = TRUE
                    """,
                    platform, target_id,
                )
            return [_row_to_dict(r) for r in rows]
        except Exception as e:
            logger.error(f"[ERROR] get_target_followers failed: {e}", exc_info=True)
            return []

    async def count_target_guilds(self, platform: str, target_id: str) -> int:
        """How many guilds currently follow a target (any state)."""
        try:
            async with self.pool.acquire() as conn:
                count = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM social_subscriptions
                    WHERE platform = $1 AND target_id = $2
                    """,
                    platform, target_id,
                )
            return count or 0
        except Exception as e:
            logger.error(f"[ERROR] count_target_guilds failed: {e}", exc_info=True)
            return 0

    async def get_target_min_poll_interval(self, platform: str, target_id: str) -> Optional[int]:
        """Smallest (most demanding) requested poll interval among remaining guilds.

        Returns ``None`` when no guild has an explicit interval (e.g. realtime
        platforms such as Bluesky).
        """
        try:
            async with self.pool.acquire() as conn:
                val = await conn.fetchval(
                    """
                    SELECT MIN(poll_interval) FROM social_subscriptions
                    WHERE platform = $1 AND target_id = $2 AND poll_interval IS NOT NULL
                    """,
                    platform, target_id,
                )
            return val
        except Exception as e:
            logger.error(f"[ERROR] get_target_min_poll_interval failed: {e}", exc_info=True)
            return None

    async def delete_guild_social_subscriptions(self, guild_id: int) -> List[Tuple[str, str]]:
        """Delete every subscription of a guild (e.g. on guild removal).

        Returns the list of (platform, target_id) the guild was following so the
        caller can recompute service subscriptions.
        """
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    DELETE FROM social_subscriptions
                    WHERE guild_id = $1
                    RETURNING platform, target_id
                    """,
                    guild_id,
                )
            return [(r["platform"], r["target_id"]) for r in rows]
        except Exception as e:
            logger.error(f"[ERROR] delete_guild_social_subscriptions failed: {e}", exc_info=True)
            return []
