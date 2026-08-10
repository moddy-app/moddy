"""
Subscription helper — gate commands/modules on active subscriptions.

Read strategy: Redis cache first (key sub:user:{user_id}), DB fallback.
The bot never writes subscription data; only the backend does.

Two scopes live here:

- **User subscription** (``get_subscription`` / ``is_subscribed``): does *this
  user* pay? Gates personal-app features.
- **Guild premium** (``is_guild_premium``): is *this server* covered by
  somebody's subscription? A subscriber picks up to N servers on the
  dashboard, which fills ``subscription_servers``. This — not a ``PREMIUM``
  attribute — is the source of truth for premium server features.

See ``docs/PREMIUM.md``.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger('moddy.subscription')

_CACHE_KEY = "sub:user:{user_id}"
_GUILD_CACHE_KEY = "sub:guild:{guild_id}"
# Short TTL: the backend also invalidates explicitly on premium_activated /
# premium_deactivated, the TTL is only there so a missed event self-heals.
_GUILD_CACHE_TTL = 300


def _cache_key(user_id: int) -> str:
    return _CACHE_KEY.format(user_id=user_id)


def _guild_cache_key(guild_id: int) -> str:
    return _GUILD_CACHE_KEY.format(guild_id=guild_id)


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
    """Return True if the user has an active subscription.

    A global limitation or suspension (Moddy-team sanction) removes premium
    access, whatever the billing state says — see ``utils/global_sanctions.py``.
    """
    from utils import global_sanctions
    if await global_sanctions.is_limited(bot, user_id=user_id):
        return False
    sub = await get_subscription(bot, user_id)
    return bool(sub and sub.get('is_active'))


async def is_guild_premium(bot, guild_id: int) -> bool:
    """Return True if this guild is covered by an active subscription.

    Redis-cached (``sub:guild:{guild_id}``, 5 min) on top of
    ``db.is_guild_premium``, because premium is checked on hot paths (every
    module config panel render, every dashboard task).

    A globally limited or suspended server never counts as premium — the
    sanction outranks the subscription. That check is not part of the cached
    value, so lifting the sanction restores premium immediately.
    """
    from utils import global_sanctions
    if await global_sanctions.is_limited(bot, guild_id=guild_id):
        return False

    if bot.redis:
        try:
            raw = await bot.redis.get(_guild_cache_key(guild_id))
            if raw is not None:
                return raw == "1"
        except Exception as e:
            logger.warning(f"[Subscription] Redis read error for guild {guild_id}: {e}")

    if not bot.db:
        return False

    try:
        premium = await bot.db.is_guild_premium(guild_id)
    except Exception as e:
        logger.error(f"[Subscription] DB read error for guild {guild_id}: {e}")
        return False

    if bot.redis:
        try:
            await bot.redis.setex(
                _guild_cache_key(guild_id), _GUILD_CACHE_TTL, "1" if premium else "0",
            )
        except Exception as e:
            logger.warning(f"[Subscription] Redis write error for guild {guild_id}: {e}")

    return bool(premium)


async def invalidate_guild_cache(bot, guild_id: int) -> None:
    """Evict the cached premium state of a guild (backend Pub/Sub events)."""
    if bot.redis:
        try:
            await bot.redis.delete(_guild_cache_key(guild_id))
        except Exception as e:
            logger.warning(f"[Subscription] Guild cache invalidation error for {guild_id}: {e}")


async def invalidate_cache(bot, user_id: int) -> None:
    """Evict the Redis cache entry for this user."""
    if bot.redis:
        try:
            await bot.redis.delete(_cache_key(user_id))
        except Exception as e:
            logger.warning(f"[Subscription] Cache invalidation error for {user_id}: {e}")
