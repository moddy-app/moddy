"""
Advanced Error Handling System for Moddy
Tracking, Discord logs, and notifications with database integration
"""

import discord
from discord import ui
from discord.ext import commands
import traceback
import hashlib
import json
from datetime import datetime, timezone
from typing import Optional, Dict, Any
import asyncio
import sys
from collections import deque
import os

from config import COLORS
import logging

logger = logging.getLogger('moddy.error_handler')

# Initialize Sentry integration
import sentry_sdk

# Only initialize Sentry if SENTRY_DSN is set in environment
SENTRY_DSN = os.getenv('SENTRY_DSN')
if SENTRY_DSN:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        # Add data like request headers and IP for users
        # see https://docs.sentry.io/platforms/python/data-management/data-collected/ for more info
        send_default_pii=True,
        # Set traces_sample_rate to 1.0 to capture 100% of transactions for performance monitoring.
        # Adjust this value in production.
        traces_sample_rate=0.1,  # 10% of transactions
        # Set profiles_sample_rate to 1.0 to profile 100% of sampled transactions.
        # Adjust this value in production.
        profiles_sample_rate=0.1,  # 10% of sampled transactions
    )
    logger.info("Sentry initialized successfully")
else:
    logger.warning("SENTRY_DSN not set - Sentry integration disabled")


def capture_error_to_sentry(error: Exception, context: Dict[str, Any] = None) -> Optional[str]:
    """
    Helper function to capture errors to Sentry with additional context.
    This runs in parallel to the existing error handling system.

    Returns:
        str: The Sentry event ID if capture was successful, None otherwise
    """
    if not SENTRY_DSN:
        return None  # Sentry not configured, skip

    try:
        # Set additional context if provided
        if context:
            with sentry_sdk.push_scope() as scope:
                # Add context information as extras
                for key, value in context.items():
                    scope.set_extra(key, value)

                # Add tags for better filtering in Sentry
                if 'error_code' in context:
                    scope.set_tag('error_code', context['error_code'])
                if 'command' in context:
                    scope.set_tag('command', context['command'])
                if 'guild_id' in context:
                    scope.set_tag('guild_id', str(context['guild_id']))
                if 'user_id' in context:
                    scope.set_tag('user_id', str(context['user_id']))
                if 'error_type' in context:
                    scope.set_tag('error_type', context['error_type'])

                # Capture the exception and get the event ID
                event_id = sentry_sdk.capture_exception(error)
                return event_id
        else:
            # Capture without additional context
            event_id = sentry_sdk.capture_exception(error)
            return event_id
    except Exception as sentry_error:
        # Don't let Sentry errors break the main error handling
        logger.error(f"Failed to capture error to Sentry: {sentry_error}")
        return None


async def fetch_sentry_issue_id(event_id: str, project_slug: str = "moddy") -> Optional[str]:
    """
    Fetch the Sentry Issue ID (group ID) from the Sentry API using the event ID.

    Args:
        event_id: The Sentry event ID
        project_slug: The Sentry project slug (default: "moddy")

    Returns:
        str: The Issue ID (group ID) if found, None otherwise
    """
    if not event_id or not SENTRY_DSN:
        return None

    sentry_api_token = os.getenv('SENTRY_API_TOKEN')
    if not sentry_api_token:
        logger.warning("SENTRY_API_TOKEN not set - cannot fetch issue ID")
        return None

    try:
        import aiohttp

        # API endpoint: /organizations/{org}/eventids/{event_id}/
        # Requires token with org:read, org:write, or org:admin scope
        url = f"https://sentry.io/api/0/organizations/moddy-0f/eventids/{event_id}/"

        headers = {
            "Authorization": f"Bearer {sentry_api_token}",
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    # According to Sentry API docs, groupId is at root level
                    issue_id = data.get('groupId')
                    if issue_id:
                        logger.info(f"Successfully fetched Sentry issue ID: {issue_id} for event {event_id}")
                        return str(issue_id)
                    else:
                        logger.warning(f"Sentry API returned 200 but no groupId found in response")
                        logger.debug(f"Response data: {data}")
                        return None
                elif response.status == 401:
                    # Unauthorized - token issue
                    error_text = await response.text()
                    logger.error(f"Sentry API authentication failed (401). Check SENTRY_API_TOKEN permissions.")
                    logger.error(f"Required scopes: org:read, org:write, or org:admin")
                    logger.debug(f"Response: {error_text[:500]}")
                    return None
                elif response.status == 403:
                    # Forbidden - permission issue
                    error_text = await response.text()
                    logger.error(f"Sentry API access forbidden (403). Token may not have required scope.")
                    logger.error(f"Required scopes: org:read, org:write, or org:admin")
                    logger.debug(f"Response: {error_text[:500]}")
                    return None
                elif response.status == 404:
                    # Event not found yet - might be too early
                    logger.info(f"Sentry event {event_id} not found yet (404). May need to wait longer.")
                    return None
                else:
                    # Other error
                    error_text = await response.text()
                    logger.warning(f"Failed to fetch Sentry issue ID: HTTP {response.status}")
                    logger.debug(f"URL: {url}")
                    logger.debug(f"Response: {error_text[:500]}")
                    return None

    except Exception as e:
        logger.error(f"Error fetching Sentry issue ID: {e}")
        return None


class BaseView(ui.LayoutView):
    """
    Base class for ALL UI Views with centralized error handling.

    ALL discord.ui Views MUST inherit from this class to ensure:
    1. Errors are caught and logged with FULL traceback in ONE log line
    2. User ALWAYS receives an error embed
    3. Error handler processes ALL exceptions

    Persistence
    -----------
    The default ``timeout`` is ``None`` — views never expire in memory.
    Subclasses that still want a timeout can pass ``timeout=<seconds>`` to
    ``super().__init__``; any numeric value overrides the default.

    To make a view survive a bot restart, set the class attribute
    ``__persistent__ = True`` and override :meth:`register_persistent`. The
    registration runs once in :func:`bot.setup_hook` via
    ``utils.persistent_views.register_all_persistent_views``. See
    ``docs/PERSISTENT_VIEWS.md`` for the full pattern.
    """

    # Override to True on subclasses that should be registered as persistent
    # at bot startup. Subclasses that set this to True MUST also implement
    # register_persistent(bot).
    __persistent__: bool = False

    def __init__(self, *, timeout: Optional[float] = None, **kwargs):
        # Default timeout=None so views never expire in memory, regardless of
        # whether they are registered as persistent. Subclasses can still
        # override by passing timeout=<seconds>.
        super().__init__(timeout=timeout, **kwargs)
        self.bot = None  # MUST be set by subclass

    @classmethod
    def register_persistent(cls, bot) -> None:
        """
        Register this view for persistence across bot restarts.

        Subclasses with ``__persistent__ = True`` MUST override this and
        typically call one of:

        - ``bot.add_view(cls())`` for a static shell instance
          (all children have stable ``custom_id``s and callbacks re-derive
          state from ``interaction``).
        - ``bot.add_dynamic_items(...)`` for ``discord.ui.DynamicItem``
          subclasses whose ``custom_id`` encodes state via a regex.

        See ``docs/PERSISTENT_VIEWS.md`` for the cookbook.
        """
        raise NotImplementedError(
            f"{cls.__name__}.__persistent__ is True but register_persistent() "
            f"is not implemented."
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        """
        Called when an error occurs in ANY UI component callback.
        This ensures ALL UI errors are caught and handled properly.
        NO EXCEPTIONS ESCAPE THIS HANDLER.
        """
        # Log the error with FULL traceback in ONE SINGLE log entry
        error_tb = ''.join(traceback.format_exception(type(error), error, error.__traceback__))
        compact_tb = error_tb.replace('\n', ' ⮐ ')
        logger.error(f"UI Error in {self.__class__.__name__} - Item: {item.__class__.__name__} - {compact_tb}")

        # Get bot - try self.bot first, then interaction.client
        bot = self.bot if self.bot else interaction.client

        # Get error tracker cog
        error_tracker = bot.get_cog('ErrorTracker') if bot else None

        if error_tracker:
            # Use centralized error handler
            error_code = error_tracker.generate_error_code(error)
            error_details = error_tracker.format_error_details(error)

            # Add UI context
            error_details.update({
                "command": f"UI:{self.__class__.__name__}",
                "user": f"{interaction.user} ({interaction.user.id})",
                "guild": f"{interaction.guild.name} ({interaction.guild.id})" if interaction.guild else "DM",
                "channel": f"#{interaction.channel.name}" if hasattr(interaction.channel, 'name') else "DM",
                "item": f"{item.__class__.__name__}"
            })

            # Capture to Sentry and get event ID
            sentry_event_id = capture_error_to_sentry(error, {
                'error_type': 'UI Error',
                'error_code': error_code,
                'view_class': self.__class__.__name__,
                'item_class': item.__class__.__name__,
                'user_id': interaction.user.id if interaction.user else None,
                'guild_id': interaction.guild.id if interaction.guild else None,
                'channel_id': interaction.channel.id if interaction.channel else None,
            })

            # Add Sentry event ID to error details
            if sentry_event_id:
                error_details['sentry_event_id'] = sentry_event_id

            # Store error
            error_tracker.store_error(error_code, error_details)
            await error_tracker.store_error_db(error_code, error_details)

            # Fetch Sentry issue ID asynchronously (don't wait for it)
            if sentry_event_id and bot.db:
                asyncio.create_task(error_tracker._update_sentry_issue_id(error_code, sentry_event_id))

            # Determine if fatal
            is_fatal = isinstance(error, (
                RuntimeError,
                AttributeError,
                ImportError,
                MemoryError,
                SystemError,
                ModuleNotFoundError
            ))

            # Log to Discord channel
            await error_tracker.send_error_log(error_code, error_details, is_fatal)

            # ALWAYS show error to user
            error_view = ErrorView(error_code)

            try:
                if interaction.response.is_done():
                    try:
                        await interaction.followup.send(view=error_view, ephemeral=True)
                    except:
                        try:
                            await interaction.edit_original_response(view=error_view)
                        except Exception as edit_error:
                            logger.error(f"Failed to edit response: {edit_error}")
                else:
                    await interaction.response.send_message(view=error_view, ephemeral=True)
            except Exception as send_error:
                logger.error(f"CRITICAL: Failed to send error view to user: {send_error}")
        else:
            # FALLBACK: No error tracker, but STILL show error to user
            logger.error(f"ERROR TRACKER NOT AVAILABLE - Falling back to basic error message")
            try:
                error_msg = f"❌ **An error occurred**\n\n`{type(error).__name__}: {str(error)}`\n\nThis error has been logged."

                if interaction.response.is_done():
                    await interaction.followup.send(error_msg, ephemeral=True)
                else:
                    await interaction.response.send_message(error_msg, ephemeral=True)
            except Exception as fallback_error:
                logger.error(f"CRITICAL: Failed to send even fallback error message: {fallback_error}")


class BaseModal(ui.Modal):
    """
    Base class for ALL UI Modals with centralized error handling.

    Note
    ----
    Modals cannot be registered as persistent — Discord treats a modal
    submission as a one-shot interaction that must be answered while the
    owning message's component store is still in memory. ``discord.ui.Modal``
    already defaults ``timeout`` to ``None``, so modals will not expire
    mid-edit as long as the bot process stays up.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bot = None  # MUST be set by subclass

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        """
        Called when an error occurs in a Modal submit.
        """
        # Log with FULL traceback in ONE line
        error_tb = ''.join(traceback.format_exception(type(error), error, error.__traceback__))
        compact_tb = error_tb.replace('\n', ' ⮐ ')
        logger.error(f"Modal Error in {self.__class__.__name__}: {compact_tb}")

        # Get bot - try self.bot first, then interaction.client
        bot = self.bot if self.bot else interaction.client

        error_tracker = bot.get_cog('ErrorTracker') if bot else None

        if error_tracker:
            error_code = error_tracker.generate_error_code(error)
            error_details = error_tracker.format_error_details(error)

            error_details.update({
                "command": f"Modal:{self.__class__.__name__}",
                "user": f"{interaction.user} ({interaction.user.id})",
                "guild": f"{interaction.guild.name} ({interaction.guild.id})" if interaction.guild else "DM",
                "channel": f"#{interaction.channel.name}" if hasattr(interaction.channel, 'name') else "DM"
            })

            # Capture to Sentry and get event ID
            sentry_event_id = capture_error_to_sentry(error, {
                'error_type': 'Modal Error',
                'error_code': error_code,
                'modal_class': self.__class__.__name__,
                'user_id': interaction.user.id if interaction.user else None,
                'guild_id': interaction.guild.id if interaction.guild else None,
                'channel_id': interaction.channel.id if interaction.channel else None,
            })

            # Add Sentry event ID to error details
            if sentry_event_id:
                error_details['sentry_event_id'] = sentry_event_id

            error_tracker.store_error(error_code, error_details)
            await error_tracker.store_error_db(error_code, error_details)

            # Fetch Sentry issue ID asynchronously (don't wait for it)
            if sentry_event_id and bot.db:
                asyncio.create_task(error_tracker._update_sentry_issue_id(error_code, sentry_event_id))

            is_fatal = isinstance(error, (
                RuntimeError,
                AttributeError,
                ImportError,
                MemoryError,
                SystemError,
                ModuleNotFoundError
            ))

            await error_tracker.send_error_log(error_code, error_details, is_fatal)

            error_view = ErrorView(error_code)

            try:
                if interaction.response.is_done():
                    try:
                        await interaction.followup.send(view=error_view, ephemeral=True)
                    except:
                        try:
                            await interaction.edit_original_response(view=error_view)
                        except:
                            pass
                else:
                    await interaction.response.send_message(view=error_view, ephemeral=True)
            except Exception as send_error:
                logger.error(f"CRITICAL: Failed to send error view to user: {send_error}")
        else:
            try:
                error_msg = f"❌ **An error occurred**\n\n`{type(error).__name__}: {str(error)}`\n\nThis error has been logged."

                if interaction.response.is_done():
                    await interaction.followup.send(error_msg, ephemeral=True)
                else:
                    await interaction.response.send_message(error_msg, ephemeral=True)
            except Exception as fallback_error:
                logger.error(f"CRITICAL: Failed to send fallback error message: {fallback_error}")


class ErrorView(ui.LayoutView):
    """Error display view using Components V2"""

    def __init__(self, error_code: str):
        super().__init__(timeout=None)
        self.error_code = error_code
        self.build_view()

    def build_view(self):
        """Builds the error view with Components V2"""
        # Create main container
        container = ui.Container()

        # Add error title with emoji
        container.add_item(
            ui.TextDisplay(f"### <:error:1444049460924776478> An Error Occurred")
        )

        # Add error message with code
        container.add_item(
            ui.TextDisplay(
                f"**Error Code:** `{self.error_code}`\n\n"
                "This error has been automatically logged and will be reviewed by our team.\n"
                "If the problem persists, please contact support with this error code."
            )
        )

        # Add button row with support link
        button_row = ui.ActionRow()
        support_btn = ui.Button(
            label="Support Server",
            style=discord.ButtonStyle.link,
            url="https://moddy.app/support"
        )
        button_row.add_item(support_btn)
        container.add_item(button_row)

        # Add container to view
        self.add_item(container)


class ErrorTracker(commands.Cog):
    """Error tracking and management system"""

    def __init__(self, bot):
        self.bot = bot
        self.error_cache = deque(maxlen=100)  # Keeps the last 100 errors in memory
        self.error_channel_id = 1392439223717724160
        self.dev_user_id = 1164597199594852395

    def generate_error_code(self, error: Exception, ctx: Optional[commands.Context] = None) -> str:
        """Generates a unique error code"""
        # Use the hash of the error + timestamp for uniqueness
        error_str = f"{type(error).__name__}:{str(error)}:{datetime.now().timestamp()}"
        hash_obj = hashlib.md5(error_str.encode())
        return hash_obj.hexdigest()[:8].upper()

    def store_error(self, error_code: str, error_data: Dict[str, Any]):
        """Stores the error in the memory cache"""
        self.error_cache.append({
            "code": error_code,
            "timestamp": datetime.now(timezone.utc),
            "data": error_data
        })

    async def store_error_db(self, error_code: str, error_data: Dict[str, Any], ctx: Optional[commands.Context] = None):
        """Stores the error in the database"""
        if not self.bot.db:
            return

        try:
            # Prepare data for the DB
            db_data = {
                "type": error_data.get("type"),
                "message": error_data.get("message"),
                "file": error_data.get("file"),
                "line": int(error_data.get("line")) if error_data.get("line", "").isdigit() else None,
                "traceback": error_data.get("traceback"),
                "user_id": None,
                "guild_id": None,
                "command": error_data.get("command"),
                "context": None,
                "sentry_event_id": error_data.get("sentry_event_id"),
                "sentry_issue_id": error_data.get("sentry_issue_id")
            }

            # Add context info if available
            if ctx:
                db_data["user_id"] = ctx.author.id
                db_data["guild_id"] = ctx.guild.id if ctx.guild else None
                db_data["context"] = json.dumps({
                    "channel": str(ctx.channel),
                    "message": ctx.message.content[:200] if hasattr(ctx, 'message') else None
                })

            # Store in the DB
            await self.bot.db.log_error(error_code, db_data)

        except Exception as e:
            import logging
            logger = logging.getLogger('moddy')
            logger.error(f"Error while storing in DB: {e}")

    async def _update_sentry_issue_id(self, error_code: str, sentry_event_id: str):
        """
        Fetch Sentry issue ID from the API and update the database.
        This runs asynchronously in the background.

        Retries up to 3 times with exponential backoff:
        - First attempt: after 5 seconds
        - Second attempt: after 10 seconds (5 + 5)
        - Third attempt: after 15 seconds (5 + 10)
        """
        max_retries = 3
        base_delay = 5  # Start with 5 seconds

        for attempt in range(max_retries):
            try:
                # Wait before attempting (exponential backoff)
                wait_time = base_delay * (attempt + 1)
                await asyncio.sleep(wait_time)

                logger.debug(f"Attempting to fetch Sentry issue ID for {error_code} (attempt {attempt + 1}/{max_retries}, waited {wait_time}s)")

                # Fetch the issue ID from Sentry API
                sentry_issue_id = await fetch_sentry_issue_id(sentry_event_id)

                if sentry_issue_id and self.bot.db:
                    # Success! Update the database with the issue ID
                    await self.bot.db.update_error_sentry_ids(
                        error_code,
                        sentry_issue_id=sentry_issue_id
                    )
                    logger.info(f"✅ Updated error {error_code} with Sentry issue ID: {sentry_issue_id} (attempt {attempt + 1})")
                    return  # Success, exit the retry loop
                elif attempt < max_retries - 1:
                    # No issue ID yet, but we have more retries
                    logger.debug(f"No issue ID yet for {error_code}, will retry...")
                    continue
                else:
                    # Last attempt failed
                    logger.warning(f"Failed to fetch Sentry issue ID for {error_code} after {max_retries} attempts")
                    return

            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(f"Error fetching Sentry issue ID for {error_code} (attempt {attempt + 1}): {e}, retrying...")
                else:
                    logger.error(f"Failed to update Sentry issue ID for error {error_code} after {max_retries} attempts: {e}")
                    return

    async def get_error_channel(self) -> Optional[discord.TextChannel]:
        """Gets the error channel"""
        return self.bot.get_channel(self.error_channel_id)

    def format_error_details(self, error: Exception, ctx: Optional[commands.Context] = None) -> Dict[str, Any]:
        """Formats the error details"""
        tb = traceback.format_exception(type(error), error, error.__traceback__)

        # Find the source file
        source_file = "Unknown"
        line_number = "?"
        for line in tb:
            if "File" in line and "site-packages" not in line:
                parts = line.strip().split('"')
                if len(parts) >= 2:
                    source_file = parts[1].split('/')[-1]
                    line_parts = line.split("line ")
                    if len(line_parts) >= 2:
                        line_number = line_parts[1].split(",")[0]
                    break

        details = {
            "type": type(error).__name__,
            "message": str(error),
            "file": source_file,
            "line": line_number,
            "traceback": ''.join(tb[-3:])  # Last 3 lines of the traceback
        }

        if ctx:
            details.update({
                "command": str(ctx.command) if ctx.command else "None",
                "user": f"{ctx.author} ({ctx.author.id})",
                "guild": f"{ctx.guild.name} ({ctx.guild.id})" if ctx.guild else "DM",
                "channel": f"#{ctx.channel.name}" if hasattr(ctx.channel, 'name') else "DM",
                "message": ctx.message.content[:100] + "..." if len(ctx.message.content) > 100 else ctx.message.content
            })

        return details

    async def send_error_log(self, error_code: str, error_details: Dict[str, Any], is_fatal: bool = False):
        """Sends the error log to the Discord channel"""
        channel = await self.get_error_channel()
        if not channel:
            return

        # Determine the color based on severity
        color = COLORS["error"] if is_fatal else COLORS["warning"]

        embed = discord.Embed(
            title=f"{'Fatal Error' if is_fatal else 'Error'} Detected",
            color=color,
            timestamp=datetime.now(timezone.utc)
        )

        # Header with the error code
        embed.add_field(
            name="Error Code",
            value=f"`{error_code}`",
            inline=True
        )

        embed.add_field(
            name="Type",
            value=f"`{error_details['type']}`",
            inline=True
        )

        embed.add_field(
            name="File",
            value=f"`{error_details['file']}:{error_details['line']}`",
            inline=True
        )

        # Error message
        embed.add_field(
            name="Message",
            value=f"```{error_details['message'][:500]}```",
            inline=False
        )

        # Context if available
        if 'command' in error_details:
            embed.add_field(
                name="Context",
                value=(
                    f"**Command:** `{error_details['command']}`\n"
                    f"**User:** {error_details['user']}\n"
                    f"**Server:** {error_details['guild']}\n"
                    f"**Channel:** {error_details['channel']}"
                ),
                inline=False
            )

            if 'message' in error_details:
                embed.add_field(
                    name="Original Message",
                    value=f"```{error_details['message']}```",
                    inline=False
                )

        # Traceback for fatal errors
        if is_fatal and 'traceback' in error_details:
            embed.add_field(
                name="Traceback",
                value=f"```py\n{error_details['traceback'][:500]}```",
                inline=False
            )

        # Note about the DB
        if self.bot.db:
            embed.set_footer(text="✅ Error saved to the database")
        else:
            embed.set_footer(text="⚠️ Database not connected - Error cached only")

        # Ping for fatal errors
        content = f"<@{self.dev_user_id}> Fatal error detected!" if is_fatal else None

        await channel.send(content=content, embed=embed)

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        """Handles command errors"""
        # Ignored errors (already handled)
        ignored = (
            commands.CommandNotFound,
            commands.NotOwner,
            commands.CheckFailure,
            commands.DisabledCommand,
            commands.NoPrivateMessage
        )

        if isinstance(error, ignored):
            return

        # Errors with specific handling (no log)
        if isinstance(error, commands.MissingPermissions):
            embed = discord.Embed(
                title="Insufficient Permissions",
                description=f"Missing permissions: `{', '.join(error.missing_permissions)}`",
                color=COLORS["error"]
            )
            await ctx.send(embed=embed)
            return

        if isinstance(error, commands.CommandOnCooldown):
            embed = discord.Embed(
                title="Cooldown Active",
                description=f"Try again in `{error.retry_after:.1f}` seconds",
                color=COLORS["warning"]
            )
            await ctx.send(embed=embed)
            return

        if isinstance(error, commands.MissingRequiredArgument):
            embed = discord.Embed(
                title="Missing Argument",
                description=f"The argument `{error.param.name}` is required",
                color=COLORS["error"]
            )
            await ctx.send(embed=embed)
            return

        # For all other errors, we log
        error_code = self.generate_error_code(error, ctx)
        error_details = self.format_error_details(error.original if hasattr(error, 'original') else error, ctx)

        # Capture to Sentry and get event ID
        actual_error = error.original if hasattr(error, 'original') else error
        sentry_event_id = capture_error_to_sentry(actual_error, {
            'error_type': 'Command Error',
            'error_code': error_code,
            'command': str(ctx.command) if ctx.command else 'None',
            'user_id': ctx.author.id if ctx.author else None,
            'guild_id': ctx.guild.id if ctx.guild else None,
            'channel_id': ctx.channel.id if ctx.channel else None,
        })

        # Add Sentry event ID to error details
        if sentry_event_id:
            error_details['sentry_event_id'] = sentry_event_id

        # Store the error in memory
        self.store_error(error_code, error_details)

        # Store in the DB if available
        await self.store_error_db(error_code, error_details, ctx)

        # Fetch Sentry issue ID asynchronously (don't wait for it)
        if sentry_event_id and self.bot.db:
            asyncio.create_task(self._update_sentry_issue_id(error_code, sentry_event_id))

        # Determine if it's fatal
        is_fatal = isinstance(error.original if hasattr(error, 'original') else error, (
            RuntimeError,
            AttributeError,
            ImportError,
            MemoryError,
            SystemError
        ))

        # Log to Discord
        await self.send_error_log(error_code, error_details, is_fatal)

        # Create error view with Components V2
        error_view = ErrorView(error_code)

        try:
            # For slash commands
            if hasattr(ctx, 'interaction') and ctx.interaction:
                if ctx.interaction.response.is_done():
                    # Try to send a followup message first (preferred)
                    try:
                        await ctx.interaction.followup.send(view=error_view, ephemeral=True)
                    except:
                        # If followup fails, edit the original response as fallback
                        await ctx.interaction.edit_original_response(content=None, view=error_view)
                else:
                    await ctx.interaction.response.send_message(view=error_view, ephemeral=True)
            else:
                # For text commands, send embed for compatibility
                embed = discord.Embed(color=COLORS["error"])
                await ctx.send(embed=embed, view=error_view)
        except Exception as send_error:
            # If we can't send in the channel, try DMs
            try:
                await ctx.author.send(view=error_view)
            except:
                # Last resort: log the failure
                import logging
                logger = logging.getLogger('moddy')
                logger.error(f"Failed to send error message to user: {send_error}")

    @commands.Cog.listener()
    async def on_error(self, event: str, *args, **kwargs):
        """Handles event errors (non-commands)"""
        # Get the actual exception from sys.exc_info()
        exc_type, exc_value, exc_traceback = sys.exc_info()

        if exc_value is None:
            return

        error_code = self.generate_error_code(exc_value)
        error_details = self.format_error_details(exc_value)

        error_details.update({
            "event": event,
            "context": f"Discord event: {event}"
        })

        # Capture to Sentry and get event ID
        sentry_event_id = capture_error_to_sentry(exc_value, {
            'error_type': 'Event Error',
            'error_code': error_code,
            'event': event,
        })

        # Add Sentry event ID to error details
        if sentry_event_id:
            error_details['sentry_event_id'] = sentry_event_id

        self.store_error(error_code, error_details)

        # Store in the DB if available
        if self.bot.db:
            await self.store_error_db(error_code, error_details)

        # Fetch Sentry issue ID asynchronously (don't wait for it)
        if sentry_event_id and self.bot.db:
            asyncio.create_task(self._update_sentry_issue_id(error_code, sentry_event_id))

        await self.send_error_log(error_code, error_details, is_fatal=True)

    @commands.Cog.listener()
    async def on_app_command_error(self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
        """Handles slash command (app command) errors"""
        # Check if the error was already handled
        if hasattr(error, '__error_handled__'):
            return

        # Mark error as handled to prevent duplicate processing
        error.__error_handled__ = True

        # Ignored errors
        if isinstance(error, discord.app_commands.CommandNotFound):
            return

        # Errors with specific handling
        if isinstance(error, discord.app_commands.MissingPermissions):
            # Create inline view for permissions error
            class PermissionErrorView(ui.LayoutView):
                def __init__(self):
                    super().__init__(timeout=None)
                    container = ui.Container()
                    container.add_item(
                        ui.TextDisplay(f"### <:error:1444049460924776478> Insufficient Permissions")
                    )
                    container.add_item(
                        ui.TextDisplay("You don't have the necessary permissions to execute this command.")
                    )
                    button_row = ui.ActionRow()
                    support_btn = ui.Button(
                        label="Support Server",
                        style=discord.ButtonStyle.link,
                        url="https://moddy.app/support"
                    )
                    button_row.add_item(support_btn)
                    container.add_item(button_row)
                    self.add_item(container)

            try:
                if interaction.response.is_done():
                    await interaction.followup.send(view=PermissionErrorView(), ephemeral=True)
                else:
                    await interaction.response.send_message(view=PermissionErrorView(), ephemeral=True)
            except:
                pass
            return

        if isinstance(error, discord.app_commands.CommandOnCooldown):
            # Create inline view for cooldown error
            class CooldownErrorView(ui.LayoutView):
                def __init__(self, retry_after: float):
                    super().__init__(timeout=None)
                    container = ui.Container()
                    container.add_item(
                        ui.TextDisplay(f"### ⏱️ Cooldown Active")
                    )
                    container.add_item(
                        ui.TextDisplay(f"Please try again in `{retry_after:.1f}` seconds.")
                    )
                    self.add_item(container)

            try:
                if interaction.response.is_done():
                    await interaction.followup.send(view=CooldownErrorView(error.retry_after), ephemeral=True)
                else:
                    await interaction.response.send_message(view=CooldownErrorView(error.retry_after), ephemeral=True)
            except:
                pass
            return

        # For all other errors, log them
        actual_error = error.original if hasattr(error, 'original') else error
        error_code = self.generate_error_code(actual_error)
        error_details = self.format_error_details(actual_error)

        # Add interaction context
        error_details.update({
            "command": interaction.command.name if interaction.command else "Unknown",
            "user": f"{interaction.user} ({interaction.user.id})",
            "guild": f"{interaction.guild.name} ({interaction.guild.id})" if interaction.guild else "DM",
            "channel": f"#{interaction.channel.name}" if hasattr(interaction.channel, 'name') else "DM"
        })

        # Capture to Sentry and get event ID
        sentry_event_id = capture_error_to_sentry(actual_error, {
            'error_type': 'App Command Error',
            'error_code': error_code,
            'command': interaction.command.name if interaction.command else 'Unknown',
            'user_id': interaction.user.id if interaction.user else None,
            'guild_id': interaction.guild.id if interaction.guild else None,
            'channel_id': interaction.channel.id if interaction.channel else None,
        })

        # Add Sentry event ID to error details
        if sentry_event_id:
            error_details['sentry_event_id'] = sentry_event_id

        # Store error
        self.store_error(error_code, error_details)
        await self.store_error_db(error_code, error_details)

        # Fetch Sentry issue ID asynchronously (don't wait for it)
        if sentry_event_id and self.bot.db:
            asyncio.create_task(self._update_sentry_issue_id(error_code, sentry_event_id))

        # Determine if it's fatal
        is_fatal = isinstance(actual_error, (
            RuntimeError,
            AttributeError,
            ImportError,
            MemoryError,
            SystemError
        ))

        # Log to Discord
        await self.send_error_log(error_code, error_details, is_fatal)

        # Send error to user with Components V2 (no embed needed)
        error_view = ErrorView(error_code)

        try:
            if interaction.response.is_done():
                # Try to send a followup message first (preferred)
                try:
                    await interaction.followup.send(view=error_view, ephemeral=True)
                except:
                    # If followup fails, edit the original response as fallback
                    await interaction.edit_original_response(content=None, view=error_view)
            else:
                # Send the error as the initial response
                await interaction.response.send_message(view=error_view, ephemeral=True)
        except Exception as send_error:
            # Last resort: log the failure
            import logging
            logger = logging.getLogger('moddy')
            logger.error(f"Failed to send app command error to user: {send_error}")


async def setup(bot):
    await bot.add_cog(ErrorTracker(bot))