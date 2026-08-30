"""
Support Commands (sup. prefix)
Commands for support staff (Manager, Supervisor_Sup, Support)
"""

import discord
from discord.ext import commands
from discord import ui
from typing import Optional
import logging
from datetime import datetime, timezone
import re

from utils.staff_permissions import staff_permissions, CommandType
from database import db
from config import COLORS
from utils.components_v2 import create_error_message, create_info_message, create_success_message
from utils.emojis import (
    EMOJIS, SUPPORT, PREMIUM, BALANCE, RED_STATUS, WARNING,
    DOWNLOAD, INFO, DONE, UNDONE, GREEN_STATUS, YELLOW_STATUS
)
from utils.staff_logger import staff_logger
from staff.base import StaffCommandsCog
from cogs.error_handler import ErrorView, report_error

logger = logging.getLogger('moddy.support_commands')


class SupportCommands(StaffCommandsCog):
    """Support commands (sup. prefix)"""

    def __init__(self, bot):
        super().__init__(bot)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen for support commands with new syntax"""
        if message.author.bot:
            return

        if not staff_permissions or not db:
            return

        parsed = staff_permissions.parse_staff_command(message.content)
        if not parsed:
            return

        command_type, command_name, args = parsed

        if command_type != CommandType.SUPPORT:
            return

        allowed, reason = await staff_permissions.check_command_permission(
            message.author.id, command_type, command_name
        )

        if not allowed:
            view = create_error_message("Permission Denied", reason)
            await self.reply_with_tracking(message, view)
            return

        # A message listener has no global error handler behind it: an
        # unexpected failure here would vanish into discord.py's dispatch log
        # and leave the author with no answer at all. Route it through the same
        # central pipeline every other command uses, and show the error code.
        try:
            if command_name == "help":
                await self.handle_help_command(message, args)
            elif command_name == "subscription":
                await self.handle_subscription_command(message, args)
            else:
                view = create_error_message(
                    "Unknown Command",
                    f"Support command `{command_name}` not found.\n\nUse `sup.help` to see available commands."
                )
                await self.reply_with_tracking(message, view)
        except Exception as exc:
            await self._report(exc, message, command_name)

    async def _report(self, exc: Exception, message: discord.Message, command_name: str):
        """Central error pipeline for this legacy cog (no framework behind it)."""
        error_code = await report_error(
            self.bot, exc, source=f"Staff:sup.{command_name}",
            user=message.author, guild=message.guild, channel=message.channel,
            error_type="Staff Command Error",
        )
        view = ErrorView(error_code) if error_code else create_error_message(
            "Error", "An unexpected error occurred."
        )
        try:
            await self.reply_with_tracking(message, view)
        except discord.HTTPException as send_error:
            logger.error("CRITICAL: could not show the error card for sup.%s: %s",
                         command_name, send_error)

    async def handle_help_command(self, message: discord.Message, args: str):
        """
        Handle sup.help command
        Usage: <@bot> sup.help
        """
        if staff_logger:
            await staff_logger.log_command("sup", "help", message.author)

        perms = await staff_permissions.get_user_permissions(message.author.id)
        role_perms = perms.get("role_permissions", {}) if perms else {}

        can_view = staff_permissions.has_permission(role_perms, "subscription_view")

        container = ui.Container()
        container.add_item(ui.TextDisplay(f"### {SUPPORT} Support Commands"))
        container.add_item(ui.TextDisplay("Available support commands based on your permissions."))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        if can_view:
            container.add_item(ui.TextDisplay(
                f"**Subscription Management** {PREMIUM}\n"
                "-# `sup.subscription @user` - View user's Premium status"
            ))
        else:
            container.add_item(ui.TextDisplay(
                "No subscription commands available.\n"
                "-# Contact a manager to request permissions."
            ))

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(ui.TextDisplay(
            f"{INFO} **Note:** Invoice and refund management is available on the dashboard."
        ))

        view = ui.LayoutView()
        view.add_item(container)
        await self.reply_with_tracking(message, view)

    def _extract_user_id(self, args: str, message: discord.Message) -> Optional[int]:
        """Extract user ID from arguments (mention or direct ID)"""
        if not args:
            return None

        args = args.replace(f"<@{self.bot.user.id}>", "").strip()

        user_match = re.search(r'<@!?(\d+)>', args)
        if user_match:
            return int(user_match.group(1))

        parts = args.split()
        if parts:
            try:
                return int(parts[0])
            except ValueError:
                pass

        return None

    async def handle_subscription_command(self, message: discord.Message, args: str):
        """
        Handle sup.subscription command — view user Premium status from DB
        Usage: <@bot> sup.subscription @user
        Requires: subscription_view permission
        """
        user_id = self._extract_user_id(args, message)
        if not user_id:
            view = create_error_message(
                "Invalid User",
                "Please mention a user or provide a valid user ID.\n\n"
                "**Usage:** `sup.subscription @user` or `sup.subscription [user_id]`"
            )
            await self.reply_with_tracking(message, view)
            return

        if staff_logger:
            # `log_command` takes `args`/`target_user`, never a `target_user_id`:
            # the old call raised a TypeError before the command even ran.
            await staff_logger.log_command("sup", "subscription", message.author,
                                           args=f"user={user_id}")

        try:
            try:
                user = await self.bot.fetch_user(user_id)
            except discord.NotFound:
                view = create_error_message("User Not Found", f"User with ID `{user_id}` not found on Discord.")
                await self.reply_with_tracking(message, view)
                return

            # Read Premium status and stripe_customer_id directly from DB
            bot_db = self.bot.db
            if not bot_db:
                view = create_error_message("Database Unavailable", "Cannot access database at this time.")
                await self.reply_with_tracking(message, view)
                return

            is_premium = await bot_db.has_attribute('user', user_id, 'PREMIUM')
            user_data = await bot_db.get_user(user_id)
            stripe_customer_id = user_data.get('stripe_customer_id') if user_data else None

            status_emoji = GREEN_STATUS if is_premium else RED_STATUS
            status_label = "Active Premium" if is_premium else "No Premium"

            container = ui.Container()
            container.add_item(ui.TextDisplay(f"### {PREMIUM} Subscription Information"))
            container.add_item(ui.TextDisplay(f"User: {user.mention} (`{user_id}`)"))
            container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
            container.add_item(ui.TextDisplay(
                f"**Status:** {status_emoji} {status_label}\n"
                + (f"**Stripe Customer:** `{stripe_customer_id}`" if stripe_customer_id else
                   "-# No Stripe customer linked")
            ))

            if is_premium or stripe_customer_id:
                container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
                container.add_item(ui.TextDisplay(
                    f"{INFO} For invoices and billing details, check the dashboard."
                ))

            view = ui.LayoutView()
            view.add_item(container)
            await self.reply_with_tracking(message, view)

        except Exception as exc:
            # An error code, not a bare "an unexpected error occurred": without
            # one neither the user nor the team can trace what happened.
            await self._report(exc, message, "subscription")
            if staff_logger:
                await staff_logger.log_command(
                    "sup", "subscription", message.author,
                    args=f"user={user_id}", success=False, error_message=str(exc),
                )


async def setup(bot):
    await bot.add_cog(SupportCommands(bot))
