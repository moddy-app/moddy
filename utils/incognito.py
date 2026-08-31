"""
Incognito system for Moddy's slash commands
Allows users to control the visibility of their responses
"""

import asyncio
import logging
import time

import discord
from discord import app_commands
from typing import Optional
import functools
import types

logger = logging.getLogger("moddy.incognito")

# The visibility preference is read before the command body runs, which means
# it sits in front of the interaction's 3-second acknowledgement window on
# every single command that carries the option. A cold or contended database
# there does not degrade the reply — it kills the interaction outright and the
# user gets Discord's "the application did not respond".
#
# So the read is cached and time-boxed. The preference changes rarely (it is
# set from the dashboard, never by the bot itself), a stale value for a few
# minutes only means one reply has the wrong visibility, and either failure is
# incomparably cheaper than a dead interaction.
_CACHE_TTL = 300.0
#: Bound the cache so a busy shard cannot grow it without limit.
_CACHE_MAX = 10_000
#: Hard ceiling on the lookup. Well under the window, leaving the command room.
_LOOKUP_TIMEOUT = 1.0

_cache: "dict[int, tuple[float, Optional[bool]]]" = {}


def invalidate_incognito(user_id: int) -> None:
    """Drop a user's cached visibility preference (call after changing it)."""
    _cache.pop(user_id, None)


async def resolve_incognito(bot, user_id: int, default: bool = True) -> bool:
    """Resolve a user's response visibility, cached and time-boxed.

    Never raises and never blocks for long: on a miss, a timeout or any
    failure it falls back to ``default``. Private is the safe default — an
    answer that should have been public is a nuisance, one that should have
    been private is a leak.
    """
    now = time.monotonic()
    cached = _cache.get(user_id)
    if cached is not None and now - cached[0] < _CACHE_TTL:
        return default if cached[1] is None else bool(cached[1])

    db = getattr(bot, "db", None)
    if not db:
        return default

    try:
        pref = await asyncio.wait_for(
            db.get_attribute("user", user_id, "DEFAULT_INCOGNITO"),
            timeout=_LOOKUP_TIMEOUT,
        )
    except asyncio.TimeoutError:
        # Do not cache a timeout: the next command should try again.
        logger.warning("Incognito preference lookup timed out for user %s", user_id)
        return default
    except Exception as exc:
        logger.warning("Incognito preference lookup failed for user %s: %s", user_id, exc)
        return default

    if len(_cache) >= _CACHE_MAX:
        _cache.clear()
    _cache[user_id] = (now, pref)
    return default if pref is None else bool(pref)


def add_incognito_option(default_value: bool = True):
    """
    Decorator that adds the incognito option to a slash command

    Args:
        default_value: Default value if no user preference is set
    """

    def decorator(func):
        # Wrapper that adds the incognito parameter
        @functools.wraps(func)
        async def wrapper(self, interaction: discord.Interaction, *args, incognito: Optional[bool] = None, **kwargs):
            # IMPORTANT: If incognito is not specified explicitly in the command
            # we check the user's preference
            if incognito is None:
                if hasattr(self, 'bot'):
                    incognito = await resolve_incognito(
                        self.bot, interaction.user.id, default_value)
                else:
                    incognito = default_value

            # Store the incognito value in the interaction so the command can use it
            interaction.extras = getattr(interaction, 'extras', {})
            interaction.extras['incognito'] = incognito

            # Call the original function
            return await func(self, interaction, *args, **kwargs)

        # Add the incognito parameter to the annotations
        wrapper.__annotations__ = func.__annotations__.copy()
        wrapper.__annotations__['incognito'] = Optional[bool]

        # discord.py resolves string annotations (PEP 563, used by cogs with
        # `from __future__ import annotations`) against `callback.__globals__`.
        # A plain closure's __globals__ is fixed to this module, so a command
        # parameter annotated with a constant from its own cog module (e.g.
        # `app_commands.Range[str, 1, MAX_INPUT_LENGTH]`) would fail to resolve
        # and silently break the whole command tree sync. Rebind the wrapper to
        # the original callback's globals so lookups happen in the right module.
        rebound = types.FunctionType(
            wrapper.__code__, func.__globals__, wrapper.__name__,
            wrapper.__defaults__, wrapper.__closure__,
        )
        rebound = functools.wraps(func)(rebound)
        rebound.__annotations__ = wrapper.__annotations__
        rebound.__kwdefaults__ = wrapper.__kwdefaults__

        return rebound

    return decorator


def get_incognito_setting(interaction: discord.Interaction) -> bool:
    """
    Gets the incognito setting from the interaction

    Args:
        interaction: The Discord interaction

    Returns:
        bool: True if ephemeral (private), False if public
    """
    return interaction.extras.get('incognito', True) if hasattr(interaction, 'extras') else True


# Export of the main functions
__all__ = ['add_incognito_option', 'get_incognito_setting',
           'resolve_incognito', 'invalidate_incognito']