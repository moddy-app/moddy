"""
Logging system for developer commands
Records all uses of staff commands in a dedicated channel
"""

import discord
from discord.ext import commands
from datetime import datetime, timezone
import traceback
import json
from typing import Optional, Dict, Any

from config import COLORS
from utils.log_cards import build_log_card, send_log_card


class DevCommandLogger(commands.Cog):
    """Automatic logger for all dev commands"""

    def __init__(self, bot):
        self.bot = bot
        self.log_channel_id = 1394323753701212291  # Dev logs channel
        self.command_stats = {}  # Usage statistics

    async def get_log_channel(self) -> Optional[discord.TextChannel]:
        """Gets the log channel"""
        return self.bot.get_channel(self.log_channel_id)

    def is_dev_command(self, ctx: commands.Context) -> bool:
        """Checks if it's a dev command"""
        # Checks if the cog is in the staff folder
        if ctx.command and ctx.command.cog:
            cog_module = ctx.command.cog.__module__
            return cog_module.startswith('staff.')
        return False

    async def log_command_execution(
            self,
            ctx: commands.Context,
            success: bool,
            error: Optional[Exception] = None,
            execution_time: float = 0.0,
            additional_info: Dict[str, Any] = None
    ):
        """Logs the execution of a dev command"""
        channel = await self.get_log_channel()
        if not channel:
            return

        # Determine the colour based on the result
        if success:
            color = COLORS["success"]
            status = "Success"
        else:
            color = COLORS["error"]
            status = "Failure"

        fields = [
            ("User", f"{ctx.author.mention}\n`{ctx.author}` (`{ctx.author.id}`)"),
        ]

        # Information about the execution location
        if ctx.guild:
            location = f"**Server:** {ctx.guild.name}\n**Channel:** {ctx.channel.mention}"
        else:
            location = "**DM**"
        fields.append(("Location", location))
        fields.append(("Status", f"{status}\n**Time:** `{execution_time:.2f}s`"))

        # Full command
        # Mask tokens or sensitive info
        command_text = ctx.message.content
        if "token" in command_text.lower() or "secret" in command_text.lower():
            # Mask sensitive parts
            words = command_text.split()
            for i, word in enumerate(words):
                if len(word) > 20 and not word.startswith("<@"):  # Probably a token
                    words[i] = f"{word[:6]}...{word[-4:]}"
            command_text = " ".join(words)

        fields.append(("Command", f"```\n{command_text[:500]}\n```"))

        # Command arguments
        if ctx.args or ctx.kwargs:
            args_str = ""
            if len(ctx.args) > 2:  # Ignore self and ctx
                args_list = [repr(arg) for arg in ctx.args[2:]]  # Skip self and ctx
                args_str += f"**Args:** {', '.join(args_list[:5])}\n"
            if ctx.kwargs:
                kwargs_list = [f"{k}={repr(v)}" for k, v in list(ctx.kwargs.items())[:5]]
                args_str += f"**Kwargs:** {', '.join(kwargs_list)}"

            if args_str:
                fields.append(("Arguments", args_str[:1024]))

        # Error if failure
        if error:
            error_details = f"**Type:** `{type(error).__name__}`\n"
            error_details += f"**Message:** {str(error)[:200]}"

            # Short traceback for serious errors
            if not isinstance(error, (commands.CommandError, commands.CheckFailure)):
                tb_lines = traceback.format_exception(type(error), error, error.__traceback__)
                tb_short = '\n'.join(tb_lines[-3:])[:500]
                error_details += f"\n```py\n{tb_short}\n```"

            fields.append(("Error", error_details))

        # Additional information
        if additional_info:
            info_str = "\n".join([f"**{k}:** {v}" for k, v in list(additional_info.items())[:5]])
            fields.append(("Additional Info", info_str[:1024]))

        # Footer with stats
        command_count = self.command_stats.get(ctx.command.name, 0) + 1
        self.command_stats[ctx.command.name] = command_count

        view = build_log_card(
            f"Dev Command: `{ctx.command.name}`",
            accent=color,
            fields=fields,
            footer=f"Usage #{command_count} • Module: {ctx.command.cog.__class__.__name__}",
        )

        # Send the log
        try:
            await channel.send(view=view)
        except Exception as e:
            print(f"Error sending log: {e}")

    @commands.Cog.listener()
    async def on_command(self, ctx: commands.Context):
        """Called when a command starts"""
        if self.is_dev_command(ctx):
            # Store the start time
            ctx.command_start_time = datetime.now()

    @commands.Cog.listener()
    async def on_command_completion(self, ctx: commands.Context):
        """Called when a command successfully completes"""
        if self.is_dev_command(ctx):
            # Calculate execution time
            execution_time = 0.0
            if hasattr(ctx, 'command_start_time'):
                execution_time = (datetime.now() - ctx.command_start_time).total_seconds()

            # Log the success
            await self.log_command_execution(
                ctx=ctx,
                success=True,
                execution_time=execution_time
            )

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        """Called when a command fails"""
        if self.is_dev_command(ctx):
            # Calculate execution time
            execution_time = 0.0
            if hasattr(ctx, 'command_start_time'):
                execution_time = (datetime.now() - ctx.command_start_time).total_seconds()

            # Log the failure
            await self.log_command_execution(
                ctx=ctx,
                success=False,
                error=error,
                execution_time=execution_time
            )


class LoggingSystem(commands.Cog):
    """Manual logging system for dev commands"""

    def __init__(self, bot):
        self.bot = bot
        self.log_channel_id = 1394323753701212291

    async def log_command(self, ctx: commands.Context, action: str, details: Dict[str, Any] = None):
        """Manual log for specific actions"""
        channel = self.bot.get_channel(self.log_channel_id)
        if not channel:
            return

        fields = [("User", f"{ctx.author.mention} (`{ctx.author.id}`)")]
        if ctx.guild:
            fields.append(("Server", f"{ctx.guild.name}"))
        if details:
            for key, value in details.items():
                fields.append((str(key), str(value)[:1024]))

        view = build_log_card(
            f"Dev Action: {action}",
            accent=COLORS["info"],
            fields=fields,
            footer=f"Action: {action}",
        )

        try:
            await channel.send(view=view)
        except Exception:
            pass

    async def log_critical(self, title: str, description: str, ping_dev: bool = True):
        """Log for critical events"""
        channel = self.bot.get_channel(self.log_channel_id)
        if not channel:
            return

        view = build_log_card(
            title,
            accent=COLORS["error"],
            description=description,
        )

        ping = None
        if ping_dev:
            # Ping the first dev of the team
            if self.bot._dev_team_ids:
                dev_id = next(iter(self.bot._dev_team_ids))
                ping = f"<@{dev_id}> Critical alert!"

        try:
            await send_log_card(channel, view, ping=ping)
        except Exception:
            pass


async def setup(bot):
    # First, load the automatic logger
    await bot.add_cog(DevCommandLogger(bot))
    # Then, the manual logging system
    await bot.add_cog(LoggingSystem(bot))