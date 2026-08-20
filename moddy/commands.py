"""Bot and cog primitives compatible with ``discord.ext.commands``."""

from discord.ext import commands


class Bot(commands.Bot):
    """Base class for Moddy bots.

    This class deliberately does not override discord.py lifecycle behaviour.
    Existing bots can inherit from it without changing their startup, caching,
    sharding, or command-tree semantics. Moddy-specific capabilities belong in
    explicit helpers rather than hidden side effects in a constructor.
    """


class Cog(commands.Cog):
    """Base class for Moddy cogs.

    It is a compatibility anchor for future shared cog behaviour while
    remaining a drop-in replacement for :class:`commands.Cog` today.
    """


__all__ = ("Bot", "Cog")
