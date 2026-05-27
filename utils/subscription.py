"""
Subscription helper — gate commands/modules on active subscriptions.

Read strategy: Redis cache first (key sub:user:{user_id}), DB fallback.
The bot never writes subscription data; only the backend does.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger('moddy.subscription')

_CACHE_KEY = "sub:user:{user_id}"


def _cache_key(user_id: int) -> str:
    return _CACHE_KEY.format(user_id=user_id)


def _ttl_seconds(expires_at: Optional[datetime]) -> Optional[int]:
    """Return seconds until expiry, or None if no expiry (no TTL set)."""
    if expires_at is None:
        return None
    delta = int((expires_at - datetime.now(timezone.utc)).total_seconds())
    return max(delta, 0)


async def get_subscription(bot, user_id: int) -> Optional[Dict[str, Any]]:
    """
    Return subscription info dict or None.

    Dict keys: tier, expires_at (datetime|None), stripe_customer_id, is_active.
    Reads Redis first; falls back to DB and caches the result.
    """
    if bot.redis:
        try:
            raw = await bot.redis.get(_cache_key(user_id))
            if raw:
                cached = json.loads(raw)
                expires_raw = cached.get('expires_at')
                expires_at = (
                    datetime.fromisoformat(expires_raw) if expires_raw else None
                )
                now = datetime.now(timezone.utc)
                is_active = bool(
                    cached.get('tier')
                    and (expires_at is None or expires_at > now)
                )
                return {
                    'tier': cached.get('tier'),
                    'expires_at': expires_at,
                    'stripe_customer_id': cached.get('stripe_customer_id'),
                    'is_active': is_active,
                }
        except Exception as e:
            logger.warning(f"[Subscription] Redis read error for {user_id}: {e}")

    if not bot.db:
        return None

    try:
        data = await bot.db.get_subscription(user_id)
    except Exception as e:
        logger.error(f"[Subscription] DB read error for {user_id}: {e}")
        return None

    if data and data.get('is_active') and bot.redis:
        # Only cache active subscriptions. Inactive results are never cached so that
        # a newly-created subscription is always visible on the next read even if the
        # Pub/Sub invalidation message was missed (fire-and-forget).
        try:
            payload = {
                'tier': data['tier'],
                'expires_at': data['expires_at'].isoformat() if data['expires_at'] else None,
                'stripe_customer_id': data['stripe_customer_id'],
            }
            ttl = _ttl_seconds(data['expires_at'])
            if ttl is None or ttl > 0:
                if ttl:
                    await bot.redis.setex(_cache_key(user_id), ttl, json.dumps(payload))
                else:
                    await bot.redis.set(_cache_key(user_id), json.dumps(payload))
        except Exception as e:
            logger.warning(f"[Subscription] Redis write error for {user_id}: {e}")

    return data


async def is_subscribed(bot, user_id: int) -> bool:
    """Return True if the user has an active subscription."""
    sub = await get_subscription(bot, user_id)
    return bool(sub and sub.get('is_active'))


async def invalidate_cache(bot, user_id: int) -> None:
    """Evict the Redis cache entry for this user."""
    if bot.redis:
        try:
            await bot.redis.delete(_cache_key(user_id))
        except Exception as e:
            logger.warning(f"[Subscription] Cache invalidation error for {user_id}: {e}")
