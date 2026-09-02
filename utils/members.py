"""
Member lookup helpers that work with or without the startup member chunk.

`config.CHUNK_GUILDS_AT_STARTUP` defaults to False, so `guild.get_member()` is
no longer guaranteed to hit: the cache only holds members Moddy has actually
seen (joins, messages, interactions -- `MemberCacheFlags(joined=True)`). These
helpers cover the two patterns the codebase needs:

- `get_or_fetch_member()` for a single lookup: cache first, one REST call on a
  miss.
- `fetch_all_members()` for the rare code path that genuinely needs the whole
  member list (stats, AltGuard resync): pulls it on demand over the gateway,
  without leaving it resident unless the caller asks.

Both are safe to call when chunking is enabled -- they simply hit the cache.
"""

import logging
from typing import Optional

import discord

logger = logging.getLogger('moddy.members')


async def get_or_fetch_member(
    guild: Optional[discord.Guild],
    user_id: Optional[int],
) -> Optional[discord.Member]:
    """Return a guild member, falling back to a REST fetch on a cache miss.

    Returns None when the guild or id is missing, when the user is not a member,
    or when Discord refuses the lookup. Callers already handle None because
    `guild.get_member()` could always return it.

    A successful fetch populates the member cache, so repeated lookups for the
    same person cost one request, not one per call.
    """
    if guild is None or user_id is None:
        return None

    member = guild.get_member(user_id)
    if member is not None:
        return member

    try:
        return await guild.fetch_member(user_id)
    except discord.NotFound:
        # Not a member (left, never joined, or a bare user id).
        return None
    except discord.HTTPException as e:
        logger.warning(
            f"fetch_member failed for {user_id} in guild {guild.id}: {e}"
        )
        return None


async def fetch_all_members(
    guild: Optional[discord.Guild],
    *,
    cache: bool = False,
) -> Optional[list]:
    """Return a guild's complete member list, chunking over the gateway if needed.

    Returns None when the list could not be completed -- the caller must then
    treat `guild.members` as a partial sample rather than report a wrong total.

    `cache` defaults to False: callers that only need to count or enumerate once
    (stats, an hourly reconciliation) should not leave every member resident in
    memory afterwards. Pass cache=True only if repeated lookups follow.
    """
    if guild is None:
        return None

    if guild.chunked:
        return list(guild.members)

    try:
        members = await guild.chunk(cache=cache)
    except (discord.ClientException, discord.HTTPException) as e:
        logger.warning(f"Could not chunk guild {guild.id}: {e}")
        return None
    except Exception as e:  # gateway timeouts surface as plain TimeoutError
        logger.warning(f"Could not chunk guild {guild.id}: {e}")
        return None

    if members is not None:
        return list(members)

    # Some paths populate the cache instead of returning the list.
    if guild.chunked:
        return list(guild.members)

    return None
