"""
Developer Commands (d. prefix)
Commands exclusively for developers from Discord Dev Portal
"""

import discord
from discord.ext import commands
from discord import ui
from typing import Optional
import logging
from datetime import datetime, timezone
import sys
import os
import random
import string

from utils.staff_permissions import staff_permissions, CommandType
from database import db
from config import COLORS
from utils.components_v2 import create_error_message, create_success_message, create_info_message, create_warning_message
from utils.emojis import EMOJIS
from utils.staff_logger import staff_logger
from staff.base import StaffCommandsCog
from utils.announcement_setup import setup_announcement_channel
from cogs.error_handler import BaseView

logger = logging.getLogger('moddy.dev_commands')


class ServerListView(BaseView):
    """
    Pagination view for server list display
    """

    def __init__(self, bot, guilds: list, page: int = 0, per_page: int = 10):
        super().__init__(timeout=300)
        self.bot = bot
        self.guilds = guilds
        self.page = page
        self.per_page = per_page
        self.total_pages = (len(guilds) - 1) // per_page + 1 if guilds else 1

        self._build_view()

    def _build_view(self):
        """Build the view with current page data"""
        self.clear_items()

        container = ui.Container()

        # Calculate pagination
        start_idx = self.page * self.per_page
        end_idx = min(start_idx + self.per_page, len(self.guilds))
        page_guilds = self.guilds[start_idx:end_idx]

        # Header with total count and page info (using ### format as per DESIGN.md)
        header = f"### {EMOJIS['web']} Server List\n**Total Servers:** `{len(self.guilds):,}`\n-# Page {self.page + 1}/{self.total_pages}"
        container.add_item(ui.TextDisplay(header))

        if not page_guilds:
            container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
            container.add_item(ui.TextDisplay("*No servers found.*"))
        else:
            container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

            # Display each server
            for guild in page_guilds:
                # Get guild features
                features = []
                if "COMMUNITY" in guild.features:
                    features.append("Community")
                if "VERIFIED" in guild.features:
                    features.append("Verified")
                if "PARTNERED" in guild.features:
                    features.append("Partner")

                # Build server info
                joined_at = guild.me.joined_at if guild.me else None
                joined_str = f"<t:{int(joined_at.timestamp())}:d>" if joined_at else "Unknown"

                server_info = f"**{guild.name}**\n"
                server_info += f"-# ID: `{guild.id}` • Joined: {joined_str} • Members: `{guild.member_count:,}`"
                if features:
                    server_info += f" • {', '.join(features)}"

                container.add_item(ui.TextDisplay(server_info))

        self.add_item(container)

        # Add navigation buttons if there are multiple pages
        if self.total_pages > 1:
            button_row = ui.ActionRow()

            # Previous button
            prev_button = ui.Button(
                label="Previous",
                style=discord.ButtonStyle.secondary,
                disabled=(self.page == 0),
                emoji=discord.PartialEmoji.from_str(EMOJIS['back'])
            )
            prev_button.callback = self.on_previous
            button_row.add_item(prev_button)

            # Next button
            next_button = ui.Button(
                label="Next",
                style=discord.ButtonStyle.secondary,
                disabled=(self.page >= self.total_pages - 1),
                emoji=discord.PartialEmoji.from_str(EMOJIS['next'])
            )
            next_button.callback = self.on_next
            button_row.add_item(next_button)

            self.add_item(button_row)

    async def on_previous(self, interaction: discord.Interaction):
        """Handle previous page button"""
        if self.page > 0:
            self.page -= 1
            self._build_view()
            await interaction.response.edit_message(view=self)
        else:
            await interaction.response.defer()

    async def on_next(self, interaction: discord.Interaction):
        """Handle next page button"""
        if self.page < self.total_pages - 1:
            self.page += 1
            self._build_view()
            await interaction.response.edit_message(view=self)
        else:
            await interaction.response.defer()


class DeveloperCommands(StaffCommandsCog):
    """Developer commands (d. prefix)"""

    def __init__(self, bot):
        super().__init__(bot)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen for developer commands with new syntax"""
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

        # Only handle dev commands in this cog
        if command_type != CommandType.DEV:
            return

        # Defer to the new staff framework for commands that have been migrated
        # there (avoids double-dispatch during the staff-commands redesign).
        router = self.bot.get_cog("StaffCommandsRouter")
        if router and hasattr(router, "is_migrated") and router.is_migrated(command_type.value, command_name):
            return

        # Log the command attempt
        logger.info(f"🔧 Dev command '{command_name}' attempted by {message.author} ({message.author.id})")

        # Check if user is in dev team
        is_dev = self.bot.is_developer(message.author.id)
        logger.info(f"   Developer status: {is_dev}")

        # Check permissions
        allowed, reason = await staff_permissions.check_command_permission(
            message.author.id, command_type, command_name
        )

        if not allowed:
            logger.warning(f"   ❌ Permission denied: {reason}")
            view = create_error_message("Permission Denied", reason)
            await self.reply_with_tracking(message, view)
            return

        logger.info(f"   ✅ Permission granted")

        # Route to appropriate command
        if command_name == "reload":
            await self.handle_reload_command(message, args)
        elif command_name == "shutdown":
            await self.handle_shutdown_command(message, args)
        elif command_name == "stats":
            await self.handle_stats_command(message, args)
        elif command_name == "sql":
            await self.handle_sql_command(message, args)
        elif command_name == "jsk":
            await self.handle_jsk_command(message, args)
        elif command_name == "error":
            await self.handle_error_command(message, args)
        elif command_name == "sync":
            await self.handle_sync_command(message, args)
        elif command_name == "setup-announcements":
            await self.handle_setup_announcements_command(message, args)
        elif command_name == "serverlist":
            await self.handle_serverlist_command(message, args)
        elif command_name == "disable":
            await self.handle_disable_command(message, args)
        elif command_name == "enable":
            await self.handle_enable_command(message, args)
        elif command_name == "disabled":
            await self.handle_disabled_command(message, args)
        elif command_name == "cogs":
            await self.handle_cogs_command(message, args)
        elif command_name == "presence":
            await self.handle_presence_command(message, args)
        elif command_name == "redirect":
            await self.handle_redirect_command(message, args)
        elif command_name == "banner":
            await self.handle_banner_command(message, args)
        else:
            view = create_error_message("Unknown Command", f"Developer command `{command_name}` not found.")
            await self.reply_with_tracking(message, view)

    async def handle_reload_command(self, message: discord.Message, args: str):
        """
        Handle d.reload command - Reload bot extensions
        Usage: <@1373916203814490194> d.reload [extension]
        """
        # Log the command
        if staff_logger:
            await staff_logger.log_command("d", "reload", message.author, args=args or "all")

        if not args or args == "all":
            # Reload all extensions
            view = create_info_message("Reloading All Extensions", "Reloading all cogs and staff commands...")
            msg = await message.reply(view=view, mention_author=False)

            success = []
            failed = []

            # Reload all loaded extensions
            extensions = list(self.bot.extensions.keys())
            for ext in extensions:
                try:
                    await self.bot.reload_extension(ext)
                    success.append(ext)
                except Exception as e:
                    failed.append(f"{ext}: {str(e)}")

            # Create result view
            title = "Reload Complete" if not failed else "Reload Complete with Errors"
            description = "Extensions reloaded successfully." if not failed else "Some extensions failed to reload."

            fields = []
            if success:
                fields.append({
                    'name': f"{EMOJIS['done']} Reloaded ({len(success)})",
                    'value': "\n".join([f"• `{ext}`" for ext in success[:10]]) + (f"\n*...and {len(success) - 10} more*" if len(success) > 10 else "")
                })

            if failed:
                fields.append({
                    'name': f"{EMOJIS['undone']} Failed ({len(failed)})",
                    'value': "\n".join([f"• {f}" for f in failed[:5]]) + (f"\n*...and {len(failed) - 5} more*" if len(failed) > 5 else "")
                })

            footer = None

            if failed:
                result_view = create_warning_message(title, description, fields)
            else:
                result_view = create_success_message(title, description, fields, footer)

            await msg.edit(view=result_view)

        else:
            # Reload specific extension
            ext_name = args.strip()

            # Try to find the extension
            if not ext_name.startswith(("cogs.", "staff.")):
                # Try to guess the right path
                if ext_name in [e.split('.')[-1] for e in self.bot.extensions]:
                    # Find full name
                    for full_name in self.bot.extensions:
                        if full_name.endswith(ext_name):
                            ext_name = full_name
                            break

            try:
                await self.bot.reload_extension(ext_name)

                view = create_success_message(
                    "Extension Reloaded",
                    f"Successfully reloaded `{ext_name}`",
                    footer=None
                )

                await self.reply_with_tracking(message, view)

            except Exception as e:
                view = create_error_message(
                    "Reload Failed",
                    f"Failed to reload `{ext_name}`",
                    fields=[{'name': 'Error', 'value': f"```{str(e)[:500]}```"}]
                )

                await self.reply_with_tracking(message, view)

    async def handle_shutdown_command(self, message: discord.Message, args: str):
        """
        Handle d.shutdown command - Shutdown the bot
        Usage: <@1373916203814490194> d.shutdown
        """
        # Log the command
        if staff_logger:
            await staff_logger.log_command("d", "shutdown", message.author)

        view = create_error_message(
            "Shutting Down",
            "MODDY is shutting down..."
        )

        await self.reply_with_tracking(message, view)

        logger.info(f"Bot shutdown requested by {message.author} ({message.author.id})")
        await self.bot.close()

    async def handle_stats_command(self, message: discord.Message, args: str):
        """
        Handle d.stats command - Show bot statistics
        Usage: <@1373916203814490194> d.stats
        """
        # Log the command
        if staff_logger:
            await staff_logger.log_command("d", "stats", message.author)

        # Bot info
        uptime = datetime.now(timezone.utc) - self.bot.launch_time
        days = uptime.days
        hours, remainder = divmod(uptime.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)

        fields = []

        fields.append({
            'name': f"{EMOJIS['moddy']} Bot Information",
            'value': f"**Uptime:** {days}d {hours}h {minutes}m {seconds}s\n**Latency:** {round(self.bot.latency * 1000)}ms"
        })

        # Server stats
        fields.append({
            'name': f"{EMOJIS['web']} Discord Statistics",
            'value': f"**Guilds:** {len(self.bot.guilds):,}\n**Users:** {len(self.bot.users):,}\n**Commands:** {len(self.bot.tree.get_commands())}"
        })

        # Database stats
        if db:
            try:
                stats = await db.get_stats()
                fields.append({
                    'name': "Database Statistics",
                    'value': f"**Users:** {stats.get('users', 0):,}\n**Guilds:** {stats.get('guilds', 0):,}\n**Errors:** {stats.get('errors', 0):,}"
                })
            except:
                pass

        # System info
        import psutil
        process = psutil.Process()
        memory_info = process.memory_info()

        fields.append({
            'name': "System Resources",
            'value': f"**RAM:** {memory_info.rss / 1024 / 1024:.2f} MB\n**CPU:** {process.cpu_percent()}%\n**Threads:** {process.num_threads()}"
        })

        # Extensions
        fields.append({
            'name': "Extensions",
            'value': f"**Loaded:** {len(self.bot.extensions)}\n**Cogs:** {len(self.bot.cogs)}"
        })

        view = create_info_message(
            f"{EMOJIS['info']} MODDY Statistics",
            "Statistiques actuelles du bot",
            fields=fields,
            footer=f"Requested by {message.author}"
        )

        await self.reply_with_tracking(message, view)

    async def handle_sql_command(self, message: discord.Message, args: str):
        """
        Handle d.sql command - Execute SQL query
        Usage: <@1373916203814490194> d.sql [query]
        """
        # Log the command (don't log the full query for security)
        if staff_logger:
            query_preview = args.strip()[:50] + "..." if len(args.strip()) > 50 else args.strip()
            await staff_logger.log_command("d", "sql", message.author, args=query_preview)

        if not args:
            view = create_error_message(
                "Invalid Usage",
                "**Usage:** `<@1373916203814490194> d.sql [query]`\n\nProvide a SQL query to execute."
            )
            await self.reply_with_tracking(message, view)
            return

        if not db:
            view = create_error_message(
                "Database Not Available",
                "Database is not connected."
            )
            await self.reply_with_tracking(message, view)
            return

        query = args.strip()

        # Warning for dangerous queries
        dangerous_keywords = ["DROP", "DELETE", "TRUNCATE", "ALTER"]
        if any(keyword in query.upper() for keyword in dangerous_keywords):
            view = create_warning_message(
                "Dangerous Query",
                f"This query contains potentially dangerous operations:\n```sql\n{query[:500]}\n```\n\nReact with ✅ to confirm execution."
            )
            msg = await message.reply(view=view, mention_author=False)
            await msg.add_reaction("✅")
            await msg.add_reaction("❌")

            def check(reaction, user):
                return user == message.author and str(reaction.emoji) in ["✅", "❌"] and reaction.message.id == msg.id

            try:
                reaction, user = await self.bot.wait_for('reaction_add', timeout=30.0, check=check)

                if str(reaction.emoji) == "❌":
                    cancel_view = create_error_message("Cancelled", "Query execution cancelled.")
                    await msg.edit(view=cancel_view)
                    return
            except:
                timeout_view = create_error_message("Timeout", "Query confirmation timed out.")
                await msg.edit(view=timeout_view)
                return

        try:
            async with db.pool.acquire() as conn:
                # Check if it's a SELECT query
                if query.upper().strip().startswith("SELECT"):
                    rows = await conn.fetch(query)

                    if not rows:
                        view = create_success_message("Query Executed", "No results returned.")
                        await self.reply_with_tracking(message, view)
                        return

                    # Format results
                    result_text = "```\n"
                    for row in rows[:10]:  # Limit to 10 rows
                        result_text += " | ".join([str(v) for v in row.values()]) + "\n"
                    result_text += "```"

                    if len(rows) > 10:
                        result_text += f"\n*...and {len(rows) - 10} more rows*"

                    view = create_success_message(
                        "Query Executed",
                        f"**Rows:** {len(rows)}\n\n{result_text}",
                        footer=None
                    )
                else:
                    # Execute non-SELECT query
                    result = await conn.execute(query)

                    view = create_success_message(
                        "Query Executed",
                        f"```sql\n{query[:500]}\n```\n\n**Result:** {result}",
                        footer=None
                    )

                await self.reply_with_tracking(message, view)

        except Exception as e:
            view = create_error_message(
                "Query Failed",
                f"```sql\n{query[:500]}\n```",
                fields=[{'name': 'Error', 'value': f"```{str(e)[:500]}```"}]
            )

            await self.reply_with_tracking(message, view)

    async def handle_jsk_command(self, message: discord.Message, args: str):
        """
        Handle d.jsk command - Execute Python code
        Usage: <@1373916203814490194> d.jsk [code]
        """
        # Log the command (don't log the full code for security)
        if staff_logger:
            code_preview = args.strip()[:50] + "..." if len(args.strip()) > 50 else args.strip()
            await staff_logger.log_command("d", "jsk", message.author, args=code_preview)

        if not args:
            view = create_error_message(
                "Invalid Usage",
                "**Usage:** `<@1373916203814490194> d.jsk [code]`\n\nProvide Python code to execute."
            )
            await self.reply_with_tracking(message, view)
            return

        code = args.strip()

        # Remove code blocks if present
        if code.startswith("```") and code.endswith("```"):
            code = code[3:-3]
            if code.startswith("python") or code.startswith("py"):
                code = code.split('\n', 1)[1] if '\n' in code else ""

        # Create execution environment
        env = {
            'bot': self.bot,
            'message': message,
            'channel': message.channel,
            'author': message.author,
            'guild': message.guild,
            'db': db,
            'discord': discord,
            'commands': commands,
            'asyncio': __import__('asyncio'),
            'datetime': datetime,
            'timezone': timezone,
        }

        # Add imports
        import io
        import contextlib
        import textwrap
        import traceback

        # Prepare code
        to_compile = f'async def func():\n{textwrap.indent(code, "  ")}'

        try:
            # Compile the code
            exec(to_compile, env)

            # Execute and capture output
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                func = env['func']
                result = await func()

            # Get output
            output = stdout.getvalue()

            # Format result
            if result is not None:
                output += f"\n{repr(result)}"

            if not output:
                output = f"{EMOJIS['done']} Code executed successfully (no output)"

            # Limit output length
            if len(output) > 1900:
                output = output[:1900] + "\n... (output truncated)"

            view = create_success_message(
                "Code Executed",
                f"```python\n{code[:500]}\n```",
                fields=[{'name': 'Output', 'value': f"```python\n{output}\n```"}],
                footer=None
            )

            await self.reply_with_tracking(message, view)

        except Exception as e:
            # Format error
            error_traceback = traceback.format_exc()

            if len(error_traceback) > 1900:
                error_traceback = error_traceback[-1900:]

            view = create_error_message(
                "Execution Failed",
                f"```python\n{code[:500]}\n```",
                fields=[{'name': 'Error', 'value': f"```python\n{error_traceback}\n```"}]
            )

            await self.reply_with_tracking(message, view)

    async def handle_error_command(self, message: discord.Message, args: str):
        """
        Handle d.error command - Get detailed error information
        Usage: <@1373916203814490194> d.error [error_code]
        """
        # Log the command
        if staff_logger:
            await staff_logger.log_command("d", "error", message.author, args=args)

        if not args:
            view = create_error_message(
                "Invalid Usage",
                "**Usage:** `<@1373916203814490194> d.error [error_code]`\n\nProvide an error code to get information."
            )
            await self.reply_with_tracking(message, view)
            return

        error_code = args.strip().upper()

        # First check in cache (from error_handler cog)
        error_tracker = self.bot.get_cog('ErrorTracker')
        cached_error = None

        if error_tracker:
            # Search in cache
            for error in error_tracker.error_cache:
                if error['code'] == error_code:
                    cached_error = error
                    break

        # Then check in database
        db_error = None
        if db:
            try:
                db_error = await db.get_error(error_code)
            except Exception as e:
                logger.error(f"Error fetching error from database: {e}")

        # If not found in either cache or database
        if not cached_error and not db_error:
            view = create_error_message(
                "Error Not Found",
                f"No error found with code `{error_code}`.\n\nThe error may have expired from cache or was never logged."
            )
            await self.reply_with_tracking(message, view)
            return

        # Use database error if available, otherwise use cached error
        error_data = db_error if db_error else cached_error['data']
        timestamp = db_error.get('timestamp') if db_error else cached_error.get('timestamp')

        fields = []

        # Error details
        error_details_lines = [
            f"**Code:** `{error_code}`",
            f"**Type:** `{error_data.get('error_type') or error_data.get('type')}`",
            f"**File:** `{error_data.get('file_source') or error_data.get('file')}:{error_data.get('line_number') or error_data.get('line')}`"
        ]

        # Add Sentry IDs if available
        sentry_event_id = error_data.get('sentry_event_id')
        sentry_issue_id = error_data.get('sentry_issue_id')

        if sentry_event_id:
            error_details_lines.append(f"**Sentry Event ID:** `{sentry_event_id}`")

        if sentry_issue_id:
            # Create a link to the Sentry issue
            sentry_url = f"https://moddy-0f.sentry.io/issues/{sentry_issue_id}/"
            error_details_lines.append(f"**Sentry Issue:** [#{sentry_issue_id}]({sentry_url})")

        fields.append({
            'name': f"{EMOJIS['info']} Error Details",
            'value': "\n".join(error_details_lines)
        })

        # Error message
        error_message = error_data.get('message', 'No message')
        if len(error_message) > 1000:
            error_message = error_message[:1000] + "..."
        fields.append({
            'name': "Message",
            'value': f"```{error_message}```"
        })

        # Context - always show if any context data is available
        context_parts = []

        # Command context
        if error_data.get('command'):
            context_parts.append(f"**Command:** `{error_data.get('command')}`")

        # User context
        if error_data.get('user_id'):
            user_id = error_data.get('user_id')
            try:
                user = await self.bot.fetch_user(user_id)
                context_parts.append(f"**User:** {user.mention} ({user} - `{user_id}`)")
            except:
                context_parts.append(f"**User ID:** `{user_id}`")

        # Guild/Server context
        if error_data.get('guild_id'):
            guild_id = error_data.get('guild_id')
            guild = self.bot.get_guild(guild_id)
            if guild:
                context_parts.append(f"**Server:** {guild.name} (`{guild_id}`)")
            else:
                context_parts.append(f"**Server ID:** `{guild_id}`")

        # Additional context from context field
        if error_data.get('context'):
            ctx = error_data['context']
            if isinstance(ctx, dict):
                if ctx.get('channel'):
                    context_parts.append(f"**Channel:** {ctx['channel']}")
                if ctx.get('message'):
                    msg = ctx['message']
                    if len(msg) > 100:
                        msg = msg[:100] + "..."
                    context_parts.append(f"**Message:** `{msg}`")

        # Add context field if we have any context information
        if context_parts:
            fields.append({
                'name': f"{EMOJIS['info']} Context",
                'value': "\n".join(context_parts)
            })
        else:
            fields.append({
                'name': f"{EMOJIS['info']} Context",
                'value': "*No context information available*"
            })

        # Traceback
        traceback_text = error_data.get('traceback', 'No traceback available')
        if len(traceback_text) > 1000:
            traceback_text = traceback_text[-1000:]  # Get last 1000 chars
        fields.append({
            'name': "Traceback",
            'value': f"```python\n{traceback_text}\n```"
        })

        # Timestamp
        if timestamp:
            ts_unix = int(timestamp.timestamp()) if hasattr(timestamp, 'timestamp') else int(datetime.fromisoformat(str(timestamp)).timestamp())
            fields.append({
                'name': f"{EMOJIS['time']} Occurred",
                'value': f"<t:{ts_unix}:R> (<t:{ts_unix}:F>)"
            })

        # Source indicator
        source = "Database" if db_error else "Cache"

        view = create_error_message(
            f"Error Information - {error_code}",
            f"Detailed information about error `{error_code}` (from {source})",
            fields=fields
        )

        await self.reply_with_tracking(message, view)

    async def handle_sync_command(self, message: discord.Message, args: str):
        """
        Handle d.sync command - Sync app commands with Discord
        Usage: <@1373916203814490194> d.sync [guild_id|global]
        """
        # Log the command
        if staff_logger:
            await staff_logger.log_command("d", "sync", message.author, args=args or "global")

        view = create_info_message("Syncing Commands", "Synchronizing application commands with Discord...")
        msg = await message.reply(view=view, mention_author=False)

        try:
            if not args or args.strip().lower() == "global":
                # Sync globally
                synced = await self.bot.tree.sync()

                view = create_success_message(
                    "Commands Synced",
                    f"Successfully synced **{len(synced)}** commands globally.\n\n-# Commands may take up to 1 hour to appear in all servers.",
                    footer=None
                )

            else:
                # Sync to specific guild
                try:
                    guild_id = int(args.strip())
                    guild = discord.Object(id=guild_id)

                    # Clear old commands first to remove any previously synced commands
                    # This allows Discord to use global commands
                    self.bot.tree.clear_commands(guild=guild)

                    # Add ONLY guild-only commands to this guild (not global commands)
                    # Global commands are already available everywhere without copy_global_to()
                    # Using copy_global_to() would make Discord ignore global commands for this guild
                    if self.bot._guild_only_commands:
                        for command in self.bot._guild_only_commands:
                            self.bot.tree.add_command(command, guild=guild)

                    synced = await self.bot.tree.sync(guild=guild)

                    guild_obj = self.bot.get_guild(guild_id)
                    guild_name = guild_obj.name if guild_obj else f"Guild {guild_id}"

                    view = create_success_message(
                        "Commands Synced",
                        f"Successfully synced **{len(synced)}** guild-only commands to **{guild_name}**.\n\n-# Global commands are already available everywhere.",
                        footer=None
                    )

                except ValueError:
                    view = create_error_message(
                        "Invalid Guild ID",
                        "Please provide a valid guild ID (numeric) or use 'global'.\n\n**Usage:** `<@1373916203814490194> d.sync [guild_id|global]`"
                    )

            await msg.edit(view=view)

        except Exception as e:
            logger.error(f"Error syncing commands: {e}")
            view = create_error_message(
                "Sync Failed",
                "Failed to sync commands with Discord.",
                fields=[{'name': 'Error', 'value': f"```{str(e)[:500]}```"}]
            )
            await msg.edit(view=view)

    async def handle_setup_announcements_command(self, message: discord.Message, args: str):
        """
        Handle d.setup-announcements command - Setup announcement channel following for a guild
        Usage: <@1373916203814490194> d.setup-announcements <guild_id>
        """
        # Log the command
        if staff_logger:
            await staff_logger.log_command("d", "setup-announcements", message.author, args=args or "no args")

        # Check if guild_id is provided
        if not args or not args.strip():
            view = create_error_message(
                "Missing Guild ID",
                "Please provide a guild ID.\n\n**Usage:** `<@1373916203814490194> d.setup-announcements <guild_id>`"
            )
            await self.reply_with_tracking(message, view)
            return

        try:
            # Parse guild ID
            guild_id = int(args.strip())
        except ValueError:
            view = create_error_message(
                "Invalid Guild ID",
                "Please provide a valid guild ID (numeric).\n\n**Usage:** `<@1373916203814490194> d.setup-announcements <guild_id>`"
            )
            await self.reply_with_tracking(message, view)
            return

        # Get guild
        guild = self.bot.get_guild(guild_id)
        if not guild:
            view = create_error_message(
                "Guild Not Found",
                f"Could not find guild with ID `{guild_id}`.\n\n-# Make sure the bot is in this server."
            )
            await self.reply_with_tracking(message, view)
            return

        # Send processing message
        view = create_info_message("Setting Up Announcements", f"Setting up announcement channel for **{guild.name}**...")
        msg = await message.reply(view=view, mention_author=False)

        try:
            # Setup announcement channel
            success, result_message = await setup_announcement_channel(guild)

            if success:
                view = create_success_message(
                    "Announcements Setup Complete",
                    f"Successfully setup announcements for **{guild.name}** (`{guild.id}`).\n\n-# {result_message}",
                    footer=None
                )
            else:
                # Check if it's a permission error
                if "permission" in result_message.lower():
                    view = create_error_message(
                        "Missing Permissions",
                        f"Cannot setup announcements for **{guild.name}** (`{guild.id}`).\n\n**Reason:** {result_message}\n\n-# The bot needs **Manage Webhooks** and **Manage Channels** permissions in this server."
                    )
                else:
                    view = create_error_message(
                        "Setup Failed",
                        f"Failed to setup announcements for **{guild.name}** (`{guild.id}`).\n\n**Reason:** {result_message}"
                    )

            await msg.edit(view=view)

        except Exception as e:
            logger.error(f"Error setting up announcements for guild {guild_id}: {e}", exc_info=True)
            view = create_error_message(
                "Unexpected Error",
                f"An unexpected error occurred while setting up announcements for **{guild.name}**.",
                fields=[{'name': 'Error', 'value': f"```{str(e)[:500]}```"}]
            )
            await msg.edit(view=view)

    async def handle_serverlist_command(self, message: discord.Message, args: str):
        """
        Handle d.serverlist command - Display list of servers where the bot is present with pagination
        Usage: <@1373916203814490194> d.serverlist
        """
        # Log the command
        if staff_logger:
            await staff_logger.log_command("d", "serverlist", message.author)

        # Get all guilds and sort by member count (descending)
        guilds = sorted(self.bot.guilds, key=lambda g: g.member_count, reverse=True)

        # Create pagination view
        view = ServerListView(self.bot, guilds, page=0, per_page=10)

        # Send the view
        await self.reply_with_tracking(message, view)


    async def handle_disable_command(self, message: discord.Message, args: str):
        """
        Handle d.disable command - Disable a cog at runtime
        Usage: <@bot> d.disable <CogName>
        """
        if staff_logger:
            await staff_logger.log_command("d", "disable", message.author, args=args or "no args")

        if not args or not args.strip():
            view = create_error_message(
                "Invalid Usage",
                "**Usage:** `d.disable <CogName>`\n\nProvide the cog class name (e.g. `Invite`, `Reminder`)."
            )
            await self.reply_with_tracking(message, view)
            return

        cog_name = args.strip()
        cog_manager = self.bot.get_cog("CogManager")
        if not cog_manager:
            view = create_error_message("Error", "CogManager is not loaded.")
            await self.reply_with_tracking(message, view)
            return

        success, result_msg = await cog_manager.disable_cog(cog_name)
        if success:
            view = create_success_message("Cog Disabled", result_msg, footer=None)
        else:
            view = create_error_message("Cannot Disable", result_msg)
        await self.reply_with_tracking(message, view)

    async def handle_enable_command(self, message: discord.Message, args: str):
        """
        Handle d.enable command - Re-enable a cog at runtime
        Usage: <@bot> d.enable <CogName>
        """
        if staff_logger:
            await staff_logger.log_command("d", "enable", message.author, args=args or "no args")

        if not args or not args.strip():
            view = create_error_message(
                "Invalid Usage",
                "**Usage:** `d.enable <CogName>`\n\nProvide the cog class name (e.g. `Invite`, `Reminder`)."
            )
            await self.reply_with_tracking(message, view)
            return

        cog_name = args.strip()
        cog_manager = self.bot.get_cog("CogManager")
        if not cog_manager:
            view = create_error_message("Error", "CogManager is not loaded.")
            await self.reply_with_tracking(message, view)
            return

        success, result_msg = await cog_manager.enable_cog(cog_name)
        if success:
            view = create_success_message("Cog Enabled", result_msg, footer=None)
        else:
            view = create_error_message("Cannot Enable", result_msg)
        await self.reply_with_tracking(message, view)

    async def handle_disabled_command(self, message: discord.Message, args: str):
        """
        Handle d.disabled command - List currently disabled cogs
        Usage: <@bot> d.disabled
        """
        if staff_logger:
            await staff_logger.log_command("d", "disabled", message.author)

        cog_manager = self.bot.get_cog("CogManager")
        if not cog_manager:
            view = create_error_message("Error", "CogManager is not loaded.")
            await self.reply_with_tracking(message, view)
            return

        disabled = cog_manager.get_disabled_cogs()
        if disabled:
            listing = "\n".join([f"• `{name}`" for name in disabled])
            view = create_info_message(
                "Disabled Cogs",
                f"**{len(disabled)}** cog(s) currently disabled:\n\n{listing}",
            )
        else:
            view = create_success_message("No Disabled Cogs", "All cogs are currently enabled.")
        await self.reply_with_tracking(message, view)

    async def handle_presence_command(self, message: discord.Message, args: str):
        """
        Handle d.presence command - Change the bot's presence/status
        Usage: <@bot> d.presence <online|idle|dnd|invisible> [activity text]
        """
        if staff_logger:
            await staff_logger.log_command("d", "presence", message.author, args=args or "no args")

        STATUS_MAP = {
            "online": discord.Status.online,
            "idle": discord.Status.idle,
            "dnd": discord.Status.dnd,
            "invisible": discord.Status.invisible,
            "offline": discord.Status.invisible,
        }

        STATUS_LABELS = {
            "online": f"{EMOJIS['green_status']} Online",
            "idle": f"{EMOJIS['yellow_status']} Idle",
            "dnd": f"{EMOJIS['red_status']} Do Not Disturb",
            "invisible": "Invisible",
            "offline": "Invisible",
        }

        if not args or not args.strip():
            options = " | ".join(f"`{k}`" for k in STATUS_MAP)
            view = create_error_message(
                "Invalid Usage",
                f"**Usage:** `d.presence <status> [activity text]`\n\n**Statuses:** {options}\n\n**Example:** `d.presence idle En maintenance`"
            )
            await self.reply_with_tracking(message, view)
            return

        parts = args.strip().split(None, 1)
        status_key = parts[0].lower()
        activity_text = parts[1].strip() if len(parts) > 1 else None

        if status_key not in STATUS_MAP:
            options = " | ".join(f"`{k}`" for k in STATUS_MAP)
            view = create_error_message(
                "Unknown Status",
                f"Status `{status_key}` is not valid.\n\n**Available:** {options}"
            )
            await self.reply_with_tracking(message, view)
            return

        status = STATUS_MAP[status_key]
        activity = discord.CustomActivity(name=activity_text) if activity_text else None

        await self.bot.change_presence(status=status, activity=activity)

        label = STATUS_LABELS[status_key]
        description = f"Presence changed to **{label}**."
        if activity_text:
            description += f"\n**Activity:** `{activity_text}`"

        view = create_success_message("Presence Updated", description, footer=None)
        await self.reply_with_tracking(message, view)

    async def handle_cogs_command(self, message: discord.Message, args: str):
        """
        Handle d.cogs command - List all loaded cogs with status
        Usage: <@bot> d.cogs
        """
        if staff_logger:
            await staff_logger.log_command("d", "cogs", message.author)

        cog_manager = self.bot.get_cog("CogManager")
        disabled_set = set()
        if cog_manager:
            disabled_set = cog_manager.disabled_cogs

        cog_names = sorted(self.bot.cogs.keys())
        lines = []
        for name in cog_names:
            status = f"{EMOJIS['undone']} Disabled" if name in disabled_set else f"{EMOJIS['done']} Enabled"
            lines.append(f"• `{name}` — {status}")

        listing = "\n".join(lines) if lines else "*No cogs loaded.*"
        view = create_info_message(
            "Loaded Cogs",
            f"**{len(cog_names)}** cog(s) loaded:\n\n{listing}",
            footer=f"Requested by {message.author}"
        )
        await self.reply_with_tracking(message, view)


    async def handle_sub_refresh_command(self, message: discord.Message, args: str):
        """
        Handle d.sub-refresh command - Invalidate subscription Redis cache for a user
        Usage: <@bot> d.sub-refresh <user_id>
        """
        from utils.subscription import invalidate_cache

        if staff_logger:
            await staff_logger.log_command("d", "sub-refresh", message.author)

        if not args:
            view = create_error_message(
                "Missing Argument",
                "**Usage:** `d.sub-refresh <user_id>`",
            )
            await self.reply_with_tracking(message, view)
            return

        try:
            user_id = int(args.strip())
        except ValueError:
            view = create_error_message("Invalid Argument", f"Invalid user ID: `{args.strip()}`")
            await self.reply_with_tracking(message, view)
            return

        await invalidate_cache(self.bot, user_id)

        view = create_success_message(
            "Cache Invalidated",
            f"Subscription Redis cache cleared for user `{user_id}`.\nNext `/subscription` call will fetch fresh data from the DB.",
        )
        await self.reply_with_tracking(message, view)


    async def handle_redirect_command(self, message: discord.Message, args: str):
        """
        Handle d.redirect command — manage redirect links table.
        Subcommands: add <domain> <path> <description>, list, info <id>, delete <id>
        """
        if staff_logger:
            await staff_logger.log_command("d", "redirect", message.author, args=args or "no args")

        if not args or not args.strip():
            view = create_error_message(
                "Invalid Usage",
                "**Subcommands:**\n"
                "`d.redirect add` — open modal to create a redirect\n"
                "`d.redirect edit <id>` — open modal to edit a redirect\n"
                "`d.redirect list`\n"
                "`d.redirect info <id>`\n"
                "`d.redirect delete <id>`"
            )
            await self.reply_with_tracking(message, view)
            return

        parts = args.strip().split(None, 1)
        subcmd = parts[0].lower()
        sub_args = parts[1].strip() if len(parts) > 1 else ""

        bot_db = self.bot.db
        if not bot_db:
            await self.reply_with_tracking(message, create_error_message("Database Unavailable", "Database is not connected."))
            return

        if subcmd == "add":
            view = RedirectModalView(self.bot, message.author.id, bot_db)
            await self.reply_with_tracking(message, view)

        elif subcmd == "edit":
            if not sub_args:
                await self.reply_with_tracking(message, create_error_message("Invalid Usage", "**Usage:** `d.redirect edit <id>`"))
                return
            try:
                rid = int(sub_args.strip())
                row = await bot_db.get_redirect(rid)
            except ValueError:
                await self.reply_with_tracking(message, create_error_message("Invalid ID", "Provide a numeric ID."))
                return
            except Exception as e:
                await self.reply_with_tracking(message, create_error_message("Error", str(e)[:300]))
                return
            if not row:
                await self.reply_with_tracking(message, create_error_message("Not Found", f"No redirect with ID `{rid}`."))
                return
            view = RedirectModalView(self.bot, message.author.id, bot_db, redirect_id=rid, prefill=row)
            await self.reply_with_tracking(message, view)

        elif subcmd == "list":
            try:
                rows = await bot_db.list_redirects()
            except Exception as e:
                await self.reply_with_tracking(message, create_error_message("Error", str(e)[:300]))
                return

            if not rows:
                await self.reply_with_tracking(message, create_info_message("Redirect Links", "No redirect links found."))
                return

            container = ui.Container()
            container.add_item(ui.TextDisplay(f"### {EMOJIS['web']} Redirect Links\n**Total:** `{len(rows)}`"))
            container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
            lines = [f"`{r['id']}` **{r['domain']}{r['path']}** → `{r['target']}`\n-# {r['description']}" for r in rows[:20]]
            container.add_item(ui.TextDisplay("\n\n".join(lines)))
            view = ui.LayoutView()
            view.add_item(container)
            await self.reply_with_tracking(message, view)

        elif subcmd == "info":
            if not sub_args:
                await self.reply_with_tracking(message, create_error_message("Invalid Usage", "**Usage:** `d.redirect info <id>`"))
                return
            try:
                rid = int(sub_args.strip())
                row = await bot_db.get_redirect(rid)
            except ValueError:
                await self.reply_with_tracking(message, create_error_message("Invalid ID", "Provide a numeric ID."))
                return
            except Exception as e:
                await self.reply_with_tracking(message, create_error_message("Error", str(e)[:300]))
                return

            if not row:
                await self.reply_with_tracking(message, create_error_message("Not Found", f"No redirect with ID `{rid}`."))
                return

            ts = int(row['added_at'].timestamp()) if row['added_at'] else 0
            view = create_info_message(
                "Redirect Info",
                f"**ID:** `{row['id']}`\n"
                f"**Source:** `{row['domain']}{row['path']}`\n"
                f"**Target:** `{row['target']}`\n"
                f"**Description:** {row['description']}\n"
                f"**Added by:** <@{row['added_by']}>\n"
                f"**Added:** <t:{ts}:R>",
            )
            await self.reply_with_tracking(message, view)

        elif subcmd == "delete":
            if not sub_args:
                await self.reply_with_tracking(message, create_error_message("Invalid Usage", "**Usage:** `d.redirect delete <id>`"))
                return
            try:
                rid = int(sub_args.strip())
                row = await bot_db.get_redirect(rid)
            except ValueError:
                await self.reply_with_tracking(message, create_error_message("Invalid ID", "Provide a numeric ID."))
                return
            except Exception as e:
                await self.reply_with_tracking(message, create_error_message("Error", str(e)[:300]))
                return

            if not row:
                await self.reply_with_tracking(message, create_error_message("Not Found", f"No redirect with ID `{rid}`."))
                return

            try:
                deleted = await bot_db.delete_redirect(rid)
            except Exception as e:
                await self.reply_with_tracking(message, create_error_message("Error", str(e)[:300]))
                return

            if deleted:
                view = create_success_message("Redirect Deleted", f"Redirect `{rid}` (`{row['domain']}{row['path']}`) has been deleted.", footer=None)
            else:
                view = create_error_message("Not Found", f"No redirect with ID `{rid}`.")
            await self.reply_with_tracking(message, view)

        else:
            view = create_error_message("Unknown Subcommand", f"Unknown subcommand `{subcmd}`. Use `add`, `list`, `info`, or `delete`.")
            await self.reply_with_tracking(message, view)

    async def handle_banner_command(self, message: discord.Message, args: str):
        """
        Handle d.banner command — manage site/dashboard banners.
        Subcommands: add, list, info <id>, activate <id>, deactivate, edit <id>, delete <id>
        """
        if staff_logger:
            await staff_logger.log_command("d", "banner", message.author, args=args or "no args")

        VALID_TYPES = ('announcement', 'incident', 'maintenance', 'information', 'warning', 'resolved')

        if not args or not args.strip():
            view = create_error_message(
                "Invalid Usage",
                "**Subcommands:**\n"
                "`d.banner add` — create a new banner (choose typed or custom)\n"
                "`d.banner list` — list all banners\n"
                "`d.banner info <id>` — show banner details\n"
                "`d.banner activate <id>` — activate a banner\n"
                "`d.banner deactivate` — deactivate the current banner\n"
                "`d.banner edit <id>` — edit a banner\n"
                "`d.banner delete <id>` — delete a banner"
            )
            await self.reply_with_tracking(message, view)
            return

        parts = args.strip().split(None, 1)
        subcmd = parts[0].lower()
        sub_args = parts[1].strip() if len(parts) > 1 else ""

        bot_db = self.bot.db
        if not bot_db:
            await self.reply_with_tracking(message, create_error_message("Database Unavailable", "Database is not connected."))
            return

        if subcmd == "add":
            await self._banner_add_flow(message, bot_db, banner_id=None, prefill=None)

        elif subcmd == "list":
            try:
                rows = await bot_db.list_banners()
            except Exception as e:
                await self.reply_with_tracking(message, create_error_message("Error", str(e)[:300]))
                return

            if not rows:
                await self.reply_with_tracking(message, create_info_message("Banners", "No banners found."))
                return

            container = ui.Container()
            container.add_item(ui.TextDisplay(f"### {EMOJIS['info']} Banners\n**Total:** `{len(rows)}`"))
            container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
            lines = []
            for r in rows[:20]:
                status = "**[ACTIVE]** " if r['is_active'] else ""
                kind = r['type'] or "custom"
                lines.append(f"`{r['id']}` {status}**{kind}** — {r['message'][:60]}{'...' if len(r['message']) > 60 else ''}")
            container.add_item(ui.TextDisplay("\n".join(lines)))
            view = ui.LayoutView()
            view.add_item(container)
            await self.reply_with_tracking(message, view)

        elif subcmd == "info":
            if not sub_args:
                await self.reply_with_tracking(message, create_error_message("Invalid Usage", "**Usage:** `d.banner info <id>`"))
                return
            try:
                bid = int(sub_args.strip())
                row = await bot_db.get_banner(bid)
            except ValueError:
                await self.reply_with_tracking(message, create_error_message("Invalid ID", "Provide a numeric ID."))
                return
            except Exception as e:
                await self.reply_with_tracking(message, create_error_message("Error", str(e)[:300]))
                return

            if not row:
                await self.reply_with_tracking(message, create_error_message("Not Found", f"No banner with ID `{bid}`."))
                return

            created_ts = int(row['created_at'].timestamp()) if row['created_at'] else 0
            updated_ts = int(row['updated_at'].timestamp()) if row['updated_at'] else 0
            kind = row['type'] or "custom"
            active_str = "Yes" if row['is_active'] else "No"
            detail = (
                f"**ID:** `{row['id']}`\n"
                f"**Active:** {active_str}\n"
                f"**Type:** {kind}\n"
            )
            if row['type'] is None:
                detail += f"**Color:** `{row['color']}`\n**Icon SVG:** *present*\n"
            detail += (
                f"**Dashboard:** {'Yes' if row['show_dashboard'] else 'No'} | **Website:** {'Yes' if row['show_website'] else 'No'}\n"
                f"**Created:** <t:{created_ts}:R> | **Updated:** <t:{updated_ts}:R>\n\n"
                f"**Message:**\n{row['message'][:500]}"
            )
            view = create_info_message("Banner Info", detail)
            await self.reply_with_tracking(message, view)

        elif subcmd == "activate":
            if not sub_args:
                await self.reply_with_tracking(message, create_error_message("Invalid Usage", "**Usage:** `d.banner activate <id>`"))
                return
            try:
                bid = int(sub_args.strip())
                ok = await bot_db.activate_banner(bid)
            except ValueError:
                await self.reply_with_tracking(message, create_error_message("Invalid ID", "Provide a numeric ID."))
                return
            except Exception as e:
                await self.reply_with_tracking(message, create_error_message("Error", str(e)[:300]))
                return

            if ok:
                view = create_success_message("Banner Activated", f"Banner `{bid}` is now active. All other banners have been deactivated.", footer=None)
            else:
                view = create_error_message("Not Found", f"No banner with ID `{bid}`.")
            await self.reply_with_tracking(message, view)

        elif subcmd == "deactivate":
            try:
                count = await bot_db.deactivate_banner()
            except Exception as e:
                await self.reply_with_tracking(message, create_error_message("Error", str(e)[:300]))
                return

            if count:
                view = create_success_message("Banner Deactivated", "The active banner has been deactivated.", footer=None)
            else:
                view = create_info_message("No Active Banner", "There is no active banner to deactivate.")
            await self.reply_with_tracking(message, view)

        elif subcmd == "edit":
            if not sub_args:
                await self.reply_with_tracking(message, create_error_message("Invalid Usage", "**Usage:** `d.banner edit <id>`"))
                return
            try:
                bid = int(sub_args.strip())
                row = await bot_db.get_banner(bid)
            except ValueError:
                await self.reply_with_tracking(message, create_error_message("Invalid ID", "Provide a numeric ID."))
                return
            except Exception as e:
                await self.reply_with_tracking(message, create_error_message("Error", str(e)[:300]))
                return

            if not row:
                await self.reply_with_tracking(message, create_error_message("Not Found", f"No banner with ID `{bid}`."))
                return

            await self._banner_add_flow(message, bot_db, banner_id=bid, prefill=row)

        elif subcmd == "delete":
            if not sub_args:
                await self.reply_with_tracking(message, create_error_message("Invalid Usage", "**Usage:** `d.banner delete <id>`"))
                return
            try:
                bid = int(sub_args.strip())
                row = await bot_db.get_banner(bid)
            except ValueError:
                await self.reply_with_tracking(message, create_error_message("Invalid ID", "Provide a numeric ID."))
                return
            except Exception as e:
                await self.reply_with_tracking(message, create_error_message("Error", str(e)[:300]))
                return

            if not row:
                await self.reply_with_tracking(message, create_error_message("Not Found", f"No banner with ID `{bid}`."))
                return

            try:
                deleted = await bot_db.delete_banner(bid)
            except Exception as e:
                await self.reply_with_tracking(message, create_error_message("Error", str(e)[:300]))
                return

            if deleted:
                view = create_success_message("Banner Deleted", f"Banner `{bid}` has been deleted.", footer=None)
            else:
                view = create_error_message("Not Found", f"No banner with ID `{bid}`.")
            await self.reply_with_tracking(message, view)

        else:
            view = create_error_message("Unknown Subcommand", f"Unknown subcommand `{subcmd}`.")
            await self.reply_with_tracking(message, view)

    async def _banner_add_flow(self, message: discord.Message, bot_db, banner_id, prefill):
        """Show the typed/custom banner selection, then open the appropriate modal."""
        is_edit = banner_id is not None
        is_custom = prefill and prefill.get('type') is None if prefill else False

        container = ui.Container()
        container.add_item(ui.TextDisplay(
            f"### {EMOJIS['info']} {'Edit' if is_edit else 'Add'} Banner\n"
            "Choose the banner kind:"
        ))
        view = BannerTypeSelectView(self.bot, message.author.id, bot_db, banner_id, prefill)
        view.add_item(container)
        await self.reply_with_tracking(message, view)


def _random_path(length: int = 6) -> str:
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))


class RedirectModal(discord.ui.Modal):
    """Modal for creating or editing a redirect link."""

    def __init__(self, bot_db, redirect_id=None, prefill=None):
        title = "Edit Redirect" if redirect_id is not None else "Add Redirect"
        super().__init__(title=title)
        self.bot_db = bot_db
        self.redirect_id = redirect_id

        self.domain_input = ui.TextInput(
            label="Domain",
            placeholder="moddy.app",
            default=prefill['domain'] if prefill else "",
            max_length=100,
        )
        self.path_input = ui.TextInput(
            label="Path (starts with /)",
            placeholder="/abc123",
            default=prefill['path'] if prefill else f"/{_random_path()}",
            max_length=100,
        )
        self.target_input = ui.TextInput(
            label="Target URL",
            placeholder="https://docs.moddy.app/...",
            default=prefill['target'] if prefill else "",
            max_length=500,
        )
        self.description_input = ui.TextInput(
            label="Description",
            placeholder="Short description of this redirect",
            default=prefill['description'] if prefill else "",
            max_length=200,
        )
        self.add_item(self.domain_input)
        self.add_item(self.path_input)
        self.add_item(self.target_input)
        self.add_item(self.description_input)

    async def on_submit(self, interaction: discord.Interaction):
        domain = self.domain_input.value.strip()
        path = self.path_input.value.strip()
        target = self.target_input.value.strip()
        description = self.description_input.value.strip()

        try:
            if self.redirect_id is not None:
                row = await self.bot_db.update_redirect(self.redirect_id, domain, path, target, description)
                label = "Redirect Updated"
            else:
                row = await self.bot_db.add_redirect(domain, path, target, description, interaction.user.id)
                label = "Redirect Added"
        except Exception as e:
            await interaction.response.send_message(f"Error: `{str(e)[:300]}`", ephemeral=True)
            return

        container = ui.Container()
        container.add_item(ui.TextDisplay(
            f"### {EMOJIS['done']} {label}\n"
            f"**ID:** `{row['id']}`\n"
            f"**Source:** `{row['domain']}{row['path']}`\n"
            f"**Target:** `{row['target']}`\n"
            f"**Description:** {row['description']}"
        ))
        view = ui.LayoutView()
        view.add_item(container)
        await interaction.response.send_message(view=view, ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        await interaction.response.send_message(f"An error occurred: `{str(error)[:300]}`", ephemeral=True)


class RedirectModalView(BaseView):
    """View that sends a button to open the redirect add/edit modal."""

    def __init__(self, bot, author_id: int, bot_db, redirect_id=None, prefill=None):
        super().__init__(timeout=120)
        self.bot = bot
        self.author_id = author_id
        self.bot_db = bot_db
        self.redirect_id = redirect_id
        self.prefill = prefill
        self._build()

    def _build(self):
        self.clear_items()
        if self.redirect_id is None:
            title = f"### {EMOJIS['web']} Add Redirect\nClick the button to fill in the redirect details."
            btn_label = "Open Modal"
        else:
            title = f"### {EMOJIS['edit']} Edit Redirect `{self.redirect_id}`\nClick the button to edit this redirect."
            btn_label = "Edit Redirect"

        container = ui.Container()
        container.add_item(ui.TextDisplay(title))
        self.add_item(container)

        row = ui.ActionRow()
        btn = ui.Button(label=btn_label, style=discord.ButtonStyle.primary)
        btn.callback = self._open_modal
        row.add_item(btn)
        self.add_item(row)

    async def _open_modal(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This menu is not for you.", ephemeral=True)
            return
        modal = RedirectModal(self.bot_db, self.redirect_id, self.prefill)
        await interaction.response.send_modal(modal)


class BannerTypeSelectView(BaseView):
    """View for selecting typed vs custom banner kind."""

    def __init__(self, bot, author_id: int, bot_db, banner_id, prefill):
        super().__init__(timeout=120)
        self.bot = bot
        self.author_id = author_id
        self.bot_db = bot_db
        self.banner_id = banner_id
        self.prefill = prefill
        self._build()

    def _build(self):
        self.clear_items()
        container = ui.Container()
        container.add_item(ui.TextDisplay(
            f"### {EMOJIS['info']} Banner Type\nSelect the kind of banner to create:"
        ))
        self.add_item(container)

        row = ui.ActionRow()
        typed_btn = ui.Button(label="Typed Banner", style=discord.ButtonStyle.primary)
        typed_btn.callback = self._on_typed
        row.add_item(typed_btn)

        custom_btn = ui.Button(label="Custom Banner", style=discord.ButtonStyle.secondary)
        custom_btn.callback = self._on_custom
        row.add_item(custom_btn)
        self.add_item(row)

    async def _check_author(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This menu is not for you.", ephemeral=True)
            return False
        return True

    async def _on_typed(self, interaction: discord.Interaction):
        if not await self._check_author(interaction):
            return
        modal = TypedBannerModal(self.bot_db, self.banner_id, self.prefill)
        await interaction.response.send_modal(modal)

    async def _on_custom(self, interaction: discord.Interaction):
        if not await self._check_author(interaction):
            return
        modal = CustomBannerModal(self.bot_db, self.banner_id, self.prefill)
        await interaction.response.send_modal(modal)


class TypedBannerModal(discord.ui.Modal, title="Typed Banner"):
    """Modal for creating/editing a typed banner."""

    VALID_TYPES = ('announcement', 'incident', 'maintenance', 'information', 'warning', 'resolved')

    banner_type = discord.ui.TextInput(
        label="Type",
        placeholder="announcement | incident | maintenance | information | warning | resolved",
        max_length=20,
    )
    message = discord.ui.TextInput(
        label="Message (Markdown supported)",
        style=discord.TextStyle.paragraph,
        max_length=1000,
    )
    show_dashboard = discord.ui.TextInput(
        label="Show on Dashboard? (yes/no)",
        placeholder="yes",
        default="yes",
        max_length=3,
    )
    show_website = discord.ui.TextInput(
        label="Show on Website? (yes/no)",
        placeholder="yes",
        default="yes",
        max_length=3,
    )

    def __init__(self, bot_db, banner_id, prefill):
        super().__init__()
        self.bot_db = bot_db
        self.banner_id = banner_id
        if prefill:
            if prefill.get('type'):
                self.banner_type.default = prefill['type']
            if prefill.get('message'):
                self.message.default = prefill['message']
            self.show_dashboard.default = "yes" if prefill.get('show_dashboard', True) else "no"
            self.show_website.default = "yes" if prefill.get('show_website', True) else "no"

    async def on_submit(self, interaction: discord.Interaction):
        btype = self.banner_type.value.strip().lower()
        if btype not in self.VALID_TYPES:
            await interaction.response.send_message(
                f"Invalid type `{btype}`. Must be one of: {', '.join(self.VALID_TYPES)}",
                ephemeral=True,
            )
            return

        msg = self.message.value.strip()
        dash = self.show_dashboard.value.strip().lower() in ('yes', 'y', '1', 'true')
        web = self.show_website.value.strip().lower() in ('yes', 'y', '1', 'true')

        try:
            if self.banner_id is not None:
                row = await self.bot_db.update_banner(self.banner_id, msg, btype, None, None, dash, web)
                label = f"Banner `{self.banner_id}` updated."
            else:
                row = await self.bot_db.create_banner(msg, btype, None, None, dash, web)
                label = f"Banner `{row['id']}` created."
        except Exception as e:
            await interaction.response.send_message(f"Error: `{str(e)[:300]}`", ephemeral=True)
            return

        container = discord.ui.Container()
        container.add_item(discord.ui.TextDisplay(
            f"{EMOJIS['done']} **{label}**\n"
            f"**Type:** {btype} | **Dashboard:** {'Yes' if dash else 'No'} | **Website:** {'Yes' if web else 'No'}\n"
            f"**Message:** {msg[:200]}"
        ))
        view = discord.ui.LayoutView()
        view.add_item(container)
        await interaction.response.send_message(view=view, ephemeral=True)


class CustomBannerModal(discord.ui.Modal, title="Custom Banner"):
    """Modal for creating/editing a custom banner (icon_svg + color)."""

    message = discord.ui.TextInput(
        label="Message (Markdown supported)",
        style=discord.TextStyle.paragraph,
        max_length=1000,
    )
    icon_svg = discord.ui.TextInput(
        label="Icon SVG (raw SVG string)",
        style=discord.TextStyle.paragraph,
        max_length=2000,
    )
    color = discord.ui.TextInput(
        label="Color (hex #RRGGBB)",
        placeholder="#5865F2",
        max_length=7,
    )
    show_dashboard = discord.ui.TextInput(
        label="Show on Dashboard? (yes/no)",
        placeholder="yes",
        default="yes",
        max_length=3,
    )
    show_website = discord.ui.TextInput(
        label="Show on Website? (yes/no)",
        placeholder="yes",
        default="yes",
        max_length=3,
    )

    def __init__(self, bot_db, banner_id, prefill):
        super().__init__()
        self.bot_db = bot_db
        self.banner_id = banner_id
        if prefill:
            if prefill.get('message'):
                self.message.default = prefill['message']
            if prefill.get('icon_svg'):
                self.icon_svg.default = prefill['icon_svg']
            if prefill.get('color'):
                self.color.default = prefill['color']
            self.show_dashboard.default = "yes" if prefill.get('show_dashboard', True) else "no"
            self.show_website.default = "yes" if prefill.get('show_website', True) else "no"

    async def on_submit(self, interaction: discord.Interaction):
        import re
        msg = self.message.value.strip()
        svg = self.icon_svg.value.strip()
        color_val = self.color.value.strip()
        dash = self.show_dashboard.value.strip().lower() in ('yes', 'y', '1', 'true')
        web = self.show_website.value.strip().lower() in ('yes', 'y', '1', 'true')

        if not re.fullmatch(r'#[0-9A-Fa-f]{6}', color_val):
            await interaction.response.send_message(
                f"Invalid color `{color_val}`. Use hex format `#RRGGBB`.",
                ephemeral=True,
            )
            return

        try:
            if self.banner_id is not None:
                row = await self.bot_db.update_banner(self.banner_id, msg, None, svg, color_val, dash, web)
                label = f"Banner `{self.banner_id}` updated."
            else:
                row = await self.bot_db.create_banner(msg, None, svg, color_val, dash, web)
                label = f"Banner `{row['id']}` created."
        except Exception as e:
            await interaction.response.send_message(f"Error: `{str(e)[:300]}`", ephemeral=True)
            return

        container = discord.ui.Container()
        container.add_item(discord.ui.TextDisplay(
            f"{EMOJIS['done']} **{label}**\n"
            f"**Type:** custom | **Color:** {color_val} | **Dashboard:** {'Yes' if dash else 'No'} | **Website:** {'Yes' if web else 'No'}\n"
            f"**Message:** {msg[:200]}"
        ))
        view = discord.ui.LayoutView()
        view.add_item(container)
        await interaction.response.send_message(view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(DeveloperCommands(bot))
