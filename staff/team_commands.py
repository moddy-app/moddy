"""
Team Commands (t. prefix)
Common commands accessible to all staff members
"""

import discord
from discord.ext import commands
from typing import Optional
import logging
from datetime import datetime, timezone

from utils.staff_permissions import staff_permissions, CommandType
from database import db
from config import COLORS
from utils.components_v2 import create_error_message, create_success_message, create_info_message, create_warning_message, create_simple_message, EMOJIS
from utils.staff_base import StaffBaseCog

logger = logging.getLogger('moddy.team_commands')


def parse_user_id(args: str) -> Optional[int]:
    """
    Parse user ID from mention or direct ID
    Accepts: @user mention (<@123456>) or direct ID (123456)
    Returns: user ID as int, or None if invalid
    """
    if not args:
        return None

    args = args.strip()

    # Check if it's a mention (<@123456> or <@!123456>)
    if args.startswith('<@') and args.endswith('>'):
        # Remove <@ and >
        user_id_str = args[2:-1]
        # Remove ! if present (some mentions use <@!ID>)
        if user_id_str.startswith('!'):
            user_id_str = user_id_str[1:]
        try:
            return int(user_id_str)
        except ValueError:
            return None

    # Otherwise, try to parse as direct ID
    try:
        return int(args)
    except ValueError:
        return None


class TeamCommands(StaffBaseCog):
    """Team commands accessible to all staff (t. prefix)"""

    def __init__(self, bot):
        super().__init__(bot)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen for team commands with new syntax"""
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

        # Only handle team commands in this cog
        if command_type != CommandType.TEAM:
            return

        # Check permissions
        allowed, reason = await staff_permissions.check_command_permission(
            message.author.id, command_type, command_name
        )

        if not allowed:
            view = create_error_message("Permission Denied", reason)
            await self.reply_and_track(message, view=view, mention_author=False)
            return

        # Route to appropriate command
        if command_name == "invite":
            await self.handle_invite_command(message, args)
        elif command_name == "serverinfo":
            await self.handle_serverinfo_command(message, args)
        elif command_name == "help":
            await self.handle_help_command(message, args)
        elif command_name == "flex":
            await self.handle_flex_command(message, args)
        elif command_name == "mutualserver":
            await self.handle_mutualserver_command(message, args)
        elif command_name == "user":
            await self.handle_user_command(message, args)
        elif command_name == "server":
            await self.handle_server_command(message, args)
        else:
            view = create_error_message(
                "Unknown Command",
                f"Team command `{command_name}` not found.\n\nUse `<@1373916203814490194> t.help` for a list of available commands."
            )
            await self.reply_and_track(message, view=view, mention_author=False)

    async def handle_invite_command(self, message: discord.Message, args: str):
        """
        Handle t.invite command - Get an invite to a server
        Usage: <@1373916203814490194> t.invite [server_id]
        """
        if not args:
            view = create_error_message(
                "Invalid Usage",
                "**Usage:** `<@1373916203814490194> t.invite [server_id]`\n\nProvide a server ID to get an invite link."
            )
            await self.reply_and_track(message, view=view, mention_author=False)
            return

        # Parse server ID
        try:
            guild_id = int(args.strip())
        except ValueError:
            view = create_error_message(
                "Invalid Server ID",
                "Please provide a valid server ID (numbers only)."
            )
            await self.reply_and_track(message, view=view, mention_author=False)
            return

        # Get guild
        guild = self.bot.get_guild(guild_id)
        if not guild:
            view = create_error_message(
                "Server Not Found",
                f"MODDY is not in a server with ID `{guild_id}`."
            )
            await self.reply_and_track(message, view=view, mention_author=False)
            return

        # Try to create an invite
        try:
            # Find a suitable channel (preferably system channel or first text channel)
            invite_channel = guild.system_channel

            if not invite_channel:
                # Find first text channel where bot can create invites
                for channel in guild.text_channels:
                    if channel.permissions_for(guild.me).create_instant_invite:
                        invite_channel = channel
                        break

            if not invite_channel:
                view = create_error_message(
                    "Cannot Create Invite",
                    f"MODDY doesn't have permission to create invites in **{guild.name}**."
                )
                await self.reply_and_track(message, view=view, mention_author=False)
                return

            # Create invite (7 days, 5 uses, no temporary membership)
            invite = await invite_channel.create_invite(
                max_age=604800,  # 7 days
                max_uses=5,
                unique=True,
                reason=f"Staff invite requested by {message.author}"
            )

            # Create simple Components V2 view with only server name and invite link
            from discord.ui import LayoutView, Container, TextDisplay

            class InviteComponents(discord.ui.LayoutView):
                container1 = discord.ui.Container(
                    discord.ui.TextDisplay(content=f"**{guild.name}**"),
                    discord.ui.TextDisplay(content=f"{invite.url}"),
                )

            view = InviteComponents()

            await self.reply_and_track(message, view=view, mention_author=False)

            # Log the action
            logger.info(f"Staff {message.author} ({message.author.id}) requested invite for {guild.name} ({guild.id})")

        except discord.Forbidden:
            view = create_error_message(
                "Permission Denied",
                f"MODDY doesn't have permission to create invites in **{guild.name}**."
            )
            await self.reply_and_track(message, view=view, mention_author=False)

        except Exception as e:
            logger.error(f"Error creating invite: {e}")
            view = create_error_message(
                "Error",
                f"Failed to create invite: {str(e)}"
            )
            await self.reply_and_track(message, view=view, mention_author=False)

    async def handle_serverinfo_command(self, message: discord.Message, args: str):
        """
        Handle t.serverinfo command - Get information about a server
        Usage: <@1373916203814490194> t.serverinfo [server_id]
        """
        if not args:
            view = create_error_message(
                "Invalid Usage",
                "**Usage:** `<@1373916203814490194> t.serverinfo [server_id]`\n\nProvide a server ID to get information."
            )
            await self.reply_and_track(message, view=view, mention_author=False)
            return

        # Parse server ID
        try:
            guild_id = int(args.strip())
        except ValueError:
            view = create_error_message(
                "Invalid Server ID",
                "Please provide a valid server ID (numbers only)."
            )
            await self.reply_and_track(message, view=view, mention_author=False)
            return

        # Get guild
        guild = self.bot.get_guild(guild_id)
        if not guild:
            view = create_error_message(
                "Server Not Found",
                f"MODDY is not in a server with ID `{guild_id}`."
            )
            await self.reply_and_track(message, view=view, mention_author=False)
            return

        # Create info view
        fields = []

        # Basic info
        fields.append({
            'name': f"{EMOJIS['info']} Basic Information",
            'value': f"**Name:** {guild.name}\n**ID:** `{guild.id}`\n**Owner:** {guild.owner.mention if guild.owner else 'Unknown'} (`{guild.owner_id}`)\n**Created:** <t:{int(guild.created_at.timestamp())}:R>"
        })

        # Member stats
        fields.append({
            'name': f"{EMOJIS['user']} Members",
            'value': f"**Total:** {guild.member_count:,}\n**Humans:** {len([m for m in guild.members if not m.bot]):,}\n**Bots:** {len([m for m in guild.members if m.bot]):,}"
        })

        # Channel stats
        fields.append({
            'name': "Channels",
            'value': f"**Text:** {len(guild.text_channels)}\n**Voice:** {len(guild.voice_channels)}\n**Categories:** {len(guild.categories)}"
        })

        # Role count
        fields.append({
            'name': "Roles",
            'value': f"**Total:** {len(guild.roles)}"
        })

        # Boost info
        fields.append({
            'name': "Boost Status",
            'value': f"**Level:** {guild.premium_tier}\n**Boosts:** {guild.premium_subscription_count}"
        })

        # Features
        if guild.features:
            features = [f.replace('_', ' ').title() for f in guild.features[:10]]
            fields.append({
                'name': "Features",
                'value': ", ".join(features)
            })

        view = create_info_message(
            f"Server Information - {guild.name}",
            f"Detailed information about **{guild.name}**",
            fields=fields
        )

        await self.reply_and_track(message, view=view, mention_author=False)

    async def handle_help_command(self, message: discord.Message, args: str):
        """
        Handle t.help command - Show available team commands
        Usage: <@1373916203814490194> t.help
        """
        # Get user roles to show relevant commands
        user_roles = await staff_permissions.get_user_roles(message.author.id)

        fields = []

        # Team commands (available to all staff)
        team_commands = [
            ("t.help", "Show this help message"),
            ("t.invite [server_id]", "Get an invite link to a server"),
            ("t.serverinfo [server_id]", "Get detailed information about a server"),
            ("t.mutualserver [user_id]", "View mutual servers with a user and their permissions"),
            ("t.user [user_id]", "Get detailed information about a user"),
            ("t.server [server_id]", "Get detailed information about a server"),
            ("t.flex", "Prove you are a member of the Moddy team")
        ]

        fields.append({
            'name': f"{EMOJIS['commands']} Team Commands (All Staff)",
            'value': "\n".join([f"`<@1373916203814490194> {cmd}` - {desc}" for cmd, desc in team_commands])
        })

        # Management commands
        if await staff_permissions.can_use_command_type(message.author.id, CommandType.MANAGEMENT):
            mgmt_commands = [
                ("m.rank @user", "Add a user to the staff team"),
                ("m.setstaff @user", "Manage staff member permissions"),
                ("m.stafflist", "List all staff members"),
                ("m.staffinfo [@user]", "Show staff member information")
            ]

            fields.append({
                'name': "👑 Management Commands",
                'value': "\n".join([f"`<@1373916203814490194> {cmd}` - {desc}" for cmd, desc in mgmt_commands])
            })

        # Developer commands
        if await staff_permissions.can_use_command_type(message.author.id, CommandType.DEV):
            dev_commands = [
                ("d.reload [extension]", "Reload bot extensions"),
                ("d.shutdown", "Shutdown the bot"),
                ("d.stats", "Show bot statistics"),
                ("d.sql [query]", "Execute SQL query"),
                ("d.jsk [code]", "Execute Python code"),
                ("d.error [error_code]", "Get detailed error information")
            ]

            fields.append({
                'name': f"{EMOJIS['dev']} Developer Commands",
                'value': "\n".join([f"`<@1373916203814490194> {cmd}` - {desc}" for cmd, desc in dev_commands])
            })

        # Moderator commands
        if await staff_permissions.can_use_command_type(message.author.id, CommandType.MODERATOR):
            mod_commands = [
                ("mod.blacklist @user [reason]", "Blacklist a user"),
                ("mod.unblacklist @user [reason]", "Remove user from blacklist")
            ]

            fields.append({
                'name': "🛡️ Moderator Commands",
                'value': "\n".join([f"`<@1373916203814490194> {cmd}` - {desc}" for cmd, desc in mod_commands])
            })

        # Support commands
        if await staff_permissions.can_use_command_type(message.author.id, CommandType.SUPPORT):
            fields.append({
                'name': "🎧 Support Commands",
                'value': "Support commands are in development."
            })

        # Communication commands
        if await staff_permissions.can_use_command_type(message.author.id, CommandType.COMMUNICATION):
            fields.append({
                'name': "💬 Communication Commands",
                'value': "Communication commands are in development."
            })

        view = create_info_message(
            "MODDY Staff Commands",
            "Available staff commands based on your permissions.",
            fields=fields
        )

        await self.reply_and_track(message, view=view, mention_author=False)

    async def handle_flex_command(self, message: discord.Message, args: str):
        """
        Handle t.flex command - Prove staff membership on a server
        Usage: <@1373916203814490194> t.flex
        """
        # Get user roles
        user_roles = await staff_permissions.get_user_roles(message.author.id)

        if not user_roles:
            view = create_error_message(
                "Not a Staff Member",
                "You don't have any staff roles in the MODDY team."
            )
            await self.reply_and_track(message, view=view, mention_author=False)
            return

        # Get primary role (highest in hierarchy)
        primary_role = user_roles[0]

        # Format role name for display
        role_display = ""
        if primary_role.value == "Dev":
            role_display = "developer"
        elif primary_role.value == "Manager":
            role_display = "manager"
        elif primary_role.value == "Supervisor_Mod":
            role_display = "moderation supervisor"
        elif primary_role.value == "Supervisor_Com":
            role_display = "communication supervisor"  
        elif primary_role.value == "Supervisor_Sup":
            role_display = "support agents"  # Support supervisor
        elif primary_role.value == "Moderator":
            role_display = "moderator"
        elif primary_role.value == "Communication":
            role_display = "member"  # Communication shows as member
        elif primary_role.value == "Support":
            role_display = "support agents"  # Support shows as support agents
        else:
            role_display = "member"

        # Create the verification message with Components V2
        from discord.ui import LayoutView, Container, TextDisplay

        class Components(discord.ui.LayoutView):
            container1 = discord.ui.Container(
                discord.ui.TextDisplay(content=f"{EMOJIS['verified']} {message.author.mention} **is a {role_display} of the Moddy Team**"),
                discord.ui.TextDisplay(content="-# Moddy team are authorized to take action on your server.\n-# This message was sent to prevent identity theft. \n-# [Report Staff](https://moddy.app/report-staff) • [Support](https://moddy.app/support) • [Documentation](https://docs.moddy.app/)"),
            )

        view = Components()

        # Send in channel (not as reply) and delete command message
        try:
            await message.channel.send(view=view)
            await message.delete()

            # Log the action
            logger.info(f"Staff {message.author} ({message.author.id}) used t.flex in {message.guild.name if message.guild else 'DM'} ({message.guild.id if message.guild else 'N/A'})")

        except discord.Forbidden:
            view_error = create_error_message(
                "Permission Denied",
                "I don't have permission to send messages or delete messages in this channel."
            )
            await self.reply_and_track(message, view=view_error, mention_author=False)

        except Exception as e:
            logger.error(f"Error in t.flex command: {e}")
            view_error = create_error_message(
                "Error",
                f"Failed to send verification message: {str(e)}"
            )
            await self.reply_and_track(message, view=view_error, mention_author=False)

    async def handle_mutualserver_command(self, message: discord.Message, args: str):
        """
        Handle t.mutualserver command - View mutual servers with a user
        Usage: <@1373916203814490194> t.mutualserver [user_id or @user]
        """
        if not args:
            view = create_error_message(
                "Invalid Usage",
                "**Usage:** `<@1373916203814490194> t.mutualserver [user_id]` or `<@1373916203814490194> t.mutualserver @user`\n\nProvide a user ID or mention to view mutual servers."
            )
            await self.reply_and_track(message, view=view, mention_author=False)
            return

        # Parse user ID
        user_id = parse_user_id(args)
        if user_id is None:
            view = create_error_message(
                "Invalid User ID",
                "Please provide a valid user ID or mention a user."
            )
            await self.reply_and_track(message, view=view, mention_author=False)
            return

        # Try to fetch user
        try:
            user = await self.bot.fetch_user(user_id)
        except discord.NotFound:
            view = create_error_message(
                "User Not Found",
                f"Could not find a user with ID `{user_id}`."
            )
            await self.reply_and_track(message, view=view, mention_author=False)
            return
        except Exception as e:
            logger.error(f"Error fetching user {user_id}: {e}")
            view = create_error_message(
                "Error",
                f"Failed to fetch user: {str(e)}"
            )
            await self.reply_and_track(message, view=view, mention_author=False)
            return

        # Find mutual servers
        mutual_guilds = [g for g in self.bot.guilds if g.get_member(user_id)]

        if not mutual_guilds:
            view = create_info_message(
                "No Mutual Servers",
                f"MODDY and **{user}** (`{user_id}`) don't share any servers."
            )
            await self.reply_and_track(message, view=view, mention_author=False)
            return

        # Create fields for each mutual server
        fields = []
        fields.append({
            'name': f"{EMOJIS['user']} User Information",
            'value': f"**Username:** {user}\n**ID:** `{user_id}`"
        })

        # Limit to first 10 servers to avoid message being too long
        for guild in mutual_guilds[:10]:
            member = guild.get_member(user_id)
            if not member:
                continue

            # Get permissions in the guild
            permissions = []
            if member.guild_permissions.administrator:
                permissions.append("Administrator")
            else:
                if member.guild_permissions.manage_guild:
                    permissions.append("Manage Server")
                if member.guild_permissions.manage_channels:
                    permissions.append("Manage Channels")
                if member.guild_permissions.manage_roles:
                    permissions.append("Manage Roles")
                if member.guild_permissions.kick_members:
                    permissions.append("Kick Members")
                if member.guild_permissions.ban_members:
                    permissions.append("Ban Members")
                if member.guild_permissions.moderate_members:
                    permissions.append("Timeout Members")

            perms_text = ", ".join(permissions) if permissions else "No special permissions"

            # Get top role
            top_role = member.top_role.name if member.top_role.name != "@everyone" else "No roles"

            fields.append({
                'name': f"{EMOJIS['web']} {guild.name}",
                'value': f"**ID:** `{guild.id}`\n**Top Role:** {top_role}\n**Permissions:** {perms_text}"
            })

        if len(mutual_guilds) > 10:
            fields.append({
                'name': "Additional Servers",
                'value': f"*...and {len(mutual_guilds) - 10} more servers*"
            })

        view = create_info_message(
            f"Mutual Servers - {user}",
            f"Found **{len(mutual_guilds)}** mutual server(s) with **{user}**",
            fields=fields
        )

        await self.reply_and_track(message, view=view, mention_author=False)

    async def handle_user_command(self, message: discord.Message, args: str):
        """
        Handle t.user command - Get detailed user information
        Usage: <@1373916203814490194> t.user [user_id or @user]
        """
        if not args:
            view = create_error_message(
                "Invalid Usage",
                "**Usage:** `<@1373916203814490194> t.user [user_id]` or `<@1373916203814490194> t.user @user`\n\nProvide a user ID or mention to get information."
            )
            await self.reply_and_track(message, view=view, mention_author=False)
            return

        # Parse user ID
        user_id = parse_user_id(args)
        if user_id is None:
            view = create_error_message(
                "Invalid User ID",
                "Please provide a valid user ID or mention a user."
            )
            await self.reply_and_track(message, view=view, mention_author=False)
            return

        # Try to fetch user
        try:
            user = await self.bot.fetch_user(user_id)
        except discord.NotFound:
            view = create_error_message(
                "User Not Found",
                f"Could not find a user with ID `{user_id}`."
            )
            await self.reply_and_track(message, view=view, mention_author=False)
            return
        except Exception as e:
            logger.error(f"Error fetching user {user_id}: {e}")
            view = create_error_message(
                "Error",
                f"Failed to fetch user: {str(e)}"
            )
            await self.reply_and_track(message, view=view, mention_author=False)
            return

        # Get user data from database
        user_data = await db.get_user(user_id)

        fields = []

        # Basic info
        fields.append({
            'name': f"{EMOJIS['user']} Basic Information",
            'value': f"**ID:** `{user.id}`\n**Username:** {user.name}\n**Display Name:** {user.display_name}\n**Bot:** {'Yes' if user.bot else 'No'}\n**Created:** <t:{int(user.created_at.timestamp())}:R>"
        })

        # Attributes
        attributes = user_data['attributes']
        if attributes:
            attr_list = []
            for key, value in attributes.items():
                if value is True:
                    attr_list.append(f"• `{key}`")
                else:
                    attr_list.append(f"• `{key}`: {value}")

            fields.append({
                'name': "Attributes",
                'value': "\n".join(attr_list) if attr_list else "*None*"
            })
        else:
            fields.append({
                'name': "Attributes",
                'value': "*None*"
            })

        # Shared servers
        guilds = [g for g in self.bot.guilds if g.get_member(user_id)]
        fields.append({
            'name': f"{EMOJIS['web']} Shared Servers",
            'value': f"{len(guilds)} server(s)"
        })

        # Database timestamps
        if user_data.get('created_at'):
            fields.append({
                'name': f"{EMOJIS['time']} First Seen",
                'value': f"<t:{int(user_data['created_at'].timestamp())}:R>"
            })

        view = create_info_message(
            f"User Information - {str(user)}",
            f"Information about **{user}**",
            fields=fields
        )

        await self.reply_and_track(message, view=view, mention_author=False)

    async def handle_server_command(self, message: discord.Message, args: str):
        """
        Handle t.server command - Get detailed server information
        Usage: <@1373916203814490194> t.server [server_id]
        """
        if not args:
            view = create_error_message(
                "Invalid Usage",
                "**Usage:** `<@1373916203814490194> t.server [server_id]`\n\nProvide a server ID to get information."
            )
            await self.reply_and_track(message, view=view, mention_author=False)
            return

        # Parse server ID
        try:
            guild_id = int(args.strip())
        except ValueError:
            view = create_error_message(
                "Invalid Server ID",
                "Please provide a valid server ID (numbers only)."
            )
            await self.reply_and_track(message, view=view, mention_author=False)
            return

        # Get guild
        guild = self.bot.get_guild(guild_id)
        if not guild:
            view = create_error_message(
                "Server Not Found",
                f"MODDY is not in a server with ID `{guild_id}`."
            )
            await self.reply_and_track(message, view=view, mention_author=False)
            return

        # Get guild data from database
        guild_data = await db.get_guild(guild_id)

        fields = []

        # Basic info
        fields.append({
            'name': f"{EMOJIS['info']} Basic Information",
            'value': f"**Name:** {guild.name}\n**ID:** `{guild.id}`\n**Owner:** {guild.owner.mention if guild.owner else 'Unknown'} (`{guild.owner_id}`)\n**Created:** <t:{int(guild.created_at.timestamp())}:R>"
        })

        # Members
        fields.append({
            'name': f"{EMOJIS['user']} Members",
            'value': f"**Total:** {guild.member_count:,}\n**Humans:** {len([m for m in guild.members if not m.bot]):,}\n**Bots:** {len([m for m in guild.members if m.bot]):,}"
        })

        # Channels
        fields.append({
            'name': "Channels",
            'value': f"**Text:** {len(guild.text_channels)}\n**Voice:** {len(guild.voice_channels)}\n**Categories:** {len(guild.categories)}"
        })

        # Roles
        fields.append({
            'name': "Roles",
            'value': f"**Total:** {len(guild.roles)}"
        })

        # Boost
        fields.append({
            'name': "Boost Status",
            'value': f"**Level:** {guild.premium_tier}\n**Boosts:** {guild.premium_subscription_count}"
        })

        # Attributes
        attributes = guild_data['attributes']
        if attributes:
            attr_list = []
            for key, value in attributes.items():
                if value is True:
                    attr_list.append(f"• `{key}`")
                else:
                    attr_list.append(f"• `{key}`: {value}")

            fields.append({
                'name': "Attributes",
                'value': "\n".join(attr_list)
            })

        # Features
        if guild.features:
            features = [f.replace('_', ' ').title() for f in guild.features[:10]]
            fields.append({
                'name': "Features",
                'value': ", ".join(features)
            })

        view = create_info_message(
            f"Server Information - {guild.name}",
            f"Detailed information about **{guild.name}**",
            fields=fields
        )

        await self.reply_and_track(message, view=view, mention_author=False)


async def setup(bot):
    await bot.add_cog(TeamCommands(bot))
