"""Application-command helpers with Discord-compatible defaults.

All discord.py command APIs remain available through this module.  The two
Moddy decorators only encode the project's global-vs-guild command contract;
they do not replace discord.py's command tree.
"""

from typing import Any, Callable

from discord import app_commands as _app_commands


def global_command(*, name: str | None = None, description: str | None = None):
    """Create a command available to user-installed and guild-installed apps.

    The decorator applies Moddy's required installation and interaction
    contexts before creating the regular discord.py command.
    """

    def decorator(callback: Callable[..., Any]):
        callback = _app_commands.allowed_contexts(
            guilds=True, dms=True, private_channels=True
        )(callback)
        callback = _app_commands.allowed_installs(guilds=True, users=True)(callback)
        return _app_commands.command(name=name, description=description)(callback)

    return decorator


def guild_command(*, name: str | None = None, description: str | None = None):
    """Create a command scoped to guilds where Moddy is installed."""

    def decorator(callback: Callable[..., Any]):
        callback = _app_commands.guild_only()(callback)
        return _app_commands.command(name=name, description=description)(callback)

    return decorator


def __getattr__(name: str):
    """Expose the complete upstream ``discord.app_commands`` surface."""

    return getattr(_app_commands, name)


__all__ = ("global_command", "guild_command")
