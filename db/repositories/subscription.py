"""
Subscription repository — read-only access to subscription data.
The bot never writes subscription_* or stripe_customer_id columns.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger('moddy.database.subscription')


class SubscriptionRepository:
    """Read-only subscription queries."""

    async def get_subscription(self, user_id: int) -> Optional[Dict[str, Any]]:
        """
        Return subscription info for a user, or None if no subscription row exists.

        Returns dict with keys:
            tier               (str | None)
            expires_at         (datetime | None)
            stripe_customer_id (str | None)
            is_active          (bool)
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT subscription_tier,
                       subscription_expires_at,
                       stripe_customer_id,
                       subscription_interval
                FROM users
                WHERE user_id = $1
                """,
                user_id,
            )

        if not row:
            return None

        tier = row['subscription_tier']
        expires_at = row['subscription_expires_at']
        now = datetime.now(timezone.utc)

        is_active = bool(
            tier
            and (expires_at is None or expires_at > now)
        )

        return {
            'tier': tier,
            'expires_at': expires_at,
            'stripe_customer_id': row['stripe_customer_id'],
            'subscription_interval': row['subscription_interval'],
            'is_active': is_active,
        }

    async def get_subscription_servers(self, user_id: int) -> List[Dict[str, Any]]:
        """Return the list of servers linked to this user's subscription."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT server_id, added_at
                FROM subscription_servers
                WHERE user_id = $1
                ORDER BY added_at ASC
                """,
                str(user_id),
            )
        return [{'server_id': r['server_id'], 'added_at': r['added_at']} for r in rows]
