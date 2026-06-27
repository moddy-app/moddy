"""
Command usage logger (non-staff).

Sends a compact technical log to the webhook-based command feed whenever a
regular user command is used. Staff commands are intentionally skipped here —
they are covered by the staff command/action feeds via StaffLogger.

See utils/tech_logger.py and docs/TECHNICAL_LOGS.md.
"""

import logging

import discord
from discord.ext import commands

logger = logging.getLogger("moddy.command_logger")

# Top-level staff slash groups — skipped here (logged via StaffLogger instead).
_STAFF_ROOTS = {"dev", "team", "mod", "manage", "sup", "com"}


def _format_options(interaction: discord.Interaction) -> str:
    """Compact 'key=value' rendering of an app command's options."""
    try:
        ns = getattr(interaction, "namespace", None)
        if not ns:
            return ""
        parts = []
        for key, value in vars(ns).items():
            if isinstance(value, (discord.Member, discord.User, discord.Role,
                                  discord.abc.GuildChannel, discord.Thread)):
                value = f"{value}:{value.id}"
            parts.append(f"{key}={value}")
        return ", ".join(parts)
    except Exception:
        return ""


class CommandLogger(commands.Cog):
    """Logs non-staff command usage to the technical command feed."""

    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_app_command_completion(
        self,
        interaction: discord.Interaction,
        command,
    ):
        tech = getattr(self.bot, "tech_logger", None)
        if not tech:
            return

        name = getattr(command, "qualified_name", getattr(command, "name", "unknown"))
        root = name.split(" ", 1)[0]
        if root in _STAFF_ROOTS:
            return

        kind = "slash"
        if isinstance(command, discord.app_commands.ContextMenu):
            kind = "context-menu"
            name = command.name

        await tech.log_command(
            name=f"/{name}" if kind == "slash" else name,
            kind=kind,
            user=interaction.user,
            guild=interaction.guild,
            channel=interaction.channel,
            success=True,
            args=_format_options(interaction),
        )

    @commands.Cog.listener()
    async def on_command_completion(self, ctx: commands.Context):
        tech = getattr(self.bot, "tech_logger", None)
        if not tech:
            return
        # Staff prefix commands live in the staff.* package — skip them.
        if ctx.command and ctx.command.cog and ctx.command.cog.__module__.startswith("staff."):
            return

        args = ""
        if ctx.kwargs:
            args = ", ".join(f"{k}={v}" for k, v in list(ctx.kwargs.items())[:8])

        await tech.log_command(
            name=f"{ctx.prefix}{ctx.command.qualified_name}" if ctx.command else "unknown",
            kind="prefix",
            user=ctx.author,
            guild=ctx.guild,
            channel=ctx.channel,
            success=True,
            args=args,
        )


async def setup(bot):
    await bot.add_cog(CommandLogger(bot))
