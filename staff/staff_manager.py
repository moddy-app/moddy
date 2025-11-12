"""
Staff Management Commands
Commands for managing staff members, roles, and permissions
"""

import discord
from discord.ext import commands
from discord import ui
from discord.ui import LayoutView, Container, TextDisplay, Separator, ActionRow, Select, Button
from discord import SeparatorSpacing, SelectOption, ButtonStyle
from typing import Optional, List, Dict
import logging
import json
from datetime import datetime, timezone

from utils.staff_permissions import staff_permissions, StaffRole, CommandType
from database import db
from config import COLORS
from utils.components_v2 import (
    create_error_message,
    create_success_message,
    create_info_message,
    create_warning_message,
    create_staff_info_message,
    create_simple_message,
    EMOJIS
)
from utils.staff_role_permissions import (
    COMMON_PERMISSIONS,
    ROLE_PERMISSIONS_MAP,
    get_permission_label,
    get_role_display_name
)
from utils.staff_base import StaffBaseCog

logger = logging.getLogger('moddy.staff_manager')


class RoleSelectView(ui.View):
    """View for selecting staff roles - Uses ui.View to handle interactions"""

    def __init__(self, target_user: discord.User, modifier: discord.User, perm_manager):
        super().__init__(timeout=300)
        self.target_user = target_user
        self.modifier = modifier
        self.perm_manager = perm_manager
        self.selected_roles: List[StaffRole] = []

        # Add select menu
        select = ui.Select(
            placeholder="Select roles for this staff member",
            min_values=0,
            max_values=7,
            custom_id="role_select_menu",
            options=[
                discord.SelectOption(
                    label="Manager",
                    value=StaffRole.MANAGER.value,
                    description="Can manage all staff and assign roles",
                    emoji="👑"
                ),
                discord.SelectOption(
                    label="Moderator Supervisor",
                    value=StaffRole.SUPERVISOR_MOD.value,
                    description="Supervises moderators",
                    emoji="🛡️"
                ),
                discord.SelectOption(
                    label="Communication Supervisor",
                    value=StaffRole.SUPERVISOR_COM.value,
                    description="Supervises communication team",
                    emoji="📢"
                ),
                discord.SelectOption(
                    label="Support Supervisor",
                    value=StaffRole.SUPERVISOR_SUP.value,
                    description="Supervises support team",
                    emoji="🎫"
                ),
                discord.SelectOption(
                    label="Moderator",
                    value=StaffRole.MODERATOR.value,
                    description="Moderation staff member",
                    emoji="🔨"
                ),
                discord.SelectOption(
                    label="Communication",
                    value=StaffRole.COMMUNICATION.value,
                    description="Communication staff member",
                    emoji="💬"
                ),
                discord.SelectOption(
                    label="Support",
                    value=StaffRole.SUPPORT.value,
                    description="Support staff member",
                    emoji="🎧"
                )
            ]
        )
        select.callback = self.role_select
        self.add_item(select)

        # Add confirm button
        confirm_btn = ui.Button(label="Confirm", style=discord.ButtonStyle.green, emoji="✅", custom_id="confirm_roles")
        confirm_btn.callback = self.confirm_button
        self.add_item(confirm_btn)

        # Add cancel button
        cancel_btn = ui.Button(label="Cancel", style=discord.ButtonStyle.red, emoji="❌", custom_id="cancel_roles")
        cancel_btn.callback = self.cancel_button
        self.add_item(cancel_btn)

    async def role_select(self, interaction: discord.Interaction):
        """Handle role selection"""
        # Verify it's the modifier
        if interaction.user.id != self.modifier.id:
            await interaction.response.send_message(
                "❌ Only the command initiator can use this menu.",
                ephemeral=True
            )
            return

        # Get the select component
        select_component = [item for item in self.children if isinstance(item, ui.Select)][0]

        # Convert values to StaffRole enums
        self.selected_roles = [StaffRole(v) for v in select_component.values]

        # Check if modifier can assign all selected roles
        invalid_roles = []
        for role in self.selected_roles:
            if not await self.perm_manager.can_assign_role(self.modifier.id, role):
                invalid_roles.append(role)

        if invalid_roles:
            await interaction.response.send_message(
                f"❌ You cannot assign the following roles: {', '.join([r.value for r in invalid_roles])}",
                ephemeral=True
            )
            return

        await interaction.response.defer()

    async def confirm_button(self, interaction: discord.Interaction):
        """Confirm role assignment"""
        if interaction.user.id != self.modifier.id:
            await interaction.response.send_message(
                "❌ Only the command initiator can use this button.",
                ephemeral=True
            )
            return

        if not self.selected_roles:
            await interaction.response.send_message(
                "❌ Please select at least one role.",
                ephemeral=True
            )
            return

        # Save roles to database
        try:
            role_values = [role.value for role in self.selected_roles]
            await db.set_staff_roles(self.target_user.id, role_values, self.modifier.id)

            # Create success view
            fields = [{
                'name': 'Roles Assigned',
                'value': "\n".join([f"• {role.value}" for role in self.selected_roles])
            }]

            view = create_success_message(
                "Staff Roles Updated",
                f"Roles for {self.target_user.mention} have been updated.",
                fields=fields
            )

            await interaction.response.edit_message(view=view, content=None)

        except Exception as e:
            logger.error(f"Error assigning roles: {e}")
            await interaction.response.send_message(
                f"❌ Error assigning roles: {str(e)}",
                ephemeral=True
            )

    async def cancel_button(self, interaction: discord.Interaction):
        """Cancel role assignment"""
        if interaction.user.id != self.modifier.id:
            await interaction.response.send_message(
                f"{EMOJIS['undone']} Only the command initiator can use this button.",
                ephemeral=True
            )
            return

        view = create_error_message("Cancelled", "Role assignment cancelled.")

        await interaction.response.edit_message(view=view, content=None)


class StaffPermissionsManagementView(ui.View):
    """View for managing staff permissions with role-based permission system"""

    def __init__(self, bot, target_user: discord.User, modifier: discord.User, perm_manager, initial_message: discord.Message):
        super().__init__(timeout=600)  # 10 minutes
        self.bot = bot
        self.target_user = target_user
        self.modifier = modifier
        self.perm_manager = perm_manager
        self.initial_message = initial_message

        # We'll add the select menus dynamically based on current roles
        self.selected_roles: List[StaffRole] = []
        self.role_permissions: Dict[str, List[str]] = {}  # role_name -> [permissions]
        self.common_permissions: List[str] = []

    async def initialize(self):
        """Initialize the view with current permissions"""
        # Get current permissions
        perms = await db.get_staff_permissions(self.target_user.id)
        self.selected_roles = [StaffRole(r) for r in perms['roles']] if perms['roles'] else []
        role_perms_data = perms.get('role_permissions', {})

        # Load role permissions
        for role in self.selected_roles:
            self.role_permissions[role.value] = role_perms_data.get(role.value, [])

        self.common_permissions = role_perms_data.get('common', [])

        # Build the view
        await self.rebuild_view()

    async def rebuild_view(self):
        """Rebuild the view with current state"""
        # Clear all items
        self.clear_items()

        # Add role selection menu
        role_select = ui.Select(
            placeholder="Select roles for this staff member",
            min_values=0,
            max_values=7,
            custom_id="role_select",
            options=[
                discord.SelectOption(
                    label="Manager",
                    value=StaffRole.MANAGER.value,
                    description="Can manage all staff and assign roles",
                    emoji="👑",
                    default=StaffRole.MANAGER in self.selected_roles
                ),
                discord.SelectOption(
                    label="Moderator Supervisor",
                    value=StaffRole.SUPERVISOR_MOD.value,
                    description="Supervises moderators",
                    emoji="🛡️",
                    default=StaffRole.SUPERVISOR_MOD in self.selected_roles
                ),
                discord.SelectOption(
                    label="Communication Supervisor",
                    value=StaffRole.SUPERVISOR_COM.value,
                    description="Supervises communication team",
                    emoji="📢",
                    default=StaffRole.SUPERVISOR_COM in self.selected_roles
                ),
                discord.SelectOption(
                    label="Support Supervisor",
                    value=StaffRole.SUPERVISOR_SUP.value,
                    description="Supervises support team",
                    emoji="🎫",
                    default=StaffRole.SUPERVISOR_SUP in self.selected_roles
                ),
                discord.SelectOption(
                    label="Moderator",
                    value=StaffRole.MODERATOR.value,
                    description="Moderation staff member",
                    emoji="🔨",
                    default=StaffRole.MODERATOR in self.selected_roles
                ),
                discord.SelectOption(
                    label="Communication",
                    value=StaffRole.COMMUNICATION.value,
                    description="Communication staff member",
                    emoji="💬",
                    default=StaffRole.COMMUNICATION in self.selected_roles
                ),
                discord.SelectOption(
                    label="Support",
                    value=StaffRole.SUPPORT.value,
                    description="Support staff member",
                    emoji="🎧",
                    default=StaffRole.SUPPORT in self.selected_roles
                )
            ]
        )
        role_select.callback = self.role_select_callback
        self.add_item(role_select)

        # Add common permissions select if there are roles
        if self.selected_roles:
            common_select = ui.Select(
                placeholder="Select common permissions (all roles)",
                min_values=0,
                max_values=len(COMMON_PERMISSIONS),
                custom_id="common_permissions",
                options=[
                    discord.SelectOption(
                        label=get_permission_label(perm),
                        value=perm,
                        default=perm in self.common_permissions
                    ) for perm in COMMON_PERMISSIONS
                ]
            )
            common_select.callback = self.common_permissions_callback
            self.add_item(common_select)

            # Add permission select for each role
            for role in self.selected_roles:
                available_perms = ROLE_PERMISSIONS_MAP.get(role.value, [])
                if available_perms:
                    role_perm_select = ui.Select(
                        placeholder=f"Permissions for {get_role_display_name(role.value)}",
                        min_values=0,
                        max_values=len(available_perms),
                        custom_id=f"perms_{role.value}",
                        options=[
                            discord.SelectOption(
                                label=get_permission_label(perm),
                                value=perm,
                                default=perm in self.role_permissions.get(role.value, [])
                            ) for perm in available_perms
                        ]
                    )
                    role_perm_select.callback = self.create_role_permission_callback(role.value)
                    self.add_item(role_perm_select)

        # Add save button
        save_btn = ui.Button(label="Save Changes", style=discord.ButtonStyle.green, emoji="✅")
        save_btn.callback = self.save_callback
        self.add_item(save_btn)

        # Add cancel button
        cancel_btn = ui.Button(label="Cancel", style=discord.ButtonStyle.red, emoji="❌")
        cancel_btn.callback = self.cancel_callback
        self.add_item(cancel_btn)

    async def role_select_callback(self, interaction: discord.Interaction):
        """Handle role selection changes"""
        if interaction.user.id != self.modifier.id:
            await interaction.response.send_message(
                "❌ Only the command initiator can use this menu.",
                ephemeral=True
            )
            return

        # Get selected roles
        select = [item for item in self.children if isinstance(item, ui.Select) and item.custom_id == "role_select"][0]
        new_roles = [StaffRole(v) for v in select.values]

        # Check permissions
        invalid_roles = []
        for role in new_roles:
            if not await self.perm_manager.can_assign_role(self.modifier.id, role):
                invalid_roles.append(role)

        if invalid_roles:
            await interaction.response.send_message(
                f"❌ You cannot assign the following roles: {', '.join([r.value for r in invalid_roles])}",
                ephemeral=True
            )
            return

        # Update selected roles
        self.selected_roles = new_roles

        # Reset role permissions for removed roles
        current_role_values = [r.value for r in new_roles]
        self.role_permissions = {k: v for k, v in self.role_permissions.items() if k in current_role_values}

        # Add empty permission lists for new roles
        for role in new_roles:
            if role.value not in self.role_permissions:
                self.role_permissions[role.value] = []

        # Rebuild the view
        await self.rebuild_view()

        # Update the message
        layout_view = await self.create_layout_view()
        await interaction.response.edit_message(view=layout_view)
        # Re-send the interactive view
        await interaction.followup.send(view=self, ephemeral=True)

    async def common_permissions_callback(self, interaction: discord.Interaction):
        """Handle common permissions selection"""
        if interaction.user.id != self.modifier.id:
            await interaction.response.send_message(
                "❌ Only the command initiator can use this menu.",
                ephemeral=True
            )
            return

        select = [item for item in self.children if isinstance(item, ui.Select) and item.custom_id == "common_permissions"][0]
        self.common_permissions = select.values

        await interaction.response.defer()

    def create_role_permission_callback(self, role_name: str):
        """Create a callback for a specific role's permissions"""
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.modifier.id:
                await interaction.response.send_message(
                    "❌ Only the command initiator can use this menu.",
                    ephemeral=True
                )
                return

            select = [item for item in self.children if isinstance(item, ui.Select) and item.custom_id == f"perms_{role_name}"][0]
            self.role_permissions[role_name] = select.values

            await interaction.response.defer()

        return callback

    async def save_callback(self, interaction: discord.Interaction):
        """Save all changes"""
        if interaction.user.id != self.modifier.id:
            await interaction.response.send_message(
                "❌ Only the command initiator can use this button.",
                ephemeral=True
            )
            return

        try:
            # Save roles
            role_values = [role.value for role in self.selected_roles]
            await db.set_staff_roles(self.target_user.id, role_values, self.modifier.id)

            # Save role permissions
            all_role_perms = dict(self.role_permissions)
            all_role_perms['common'] = self.common_permissions

            async with db.pool.acquire() as conn:
                await conn.execute("""
                    UPDATE staff_permissions
                    SET role_permissions = $1, updated_by = $2, updated_at = NOW()
                    WHERE user_id = $3
                """, json.dumps(all_role_perms), self.modifier.id, self.target_user.id)

            # Create success view
            view = create_success_message(
                "Staff Permissions Updated",
                f"Permissions for {self.target_user.mention} have been successfully updated."
            )

            await interaction.response.send_message(view=view, ephemeral=True)

            # Update the original message to show final state
            layout_view = await self.create_layout_view(final=True)
            await self.initial_message.edit(view=layout_view)

        except Exception as e:
            logger.error(f"Error saving staff permissions: {e}")
            await interaction.response.send_message(
                f"❌ Error saving permissions: {str(e)}",
                ephemeral=True
            )

    async def cancel_callback(self, interaction: discord.Interaction):
        """Cancel changes"""
        if interaction.user.id != self.modifier.id:
            await interaction.response.send_message(
                "❌ Only the command initiator can use this button.",
                ephemeral=True
            )
            return

        view = create_error_message("Cancelled", "Permission changes cancelled.")
        await interaction.response.send_message(view=view, ephemeral=True)

    async def create_layout_view(self, final: bool = False):
        """Create the layout view showing current state"""
        # Build role display with badges
        role_display_lines = []
        if self.selected_roles:
            for role in self.selected_roles:
                badge = ""
                if role == StaffRole.DEV:
                    badge = EMOJIS['dev_badge']
                elif role == StaffRole.MANAGER:
                    badge = EMOJIS['manager_badge']
                elif role == StaffRole.SUPERVISOR_MOD:
                    badge = EMOJIS['mod_supervisor_badge']
                elif role == StaffRole.SUPERVISOR_COM:
                    badge = EMOJIS['communication_supervisor_badge']
                elif role == StaffRole.SUPERVISOR_SUP:
                    badge = EMOJIS['support_supervisor_badge']
                elif role == StaffRole.MODERATOR:
                    badge = EMOJIS['moderator_badge']
                elif role == StaffRole.COMMUNICATION:
                    badge = EMOJIS['comunication_badge']
                elif role == StaffRole.SUPPORT:
                    badge = EMOJIS['supportagent_badge']

                role_display_lines.append(f"{badge} {role.value}")

                # Show permissions for this role
                perms = self.role_permissions.get(role.value, [])
                if perms:
                    role_display_lines.append(f"  └ Permissions: {', '.join([get_permission_label(p) for p in perms])}")
                else:
                    role_display_lines.append(f"  └ No permissions assigned")
        else:
            role_display_lines.append("*No roles assigned*")

        # Build container
        container_components = [
            discord.ui.TextDisplay(content=f"{EMOJIS['settings']} **Staff Permissions Management**\nManaging {self.target_user.mention}"),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            discord.ui.TextDisplay(content=f"**Roles & Permissions**\n" + "\n".join(role_display_lines)),
        ]

        # Show common permissions if any
        if self.common_permissions:
            container_components.extend([
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                discord.ui.TextDisplay(content=f"**Common Permissions (All Roles)**\n" + ", ".join([get_permission_label(p) for p in self.common_permissions]))
            ])

        if not final:
            container_components.extend([
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                discord.ui.TextDisplay(content="*Use the menus below to configure roles and permissions*")
            ])

        class PermissionsLayout(discord.ui.LayoutView):
            container1 = discord.ui.Container(*container_components)

        return PermissionsLayout()


class DenyCommandModal(ui.Modal, title="Deny Specific Commands"):
    """Modal for denying specific commands"""

    command_input = ui.TextInput(
        label="Commands to deny (one per line)",
        style=discord.TextStyle.paragraph,
        placeholder="mod.ban\nmod.kick\nsup.ticket",
        required=False
    )

    def __init__(self, target_user: discord.User, modifier: discord.User):
        super().__init__()
        self.target_user = target_user
        self.modifier = modifier

    async def on_submit(self, interaction: discord.Interaction):
        """Handle command denial submission"""
        commands = [cmd.strip() for cmd in self.command_input.value.split('\n') if cmd.strip()]

        try:
            await db.set_denied_commands(self.target_user.id, commands, self.modifier.id)

            denied_value = "\n".join([f"• `{cmd}`" for cmd in commands]) if commands else "None - All restrictions removed"

            fields = [{
                'name': 'Denied Commands',
                'value': denied_value
            }]

            view = create_success_message(
                "Command Restrictions Updated",
                f"Command restrictions for {self.target_user.mention} have been updated.",
                fields=fields
            )

            await interaction.response.send_message(view=view, ephemeral=True)

        except Exception as e:
            logger.error(f"Error setting denied commands: {e}")
            await interaction.response.send_message(
                f"❌ Error updating command restrictions: {str(e)}",
                ephemeral=True
            )


class StaffManagement(StaffBaseCog):
    """Staff management commands (m. prefix)"""

    def __init__(self, bot):
        super().__init__(bot)
        # Store pending interactions context
        self.interaction_contexts = {}

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        """Handle button interactions from Components V2"""
        if interaction.type != discord.InteractionType.component:
            return

        custom_id = interaction.data.get('custom_id', '')

        # Handle edit roles button
        if custom_id.startswith('edit_roles_'):
            # Extract session_id (everything after 'edit_roles_')
            session_id = custom_id[len('edit_roles_'):]

            # Get context if stored
            context = self.interaction_contexts.get(session_id)
            if not context:
                await interaction.response.send_message("❌ Session expired. Please run the command again.", ephemeral=True)
                return

            if interaction.user.id != context['modifier_id']:
                await interaction.response.send_message(f"{EMOJIS['undone']} Only the command initiator can use this.", ephemeral=True)
                return

            target_user = await self.bot.fetch_user(context['target_id'])
            modifier = await self.bot.fetch_user(context['modifier_id'])

            role_view = RoleSelectView(target_user, modifier, staff_permissions)

            # Create Components V2 layout for role selection
            class RoleSelectLayout(discord.ui.LayoutView):
                container1 = discord.ui.Container(
                    discord.ui.TextDisplay(content=f"{EMOJIS['settings']} **Edit Roles**\nEditing roles for {target_user.mention}\n\nSelect the new roles below:"),
                )

            layout = RoleSelectLayout()
            await interaction.response.send_message(view=layout, ephemeral=True)
            # Also send the interactive view
            await interaction.followup.send(view=role_view, ephemeral=True)

        # Handle manage restrictions button
        elif custom_id.startswith('manage_restrictions_'):
            # Extract session_id (everything after 'manage_restrictions_')
            session_id = custom_id[len('manage_restrictions_'):]

            # Get context if stored
            context = self.interaction_contexts.get(session_id)
            if not context:
                await interaction.response.send_message("❌ Session expired. Please run the command again.", ephemeral=True)
                return

            if interaction.user.id != context['modifier_id']:
                await interaction.response.send_message("❌ Only the command initiator can use this.", ephemeral=True)
                return

            target_user = await self.bot.fetch_user(context['target_id'])
            modifier = await self.bot.fetch_user(context['modifier_id'])

            modal = DenyCommandModal(target_user, modifier)
            await interaction.response.send_modal(modal)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen for staff commands with new syntax"""
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

        # Only handle management commands in this cog
        if command_type != CommandType.MANAGEMENT:
            return

        # Log the command attempt
        logger.info(f"👑 Management command '{command_name}' attempted by {message.author} ({message.author.id})")

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
            await message.reply(view=view, mention_author=False)
            return

        logger.info(f"   ✅ Permission granted")

        # Route to appropriate command
        if command_name == "rank":
            await self.handle_rank_command(message, args)
        elif command_name == "unrank":
            await self.handle_unrank_command(message, args)
        elif command_name == "setstaff":
            await self.handle_setstaff_command(message, args)
        elif command_name == "stafflist":
            await self.handle_stafflist_command(message, args)
        elif command_name == "staffinfo":
            await self.handle_staffinfo_command(message, args)
        else:
            view = create_error_message("Unknown Command", f"Management command `{command_name}` not found.")
            await message.reply(view=view, mention_author=False)

    async def handle_rank_command(self, message: discord.Message, args: str):
        """
        Handle m.rank command - Add user to staff team
        Usage: <@1373916203814490194> m.rank @user
        """
        # Parse user mention or ID
        target_user = None

        # Try to get user from mentions (exclude bot mention)
        for mention in message.mentions:
            if mention.id != self.bot.user.id:
                target_user = mention
                break

        # If no mention found, try to parse as ID
        if not target_user and args:
            try:
                user_id = int(args.strip().split()[0])
                target_user = await self.bot.fetch_user(user_id)
            except (ValueError, discord.NotFound, discord.HTTPException):
                pass

        if not target_user:
            view = create_error_message(
                "Invalid Usage",
                "**Usage:** `<@1373916203814490194> m.rank @user` or `<@1373916203814490194> m.rank [user_id]`\n\nMention a user or provide their ID to add them to the staff team."
            )
            await message.reply(view=view, mention_author=False)
            return

        # Can't rank bots
        if target_user.bot:
            view = create_error_message("Invalid Target", "You cannot add bots to the staff team.")
            await message.reply(view=view, mention_author=False)
            return

        # Check if user is already staff
        user_data = await db.get_user(target_user.id)
        if user_data['attributes'].get('TEAM'):
            view = create_warning_message("Already Staff", f"{target_user.mention} is already a staff member.")
            await message.reply(view=view, mention_author=False)
            return

        # Open role selection with Components V2
        button_view = RoleSelectView(target_user, message.author, staff_permissions)

        # Create Components V2 layout
        class RankLayout(discord.ui.LayoutView):
            container1 = discord.ui.Container(
                discord.ui.TextDisplay(content=f"{EMOJIS['user']} **Add Staff Member**\nAdding {target_user.mention} to the staff team.\n\nSelect the roles for this staff member:"),
            )

        layout = RankLayout()

        # Send Components V2 layout
        await self.reply_and_track(message, view=layout, mention_author=False)
        # Send interactive view as followup
        await message.channel.send(view=button_view)

    async def handle_unrank_command(self, message: discord.Message, args: str):
        """
        Handle m.unrank command - Remove user from staff team
        Usage: <@1373916203814490194> m.unrank @user
        """
        # Parse user mention or ID
        target_user = None

        # Try to get user from mentions (exclude bot mention)
        for mention in message.mentions:
            if mention.id != self.bot.user.id:
                target_user = mention
                break

        # If no mention found, try to parse as ID
        if not target_user and args:
            try:
                user_id = int(args.strip().split()[0])
                target_user = await self.bot.fetch_user(user_id)
            except (ValueError, discord.NotFound, discord.HTTPException):
                pass

        if not target_user:
            view = create_error_message(
                "Invalid Usage",
                "**Usage:** `<@1373916203814490194> m.unrank @user` or `<@1373916203814490194> m.unrank [user_id]`\n\nMention a user or provide their ID to remove them from the staff team."
            )
            await message.reply(view=view, mention_author=False)
            return

        # Check if target is staff
        user_data = await db.get_user(target_user.id)
        if not user_data['attributes'].get('TEAM'):
            view = create_error_message(
                "Not Staff",
                f"{target_user.mention} is not a staff member."
            )
            await message.reply(view=view, mention_author=False)
            return

        # Check if modifier can modify target
        can_modify = await staff_permissions.can_modify_user(message.author.id, target_user.id)
        if not can_modify:
            view = create_error_message(
                "Permission Denied",
                "You cannot remove this user from the staff team.\n\nYou can only modify staff members below your hierarchy level."
            )
            await message.reply(view=view, mention_author=False)
            return

        # Remove all roles and TEAM attribute
        try:
            # Remove staff permissions (clears roles and denied commands)
            await db.remove_staff_permissions(target_user.id)

            # Remove TEAM attribute
            await db.set_attribute('user', target_user.id, 'TEAM', False, message.author.id, "Removed from staff via m.unrank")

            # Create success message
            view = create_success_message(
                f"Staff Member Removed",
                f"{target_user.mention} has been removed from the staff team."
            )

            await self.reply_and_track(message, view=view, mention_author=False)

            # Log the action
            logger.info(f"Staff {message.author} ({message.author.id}) removed {target_user} ({target_user.id}) from staff")

        except Exception as e:
            logger.error(f"Error removing staff member: {e}")
            view = create_error_message(
                "Error",
                f"Failed to remove staff member: {str(e)}"
            )
            await message.reply(view=view, mention_author=False)

    async def handle_setstaff_command(self, message: discord.Message, args: str):
        """
        Handle m.setstaff command - Manage staff permissions with role-based permissions
        Usage: <@1373916203814490194> m.setstaff @user
        """
        # Parse user mention or ID
        target_user = None

        # Try to get user from mentions (exclude bot mention)
        for mention in message.mentions:
            if mention.id != self.bot.user.id:
                target_user = mention
                break

        # If no mention found, try to parse as ID
        if not target_user and args:
            try:
                user_id = int(args.strip().split()[0])
                target_user = await self.bot.fetch_user(user_id)
            except (ValueError, discord.NotFound, discord.HTTPException):
                pass

        if not target_user:
            view = create_error_message(
                "Invalid Usage",
                "**Usage:** `<@1373916203814490194> m.setstaff @user` or `<@1373916203814490194> m.setstaff [user_id]`\n\nMention a user or provide their ID to manage their permissions."
            )
            await message.reply(view=view, mention_author=False)
            return

        # Check if target is staff
        user_data = await db.get_user(target_user.id)
        if not user_data['attributes'].get('TEAM') and not self.bot.is_developer(target_user.id):
            view = create_error_message(
                "Not Staff",
                f"{target_user.mention} is not a staff member.\n\nUse `m.rank @user` to add them first."
            )
            await message.reply(view=view, mention_author=False)
            return

        # Check if modifier can modify target
        can_modify = await staff_permissions.can_modify_user(message.author.id, target_user.id)
        if not can_modify:
            view = create_error_message(
                "Permission Denied",
                "You cannot modify this user's permissions.\n\nYou can only modify staff members below your hierarchy level."
            )
            await message.reply(view=view, mention_author=False)
            return

        # Create the permissions management view
        perm_view = StaffPermissionsManagementView(self.bot, target_user, message.author, staff_permissions, None)
        await perm_view.initialize()

        # Create and send the layout view
        layout_view = await perm_view.create_layout_view()
        reply_message = await self.reply_and_track(message, view=layout_view, mention_author=False)

        # Update the view with the message reference
        perm_view.initial_message = reply_message

        # Send the interactive view in a followup (ephemeral)
        await message.channel.send(view=perm_view)

    async def handle_stafflist_command(self, message: discord.Message, args: str):
        """
        Handle m.stafflist command - List all staff members
        Usage: <@1373916203814490194> m.stafflist
        """
        staff_members = await db.get_all_staff_members()

        if not staff_members:
            view = create_info_message("Staff List", "No staff members found.")
            await message.reply(view=view, mention_author=False)
            return

        # Group by roles
        by_role = {}
        for member in staff_members:
            for role_str in member['roles']:
                if role_str not in by_role:
                    by_role[role_str] = []
                by_role[role_str].append(member['user_id'])

        fields = []

        # Add fields for each role
        role_order = [
            StaffRole.MANAGER.value,
            StaffRole.SUPERVISOR_MOD.value,
            StaffRole.SUPERVISOR_COM.value,
            StaffRole.SUPERVISOR_SUP.value,
            StaffRole.MODERATOR.value,
            StaffRole.COMMUNICATION.value,
            StaffRole.SUPPORT.value
        ]

        for role_str in role_order:
            if role_str in by_role:
                members = by_role[role_str]
                member_mentions = [f"<@{uid}>" for uid in members]
                fields.append({
                    'name': f"{role_str} ({len(members)})",
                    'value': ", ".join(member_mentions)
                })

        view = create_info_message(
            "MODDY Staff Team",
            f"Total staff members: **{len(staff_members)}**",
            fields=fields
        )

        await self.reply_and_track(message, view=view, mention_author=False)

    async def handle_staffinfo_command(self, message: discord.Message, args: str):
        """
        Handle m.staffinfo command - Show info about a staff member
        Usage: <@1373916203814490194> m.staffinfo @user
        """
        # Parse user mention or ID or use self
        target_user = None

        # Try to get user from mentions (exclude bot mention)
        for mention in message.mentions:
            if mention.id != self.bot.user.id:
                target_user = mention
                break

        # If no mention found, try to parse as ID
        if not target_user and args:
            try:
                user_id = int(args.strip().split()[0])
                target_user = await self.bot.fetch_user(user_id)
            except (ValueError, discord.NotFound, discord.HTTPException):
                pass

        # If still no target, use command author
        if not target_user:
            target_user = message.author

        # Get permissions
        perms = await db.get_staff_permissions(target_user.id)

        if not perms['roles'] and not self.bot.is_developer(target_user.id):
            view = create_error_message("Not Staff", f"{target_user.mention} is not a staff member.")
            await message.reply(view=view, mention_author=False)
            return

        fields = []

        # Add roles with badges
        role_display = []
        staff_roles = [StaffRole(r) for r in perms['roles']] if perms['roles'] else []

        # Add auto-assigned roles for developers
        if self.bot.is_developer(target_user.id):
            if StaffRole.DEV not in staff_roles:
                role_display.append(f"{EMOJIS['dev_badge']} Dev (Auto)")
            if StaffRole.MANAGER not in staff_roles:
                role_display.append(f"{EMOJIS['manager_badge']} Manager (Auto)")

        # Add regular roles
        for role in staff_roles:
            badge = ""
            if role == StaffRole.DEV:
                badge = EMOJIS['dev_badge']
            elif role == StaffRole.MANAGER:
                badge = EMOJIS['manager_badge']
            elif role == StaffRole.SUPERVISOR_MOD:
                badge = EMOJIS['mod_supervisor_badge']
            elif role == StaffRole.SUPERVISOR_COM:
                badge = EMOJIS['communication_supervisor_badge']
            elif role == StaffRole.SUPERVISOR_SUP:
                badge = EMOJIS['support_supervisor_badge']
            elif role == StaffRole.MODERATOR:
                badge = EMOJIS['moderator_badge']
            elif role == StaffRole.COMMUNICATION:
                badge = EMOJIS['comunication_badge']
            elif role == StaffRole.SUPPORT:
                badge = EMOJIS['supportagent_badge']

            role_display.append(f"{badge} {role.value}")

        fields.append({
            'name': 'Roles',
            'value': "\n".join(role_display) if role_display else "*No roles*"
        })

        # Add denied commands
        if perms['denied_commands']:
            fields.append({
                'name': 'Command Restrictions',
                'value': "\n".join([f"• `{cmd}`" for cmd in perms['denied_commands']])
            })

        # Add timestamps
        if perms['created_at']:
            fields.append({
                'name': f"{EMOJIS['time']} Joined Staff",
                'value': f"<t:{int(perms['created_at'].timestamp())}:R>"
            })

        if perms['updated_at']:
            fields.append({
                'name': f"{EMOJIS['time']} Last Updated",
                'value': f"<t:{int(perms['updated_at'].timestamp())}:R>"
            })

        view = create_info_message(
            f"Staff Member Information - {str(target_user)}",
            f"Information about staff member {target_user.mention}",
            fields=fields
        )

        await self.reply_and_track(message, view=view, mention_author=False)


async def setup(bot):
    await bot.add_cog(StaffManagement(bot))
