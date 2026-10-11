"""
Incognito system for Moddy's slash commands
Allows users to control the visibility of their responses
"""

import asyncio
import discord
from discord import app_commands
from typing import Optional
import functools
import time
import types
from typing import Dict, Tuple

# Discord requires the initial ack (response.send_message / defer) within 3s
# of receiving the interaction. This lookup runs before that ack, so it must
# never be allowed to eat into that budget on a slow/stuck DB call — a
# timeout here falls back to the default, same as any other lookup failure.
_PREFERENCE_LOOKUP_TIMEOUT = 0.8

# user_id -> (DEFAULT_INCOGNITO value or None, expiry). The preference is read
# before the first response of nearly every slash command; a short in-process
# cache keeps that DB round trip (and any pool wait) off the 3 s ack budget.
_PREF_TTL = 60.0
_PREF_CACHE_MAX = 4096
_pref_cache: "Dict[int, Tuple[Optional[bool], float]]" = {}


def invalidate_incognito_cache(user_id: Optional[int] = None) -> None:
    """Forget the cached DEFAULT_INCOGNITO of one user (or of everyone)."""
    if user_id is None:
        _pref_cache.clear()
    else:
        _pref_cache.pop(int(user_id), None)


async def resolve_default_incognito(bot, user_id: int, default: bool = True) -> bool:
    """The user's default for ephemeral replies, safe to await before an ack.

    Cached for ``_PREF_TTL`` seconds and bounded by ``_PREFERENCE_LOOKUP_TIMEOUT``;
    any failure (including a timeout) falls back to ``default``.
    """
    if not getattr(bot, "db", None):
        return default
    now = time.monotonic()
    hit = _pref_cache.get(user_id)
    if hit and hit[1] > now:
        pref = hit[0]
    else:
        try:
            pref = await asyncio.wait_for(
                bot.db.get_attribute('user', user_id, 'DEFAULT_INCOGNITO'),
                timeout=_PREFERENCE_LOOKUP_TIMEOUT,
            )
        except Exception as e:
            import logging
            logging.getLogger('moddy').warning(
                "Incognito preference lookup failed (%s); using default", type(e).__name__)
            return default
        if len(_pref_cache) >= _PREF_CACHE_MAX:
            _pref_cache.clear()
        _pref_cache[user_id] = (pref, now + _PREF_TTL)
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
                # First, check the user attribute for the default preference
                if hasattr(self, 'bot') and self.bot.db:
                    incognito = await resolve_default_incognito(
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
        # the original callback's globals so lookups happen in the right module —
        # but merge in this module's own globals first, since the wrapper's body
        # itself references names (asyncio, _PREFERENCE_LOOKUP_TIMEOUT) that only
        # live here, not in the cog module.
        merged_globals = {**func.__globals__, **globals()}
        rebound = types.FunctionType(
            wrapper.__code__, merged_globals, wrapper.__name__,
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
__all__ = ['add_incognito_option', 'get_incognito_setting']