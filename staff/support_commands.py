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
from services import get_backend_client, BackendClientError

logger = logging.getLogger('moddy.support_commands')


class SupportCommands(StaffCommandsCog):
    """Support commands (sup. prefix)"""

    def __init__(self, bot):
        super().__init__(bot)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen for support commands with new syntax"""
        # Ignore bots
        if message.author.bot:
            return

        # Check if staff permissions system is ready
        if not staff_permissions or not db:
            return

        # Parse command
        parsed = staff_permissions.parse_staff_command(message.content)
        if not parsed:
            return

        command_type, command_name, args = parsed

        # Only handle support commands in this cog
        if command_type != CommandType.SUPPORT:
            return

        # Check permissions
        allowed, reason = await staff_permissions.check_command_permission(
            message.author.id, command_type, command_name
        )

        if not allowed:
            view = create_error_message("Permission Denied", reason)
            await self.reply_with_tracking(message, view)
            return

        # Route to appropriate command
        if command_name == "help":
            await self.handle_help_command(message, args)
        elif command_name == "subscription":
            await self.handle_subscription_command(message, args)
        elif command_name == "invoices":
            await self.handle_invoices_command(message, args)
        elif command_name == "refund":
            await self.handle_refund_command(message, args)
        else:
            view = create_error_message(
                "Unknown Command",
                f"Support command `{command_name}` not found.\n\nUse `sup.help` to see available commands."
            )
            await self.reply_with_tracking(message, view)

    async def handle_help_command(self, message: discord.Message, args: str):
        """
        Handle sup.help command - Show available support commands
        Usage: <@1373916203814490194> sup.help
        """
        # Log the command
        if staff_logger:
            await staff_logger.log_command("sup", "help", message.author)

        # Get user's permissions to show only available commands
        perms = await staff_permissions.get_user_permissions(message.author.id)
        role_perms = perms.get("role_permissions", {}) if perms else {}

        # Check which commands the user can access
        can_view = staff_permissions.has_permission(role_perms, "subscription_view")
        can_manage = staff_permissions.has_permission(role_perms, "subscription_manage")

        # Build help message
        container = ui.Container()
        container.add_item(ui.TextDisplay(
            f"### {SUPPORT} Support Commands"
        ))
        container.add_item(ui.TextDisplay(
            "Available support commands based on your permissions."
        ))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        if can_view:
            container.add_item(ui.TextDisplay(
                f"**Subscription Management** {PREMIUM}\n"
                "-# `sup.subscription @user` - View user's subscription details\n"
                "-# `sup.invoices @user [limit]` - View user's payment invoices"
            ))

        if can_manage:
            container.add_item(ui.TextDisplay(
                f"**Payment Management** {BALANCE}\n"
                "-# `sup.refund @user [amount] [reason]` - Refund a payment"
            ))

        if not can_view and not can_manage:
            container.add_item(ui.TextDisplay(
                "No subscription commands available.\n"
                "-# Contact a manager to request permissions."
            ))

        view = ui.LayoutView()
        view.add_item(container)

        await self.reply_with_tracking(message, view)

    def _extract_user_id(self, args: str, message: discord.Message) -> Optional[int]:
        """Extract user ID from arguments (mention or direct ID)"""
        if not args:
            return None

        # Remove bot mention if present
        args = args.replace(f"<@{self.bot.user.id}>", "").strip()

        # Try to extract user mention
        user_match = re.search(r'<@!?(\d+)>', args)
        if user_match:
            return int(user_match.group(1))

        # Try to parse as direct ID
        parts = args.split()
        if parts:
            try:
                return int(parts[0])
            except ValueError:
                pass

        return None

    async def handle_subscription_command(self, message: discord.Message, args: str):
        """
        Handle sup.subscription command - View user subscription
        Usage: <@1373916203814490194> sup.subscription @user
        Usage: <@1373916203814490194> sup.subscription [user_id]
        Requires: subscription_view permission
        """
        # Extract user ID
        user_id = self._extract_user_id(args, message)
        if not user_id:
            view = create_error_message(
                "Invalid User",
                "Please mention a user or provide a valid user ID.\n\n"
                "**Usage:** `sup.subscription @user` or `sup.subscription [user_id]`"
            )
            await self.reply_with_tracking(message, view)
            return

        # Log the command
        if staff_logger:
            await staff_logger.log_command(
                "sup", "subscription", message.author,
                target_user_id=user_id
            )

        try:
            # Fetch user from Discord
            try:
                user = await self.bot.fetch_user(user_id)
            except discord.NotFound:
                view = create_error_message(
                    "User Not Found",
                    f"User with ID `{user_id}` not found on Discord."
                )
                await self.reply_with_tracking(message, view)
                return

            # Get subscription info from backend
            backend_client = get_backend_client()
            subscription_data = await backend_client.get_subscription_info(str(user_id))

            # Build response with Components V2
            container = ui.Container()
            container.add_item(ui.TextDisplay(
                f"### {PREMIUM} Subscription Information"
            ))
            container.add_item(ui.TextDisplay(
                f"User: {user.mention} ({user})"
            ))
            container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

            if not subscription_data.get("has_subscription"):
                container.add_item(ui.TextDisplay(
                    f"{RED_STATUS} **No Active Subscription**\n"
                    "-# This user does not have an active subscription."
                ))
            else:
                sub = subscription_data["subscription"]
                status_emoji = self._get_status_emoji(sub["status"])
                amount_euros = sub["amount"] / 100
                subscription_type = "Yearly" if sub["subscription_type"] == "yearly" else "Monthly"

                container.add_item(ui.TextDisplay(
                    f"**Status:** {status_emoji} {sub['status'].capitalize()}\n"
                    f"**Plan:** {subscription_type}\n"
                    f"**Price:** {amount_euros}€ / {sub['subscription_type']}\n"
                    f"**Customer ID:** `{sub['customer_id']}`\n"
                    f"**Subscription ID:** `{sub['subscription_id']}`"
                ))
                container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
                container.add_item(ui.TextDisplay(
                    f"**Current Period:**\n"
                    f"-# Start: {self._format_date(sub['current_period_start'])}\n"
                    f"-# End: {self._format_date(sub['current_period_end'])}"
                ))

                if sub.get("cancel_at_period_end"):
                    container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
                    container.add_item(ui.TextDisplay(
                        f"{WARNING} **Cancellation Scheduled**\n"
                        f"-# Subscription will end on {self._format_date(sub['current_period_end'])}"
                    ))

            view = ui.LayoutView()
            view.add_item(container)
            await self.reply_with_tracking(message, view)

        except BackendClientError as e:
            logger.error(f"Backend error in sup.subscription: {e}", exc_info=True)
            view = create_error_message(
                "Backend Error",
                "Failed to retrieve subscription information from backend.\n\n"
                f"-# Error: {str(e)}"
            )
            await self.reply_with_tracking(message, view)
            if staff_logger:
                await staff_logger.log_command(
                    "sup", "subscription", message.author,
                    target_user_id=user_id, success=False, error=str(e)
                )
        except Exception as e:
            logger.error(f"Unexpected error in sup.subscription: {e}", exc_info=True)
            view = create_error_message(
                "Error",
                "An unexpected error occurred while retrieving subscription information."
            )
            await self.reply_with_tracking(message, view)
            if staff_logger:
                await staff_logger.log_command(
                    "sup", "subscription", message.author,
                    target_user_id=user_id, success=False, error=str(e)
                )

    async def handle_invoices_command(self, message: discord.Message, args: str):
        """
        Handle sup.invoices command - View user invoices
        Usage: <@1373916203814490194> sup.invoices @user [limit]
        Requires: subscription_view permission
        """
        # Extract user ID
        user_id = self._extract_user_id(args, message)
        if not user_id:
            view = create_error_message(
                "Invalid User",
                "Please mention a user or provide a valid user ID.\n\n"
                "**Usage:** `sup.invoices @user [limit]`"
            )
            await self.reply_with_tracking(message, view)
            return

        # Extract limit if provided
        limit = 10
        try:
            parts = args.split()
            if len(parts) > 1:
                limit = int(parts[1])
                limit = max(1, min(limit, 50))  # Limit between 1 and 50
        except ValueError:
            pass

        # Log the command
        if staff_logger:
            await staff_logger.log_command(
                "sup", "invoices", message.author,
                target_user_id=user_id, args=f"limit={limit}"
            )

        try:
            # Fetch user from Discord
            try:
                user = await self.bot.fetch_user(user_id)
            except discord.NotFound:
                view = create_error_message(
                    "User Not Found",
                    f"User with ID `{user_id}` not found on Discord."
                )
                await self.reply_with_tracking(message, view)
                return

            # Get invoices from backend
            backend_client = get_backend_client()
            invoices_data = await backend_client.get_subscription_invoices(str(user_id), limit=limit)

            # Build response
            container = ui.Container()
            container.add_item(ui.TextDisplay(
                f"### {DOWNLOAD} Payment Invoices"
            ))
            container.add_item(ui.TextDisplay(
                f"User: {user.mention} ({user})"
            ))
            container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

            invoices = invoices_data.get("invoices", [])
            if not invoices:
                container.add_item(ui.TextDisplay(
                    f"{INFO} **No Invoices Found**\n"
                    "-# This user has no payment invoices."
                ))
            else:
                container.add_item(ui.TextDisplay(
                    f"**Total Invoices:** {len(invoices)} (showing last {limit})"
                ))
                container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

                for i, invoice in enumerate(invoices[:10], 1):  # Show max 10 in display
                    amount = invoice["amount"] / 100
                    status_emoji = DONE if invoice["status"] == "paid" else UNDONE
                    pdf_link = f"[PDF]({invoice['invoice_pdf']})" if invoice.get("invoice_pdf") else "N/A"

                    container.add_item(ui.TextDisplay(
                        f"**Invoice {i}:** `{invoice['invoice_id']}`\n"
                        f"-# {status_emoji} {invoice['status'].capitalize()} | "
                        f"{amount}€ {invoice['currency'].upper()} | "
                        f"{self._format_date(invoice['created'])} | {pdf_link}"
                    ))

            view = ui.LayoutView()
            view.add_item(container)
            await self.reply_with_tracking(message, view)

        except BackendClientError as e:
            logger.error(f"Backend error in sup.invoices: {e}", exc_info=True)
            view = create_error_message(
                "Backend Error",
                "Failed to retrieve invoices from backend.\n\n"
                f"-# Error: {str(e)}"
            )
            await self.reply_with_tracking(message, view)
            if staff_logger:
                await staff_logger.log_command(
                    "sup", "invoices", message.author,
                    target_user_id=user_id, success=False, error=str(e)
                )
        except Exception as e:
            logger.error(f"Unexpected error in sup.invoices: {e}", exc_info=True)
            view = create_error_message(
                "Error",
                "An unexpected error occurred while retrieving invoices."
            )
            await self.reply_with_tracking(message, view)
            if staff_logger:
                await staff_logger.log_command(
                    "sup", "invoices", message.author,
                    target_user_id=user_id, success=False, error=str(e)
                )

    async def handle_refund_command(self, message: discord.Message, args: str):
        """
        Handle sup.refund command - Refund a payment
        Usage: <@1373916203814490194> sup.refund @user [amount] [reason]
        Usage: <@1373916203814490194> sup.refund [user_id] [amount] [reason]
        Requires: subscription_manage permission

        Amount in cents (e.g., 5000 for 50€). Omit for full refund.
        """
        # Extract user ID
        user_id = self._extract_user_id(args, message)
        if not user_id:
            view = create_error_message(
                "Invalid User",
                "Please mention a user or provide a valid user ID.\n\n"
                "**Usage:** `sup.refund @user [amount] [reason]`\n"
                "-# Amount in cents (e.g., 5000 for 50€). Omit for full refund."
            )
            await self.reply_with_tracking(message, view)
            return

        # Extract amount and reason
        parts = args.split(maxsplit=2)
        amount = None
        reason = None

        # Try to parse amount (second argument)
        if len(parts) > 1:
            try:
                # Skip user ID/mention
                amount_str = parts[1]
                if not amount_str.startswith('<@'):  # Not a mention
                    amount = int(amount_str)
                    if amount <= 0:
                        view = create_error_message(
                            "Invalid Amount",
                            "Amount must be a positive number in cents.\n\n"
                            "**Example:** `sup.refund @user 5000 Service issue` (50€ refund)"
                        )
                        await self.reply_with_tracking(message, view)
                        return
            except ValueError:
                # Maybe it's the reason without amount
                reason = ' '.join(parts[1:])
                amount = None

        # Extract reason (third argument or rest if no amount)
        if len(parts) > 2 and amount is not None:
            reason = parts[2]

        # Log the command
        if staff_logger:
            await staff_logger.log_command(
                "sup", "refund", message.author,
                target_user_id=user_id,
                args=f"amount={amount or 'full'}, reason={reason or 'No reason provided'}"
            )

        try:
            # Fetch user from Discord
            try:
                user = await self.bot.fetch_user(user_id)
            except discord.NotFound:
                view = create_error_message(
                    "User Not Found",
                    f"User with ID `{user_id}` not found on Discord."
                )
                await self.reply_with_tracking(message, view)
                return

            # Process refund via backend
            backend_client = get_backend_client()
            refund_data = await backend_client.refund_payment(
                str(user_id),
                amount=amount,
                reason=reason
            )

            # Build response
            if refund_data.get("refunded"):
                amount_euros = refund_data.get("amount_refunded", 0) / 100

                container = ui.Container()
                container.add_item(ui.TextDisplay(
                    f"### {DONE} Refund Processed"
                ))
                container.add_item(ui.TextDisplay(
                    f"User: {user.mention} ({user})"
                ))
                container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
                container.add_item(ui.TextDisplay(
                    f"**Amount Refunded:** {amount_euros}€\n"
                    f"**Refund ID:** `{refund_data.get('refund_id', 'N/A')}`\n"
                    f"**Reason:** {reason or 'No reason provided'}\n"
                    f"**Processed by:** {message.author.mention}"
                ))

                view = ui.LayoutView()
                view.add_item(container)
                await self.reply_with_tracking(message, view)
            else:
                view = create_error_message(
                    "Refund Failed",
                    f"{refund_data.get('message', 'Unknown error occurred')}\n\n"
                    "-# The refund could not be processed. Check the error message above."
                )
                await self.reply_with_tracking(message, view)
                if staff_logger:
                    await staff_logger.log_command(
                        "sup", "refund", message.author,
                        target_user_id=user_id, success=False,
                        error=refund_data.get('message')
                    )

        except BackendClientError as e:
            logger.error(f"Backend error in sup.refund: {e}", exc_info=True)
            view = create_error_message(
                "Backend Error",
                "Failed to process refund via backend.\n\n"
                f"-# Error: {str(e)}"
            )
            await self.reply_with_tracking(message, view)
            if staff_logger:
                await staff_logger.log_command(
                    "sup", "refund", message.author,
                    target_user_id=user_id, success=False, error=str(e)
                )
        except Exception as e:
            logger.error(f"Unexpected error in sup.refund: {e}", exc_info=True)
            view = create_error_message(
                "Error",
                "An unexpected error occurred while processing the refund."
            )
            await self.reply_with_tracking(message, view)
            if staff_logger:
                await staff_logger.log_command(
                    "sup", "refund", message.author,
                    target_user_id=user_id, success=False, error=str(e)
                )

    def _get_status_emoji(self, status: str) -> str:
        """Get status emoji based on subscription status"""
        status_emojis = {
            "active": GREEN_STATUS,
            "canceled": RED_STATUS,
            "trialing": YELLOW_STATUS,
            "past_due": WARNING,
            "incomplete": YELLOW_STATUS,
            "unpaid": RED_STATUS,
        }
        return status_emojis.get(status, INFO)

    def _format_date(self, iso_date: str) -> str:
        """Format ISO 8601 date to readable format"""
        try:
            dt = datetime.fromisoformat(iso_date.replace('Z', '+00:00'))
            return dt.strftime("%B %d, %Y")
        except Exception as e:
            logger.error(f"Error formatting date {iso_date}: {e}")
            return iso_date


async def setup(bot):
    await bot.add_cog(SupportCommands(bot))
