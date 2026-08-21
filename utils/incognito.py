"""
Incognito system for Moddy's slash commands
Allows users to control the visibility of their responses
"""

import discord
from discord import app_commands
from typing import Optional
import functools
import types


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
                    try:
                        # Get the DEFAULT_INCOGNITO preference
                        user_pref = await self.bot.db.get_attribute('user', interaction.user.id, 'DEFAULT_INCOGNITO')

                        # If the user has a defined preference
                        if user_pref is not None:
                            # If DEFAULT_INCOGNITO is False, we want messages to be public by default
                            incognito = user_pref
                        else:
                            # No preference defined, use the default value
                            incognito = default_value
                    except Exception as e:
                        # In case of an error, use the default value
                        import logging
                        logger = logging.getLogger('moddy')
                        logger.error(f"Error getting incognito preference: {e}")
                        incognito = default_value
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
__all__ = ['add_incognito_option', 'get_incognito_setting']