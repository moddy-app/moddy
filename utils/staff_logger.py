"""
Staff Command Logger
Logs all staff commands and actions to a designated Discord channel
"""

import discord
from discord.ext import commands
from typing import Optional, Dict, Any
import logging
from datetime import datetime, timezone

logger = logging.getLogger('moddy.staff_logger')


class StaffLogger:
    """Logger for staff commands and actions"""

    def __init__(self, bot):
        self.bot = bot
        # Staff logs channel
        self.log_channel_id = 1408872408827297952
        self.log_server_id = 1394001780148535387

    async def get_log_channel(self) -> Optional[discord.TextChannel]:
        """Get the staff log channel"""
        try:
            guild = self.bot.get_guild(self.log_server_id)
            if not guild:
                logger.warning(f"Staff log guild not found: {self.log_server_id}")
                return None

            channel = guild.get_channel(self.log_channel_id)
            if not channel:
                logger.warning(f"Staff log channel not found: {self.log_channel_id}")
                return None

            return channel
        except Exception as e:
            logger.error(f"Error getting staff log channel: {e}")
            return None

    async def log_command(
        self,
        command_type: str,
        command_name: str,
        executor: discord.User,
        args: str = "",
        target_user: Optional[discord.User] = None,
        target_server: Optional[discord.Guild] = None,
        success: bool = True,
        error_message: str = None,
        additional_info: Dict[str, Any] = None
    ):
        """
        Log a staff command execution

        Args:
            command_type: Command type (e.g., "d", "m", "t", "mod")
            command_name: Command name (e.g., "error", "rank", "blacklist")
            executor: User who executed the command
            args: Command arguments
            target_user: User targeted by the command (if applicable)
            target_server: Server targeted by the command (if applicable)
            success: Whether the command succeeded
            error_message: Error message if command failed
            additional_info: Additional information to log
        """
        # Staff command logging now goes exclusively through the webhook-based
        # technical logs (dedicated channel). The legacy in-server embed that
        # used to be posted here has been removed.
        tech = getattr(self.bot, "tech_logger", None)
        if tech:
            await tech.log_staff_command(
                command_type, command_name, executor,
                args=args,
                guild=target_server,
                success=success,
            )
            logger.info(f"Logged staff command: {command_type}.{command_name} by {executor.id}")

    async def log_action(
        self,
        action: str,
        executor: discord.User,
        description: str,
        target: Optional[str] = None,
        success: bool = True,
        additional_info: Dict[str, Any] = None
    ):
        """
        Log a general staff action (not necessarily a command)

        Args:
            action: Action performed (e.g., "Permission Change", "Role Assignment")
            executor: User who performed the action
            description: Description of the action
            target: Target of the action (user, server, etc.)
            success: Whether the action succeeded
            additional_info: Additional information to log
        """
        # Technical log (webhook-based, separate channel)
        tech = getattr(self.bot, "tech_logger", None)
        if tech:
            await tech.log_staff_action(
                action, executor, description,
                target=target,
                success=success,
                additional_info=additional_info,
            )

        channel = await self.get_log_channel()
        if not channel:
            return

        try:
            from config import COLORS
            color = COLORS["success"] if success else COLORS["error"]

            embed = discord.Embed(
                title=f"{'✅' if success else '❌'} Staff Action: {action}",
                description=description,
                color=color,
                timestamp=datetime.now(timezone.utc)
            )

            embed.add_field(
                name="👤 Executor",
                value=f"{executor.mention} ({executor})\nID: `{executor.id}`",
                inline=True
            )

            if target:
                embed.add_field(
                    name="🎯 Target",
                    value=target,
                    inline=True
                )

            if additional_info:
                info_parts = []
                for key, value in additional_info.items():
                    if isinstance(value, (list, tuple)):
                        value_str = ", ".join(str(v) for v in value)
                    else:
                        value_str = str(value)

                    if len(value_str) > 200:
                        value_str = value_str[:200] + "..."

                    info_parts.append(f"**{key}:** {value_str}")

                if info_parts:
                    embed.add_field(
                        name="ℹ️ Details",
                        value="\n".join(info_parts),
                        inline=False
                    )

            await channel.send(embed=embed)
            logger.info(f"Logged staff action: {action} by {executor.id}")

        except Exception as e:
            logger.error(f"Error logging staff action: {e}")


# Global instance (will be initialized by the bot)
staff_logger: Optional[StaffLogger] = None


def init_staff_logger(bot):
    """Initialize the global staff logger"""
    global staff_logger
    staff_logger = StaffLogger(bot)
    logger.info("Staff logger initialized")
