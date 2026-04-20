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
from cogs.error_handler import BaseView
import logging
import json
import re
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
)
from utils.emojis import EMOJIS
from utils.staff_logger import staff_logger
from utils.staff_role_permissions import (
    COMMON_PERMISSIONS,
    ROLE_PERMISSIONS_MAP,
    get_permission_label,
    get_role_display_name
)
from staff.base import StaffCommandsCog

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
                    emoji=discord.PartialEmoji(name="manager_badge", id=1437514336355483749)
                ),
                discord.SelectOption(
                    label="Moderator Supervisor",
                    value=StaffRole.SUPERVISOR_MOD.value,
                    description="Supervises moderators",
                    emoji=discord.PartialEmoji(name="mod_supervisor_badge", id=1437514356135821322)
                ),
                discord.SelectOption(
                    label="Communication Supervisor",
                    value=StaffRole.SUPERVISOR_COM.value,
                    description="Supervises communication team",
                    emoji=discord.PartialEmoji(name="communication_supervisor_badge", id=1437514333763535068)
                ),
                discord.SelectOption(
                    label="Support Supervisor",
                    value=StaffRole.SUPERVISOR_SUP.value,
                    description="Supervises support team",
                    emoji=discord.PartialEmoji(name="support_supervisor_badge", id=1437514347923636435)
                ),
                discord.SelectOption(
                    label="Moderator",
                    value=StaffRole.MODERATOR.value,
                    description="Moderation staff member",
                    emoji=discord.PartialEmoji(name="moderator_badge", id=1437514357230796891)
                ),
                discord.SelectOption(
                    label="Communication",
                    value=StaffRole.COMMUNICATION.value,
                    description="Communication staff member",
                    emoji=discord.PartialEmoji(name="comunication_badge", id=1437514353304670268)
                ),
                discord.SelectOption(
                    label="Support",
                    value=StaffRole.SUPPORT.value,
                    description="Support staff member",
                    emoji=discord.PartialEmoji(name="supportagent_badge", id=1437514361861177350)
                )
            ]
        )
        select.callback = self.role_select
        self.add_item(select)

        # Add confirm button
        confirm_btn = ui.Button(
            label="Confirm",
            style=discord.ButtonStyle.green,
            emoji=discord.PartialEmoji(name="done", id=1398729525277229066),
            custom_id="confirm_roles"
        )
        confirm_btn.callback = self.confirm_button
        self.add_item(confirm_btn)

        # Add cancel button
        cancel_btn = ui.Button(
            label="Cancel",
            style=discord.ButtonStyle.red,
            emoji=discord.PartialEmoji(name="undone", id=1398729502028333218),
            custom_id="cancel_roles"
        )
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


class StaffPermissionsManagementView(BaseView):
    """View for managing staff permissions with role-based permission system using Components V2"""

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
        self.selected_role_for_config: Optional[StaffRole] = None  # Currently configuring this role

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

        # Set default to common permissions
        if self.selected_roles:
            self.selected_role_for_config = "common"

        # Build the view
        await self.rebuild_view()

    async def rebuild_view(self):
        """Rebuild the view with current state"""
        # Clear all items
        self.clear_items()

        # Create main container
        container = ui.Container()

        # Header
        container.add_item(ui.TextDisplay(
            content=f"### {EMOJIS['settings']} Staff Permissions Management\n"
                   f"Configure roles and permissions for {self.target_user.mention}"
        ))

        # Role Selection Section
        container.add_item(ui.TextDisplay(content="**Select Roles**\n-# Choose which roles to assign to this staff member"))

        role_select_row = ui.ActionRow()
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
                    emoji=discord.PartialEmoji(name="manager_badge", id=1437514336355483749),
                    default=StaffRole.MANAGER in self.selected_roles
                ),
                discord.SelectOption(
                    label="Moderator Supervisor",
                    value=StaffRole.SUPERVISOR_MOD.value,
                    description="Supervises moderators",
                    emoji=discord.PartialEmoji(name="mod_supervisor_badge", id=1437514356135821322),
                    default=StaffRole.SUPERVISOR_MOD in self.selected_roles
                ),
                discord.SelectOption(
                    label="Communication Supervisor",
                    value=StaffRole.SUPERVISOR_COM.value,
                    description="Supervises communication team",
                    emoji=discord.PartialEmoji(name="communication_supervisor_badge", id=1437514333763535068),
                    default=StaffRole.SUPERVISOR_COM in self.selected_roles
                ),
                discord.SelectOption(
                    label="Support Supervisor",
                    value=StaffRole.SUPERVISOR_SUP.value,
                    description="Supervises support team",
                    emoji=discord.PartialEmoji(name="support_supervisor_badge", id=1437514347923636435),
                    default=StaffRole.SUPERVISOR_SUP in self.selected_roles
                ),
                discord.SelectOption(
                    label="Moderator",
                    value=StaffRole.MODERATOR.value,
                    description="Moderation staff member",
                    emoji=discord.PartialEmoji(name="moderator_badge", id=1437514357230796891),
                    default=StaffRole.MODERATOR in self.selected_roles
                ),
                discord.SelectOption(
                    label="Communication",
                    value=StaffRole.COMMUNICATION.value,
                    description="Communication staff member",
                    emoji=discord.PartialEmoji(name="comunication_badge", id=1437514353304670268),
                    default=StaffRole.COMMUNICATION in self.selected_roles
                ),
                discord.SelectOption(
                    label="Support",
                    value=StaffRole.SUPPORT.value,
                    description="Support staff member",
                    emoji=discord.PartialEmoji(name="supportagent_badge", id=1437514361861177350),
                    default=StaffRole.SUPPORT in self.selected_roles
                )
            ]
        )
        role_select.callback = self.role_select_callback
        role_select_row.add_item(role_select)
        container.add_item(role_select_row)

        # Add permission configuration if there are roles
        if self.selected_roles:
            # Build options: Common + all roles with permissions
            config_options = []

            # Add Common Permissions option
            config_options.append(
                discord.SelectOption(
                    label="Common Permissions",
                    value="common",
                    description="Permissions available to all roles",
                    emoji=discord.PartialEmoji(name="settings", id=1398729549323440208),
                    default=self.selected_role_for_config == "common"
                )
            )

            # Add role-specific options
            roles_with_perms = [role for role in self.selected_roles if ROLE_PERMISSIONS_MAP.get(role.value)]
            for role in roles_with_perms:
                config_options.append(
                    discord.SelectOption(
                        label=get_role_display_name(role.value),
                        value=role.value,
                        emoji=discord.PartialEmoji(
                            name=self._get_role_badge_name(role),
                            id=self._get_role_badge_id(role)
                        ),
                        default=role.value == self.selected_role_for_config
                    )
                )

            if config_options:
                container.add_item(ui.TextDisplay(
                    content=f"**Configure Permissions**\n-# Select which permissions to configure"
                ))

                # Menu to select what to configure (common or specific role)
                role_config_select_row = ui.ActionRow()
                role_config_select = ui.Select(
                    placeholder="Select what to configure",
                    min_values=1,
                    max_values=1,
                    custom_id="role_to_configure",
                    options=config_options
                )
                role_config_select.callback = self.role_config_select_callback
                role_config_select_row.add_item(role_config_select)
                container.add_item(role_config_select_row)

                # Show permissions menu based on selection
                if self.selected_role_for_config == "common":
                    # Common permissions
                    common_select_row = ui.ActionRow()
                    common_select = ui.Select(
                        placeholder="Select common permissions",
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
                    common_select_row.add_item(common_select)
                    container.add_item(common_select_row)

                elif self.selected_role_for_config and self.selected_role_for_config in [r.value for r in roles_with_perms]:
                    # Role-specific permissions
                    available_perms = ROLE_PERMISSIONS_MAP.get(self.selected_role_for_config, [])
                    if available_perms:
                        role_perm_select_row = ui.ActionRow()
                        role_perm_select = ui.Select(
                            placeholder=f"Select permissions",
                            min_values=0,
                            max_values=len(available_perms),
                            custom_id=f"perms_{self.selected_role_for_config}",
                            options=[
                                discord.SelectOption(
                                    label=get_permission_label(perm),
                                    value=perm,
                                    default=perm in self.role_permissions.get(self.selected_role_for_config, [])
                                ) for perm in available_perms
                            ]
                        )
                        role_perm_select.callback = self.create_role_permission_callback(self.selected_role_for_config)
                        role_perm_select_row.add_item(role_perm_select)
                        container.add_item(role_perm_select_row)

        # Add buttons row
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        buttons_row = ui.ActionRow()

        # Create buttons explicitly
        save_btn = ui.Button(
            label="Save Changes",
            style=discord.ButtonStyle.green,
            emoji=discord.PartialEmoji(name="done", id=1398729525277229066),
            custom_id=f"save_perms_{self.target_user.id}"
        )
        save_btn.callback = self.save_callback

        cancel_btn = ui.Button(
            label="Cancel",
            style=discord.ButtonStyle.red,
            emoji=discord.PartialEmoji(name="undone", id=1398729502028333218),
            custom_id=f"cancel_perms_{self.target_user.id}"
        )
        cancel_btn.callback = self.cancel_callback

        buttons_row.add_item(save_btn)
        buttons_row.add_item(cancel_btn)
        container.add_item(buttons_row)

        # Add container to view
        self.add_item(container)

    def _get_role_badge(self, role: StaffRole) -> str:
        """Get emoji badge for a role"""
        badges = {
            StaffRole.DEV: EMOJIS['dev_badge'],
            StaffRole.MANAGER: EMOJIS['manager_badge'],
            StaffRole.SUPERVISOR_MOD: EMOJIS['mod_supervisor_badge'],
            StaffRole.SUPERVISOR_COM: EMOJIS['communication_supervisor_badge'],
            StaffRole.SUPERVISOR_SUP: EMOJIS['support_supervisor_badge'],
            StaffRole.MODERATOR: EMOJIS['moderator_badge'],
            StaffRole.COMMUNICATION: EMOJIS['comunication_badge'],
            StaffRole.SUPPORT: EMOJIS['supportagent_badge'],
        }
        return badges.get(role, "")

    def _get_role_badge_name(self, role: StaffRole) -> str:
        """Get emoji badge name for a role"""
        badge_names = {
            StaffRole.DEV: "dev_badge",
            StaffRole.MANAGER: "manager_badge",
            StaffRole.SUPERVISOR_MOD: "mod_supervisor_badge",
            StaffRole.SUPERVISOR_COM: "communication_supervisor_badge",
            StaffRole.SUPERVISOR_SUP: "support_supervisor_badge",
            StaffRole.MODERATOR: "moderator_badge",
            StaffRole.COMMUNICATION: "comunication_badge",
            StaffRole.SUPPORT: "supportagent_badge",
        }
        return badge_names.get(role, "")

    def _get_role_badge_id(self, role: StaffRole) -> int:
        """Get emoji badge ID for a role"""
        badge_ids = {
            StaffRole.DEV: 1437514335009247274,
            StaffRole.MANAGER: 1437514336355483749,
            StaffRole.SUPERVISOR_MOD: 1437514356135821322,
            StaffRole.SUPERVISOR_COM: 1437514333763535068,
            StaffRole.SUPERVISOR_SUP: 1437514347923636435,
            StaffRole.MODERATOR: 1437514357230796891,
            StaffRole.COMMUNICATION: 1437514353304670268,
            StaffRole.SUPPORT: 1437514361861177350,
        }
        return badge_ids.get(role, 0)

    async def role_config_select_callback(self, interaction: discord.Interaction):
        """Handle role configuration selection"""
        if interaction.user.id != self.modifier.id:
            await interaction.response.send_message(
                "❌ Only the command initiator can use this menu.",
                ephemeral=True
            )
            return

        # Find the role config select in the container
        container = self.children[0]
        select = None
        for item in container.children:
            if isinstance(item, ui.ActionRow):
                for child in item.children:
                    if isinstance(child, ui.Select) and child.custom_id == "role_to_configure":
                        select = child
                        break
            if select:
                break

        if select and select.values:
            selected_value = select.values[0]

            # If "common", set as string, otherwise convert to StaffRole
            if selected_value == "common":
                self.selected_role_for_config = "common"
            else:
                self.selected_role_for_config = selected_value

            # Rebuild to show permissions for selected role/common
            await self.rebuild_view()
            await interaction.response.edit_message(view=self)
        else:
            await interaction.response.defer()

    async def role_select_callback(self, interaction: discord.Interaction):
        """Handle role selection changes"""
        if interaction.user.id != self.modifier.id:
            await interaction.response.send_message(
                "❌ Only the command initiator can use this menu.",
                ephemeral=True
            )
            return

        # Find the role select in the container
        container = self.children[0]  # First item is our container
        select = None
        for item in container.children:
            if isinstance(item, ui.ActionRow):
                for child in item.children:
                    if isinstance(child, ui.Select) and child.custom_id == "role_select":
                        select = child
                        break
            if select:
                break

        if not select:
            await interaction.response.send_message("❌ Error finding role selector.", ephemeral=True)
            return

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

        # Set default to common if roles exist
        if new_roles:
            # Check if current selection is still valid
            valid_values = ["common"] + [r.value for r in new_roles if ROLE_PERMISSIONS_MAP.get(r.value)]
            if self.selected_role_for_config not in valid_values:
                self.selected_role_for_config = "common"
        else:
            self.selected_role_for_config = None

        # Rebuild the view
        await self.rebuild_view()

        # Update the message with rebuilt view
        await interaction.response.edit_message(view=self)

    async def common_permissions_callback(self, interaction: discord.Interaction):
        """Handle common permissions selection"""
        if interaction.user.id != self.modifier.id:
            await interaction.response.send_message(
                "❌ Only the command initiator can use this menu.",
                ephemeral=True
            )
            return

        # Find the common permissions select in the container
        container = self.children[0]
        select = None
        for item in container.children:
            if isinstance(item, ui.ActionRow):
                for child in item.children:
                    if isinstance(child, ui.Select) and child.custom_id == "common_permissions":
                        select = child
                        break
            if select:
                break

        if select:
            self.common_permissions = select.values
            # Rebuild to show updated selections
            await self.rebuild_view()
            await interaction.response.edit_message(view=self)
        else:
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

            # Find the role permission select in the container
            container = self.children[0]
            select = None
            for item in container.children:
                if isinstance(item, ui.ActionRow):
                    for child in item.children:
                        if isinstance(child, ui.Select) and child.custom_id == f"perms_{role_name}":
                            select = child
                            break
                if select:
                    break

            if select:
                self.role_permissions[role_name] = select.values
                # Rebuild to show updated selections
                await self.rebuild_view()
                await interaction.response.edit_message(view=self)
            else:
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

            # Create final view showing saved state
            await self.rebuild_view()

            # Create success message
            success_view = create_success_message(
                "Staff Permissions Updated",
                f"Permissions for {self.target_user.mention} have been successfully updated."
            )

            await interaction.response.send_message(view=success_view, ephemeral=True)

            # Update the original message to disable buttons
            if self.initial_message:
                try:
                    # Clear the view to remove interactive components
                    await self.initial_message.edit(view=None)
                except:
                    pass  # Message might have been deleted

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


class StaffManagement(StaffCommandsCog):
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
        elif command_name == "badge":
            await self.handle_badge_command(message, args)
        else:
            view = create_error_message("Unknown Command", f"Management command `{command_name}` not found.")
            await message.reply(view=view, mention_author=False)

    async def handle_rank_command(self, message: discord.Message, args: str):
        """
        Handle m.rank command - Add user to staff team
        Usage: <@1373916203814490194> m.rank @user
        """
        # Log the command
        if staff_logger:
            await staff_logger.log_command("m", "rank", message.author, args=args)

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
        await self.reply_with_tracking(message, layout)
        # Send interactive view as followup
        await message.channel.send(view=button_view)

    async def handle_unrank_command(self, message: discord.Message, args: str):
        """
        Handle m.unrank command - Remove user from staff team
        Usage: <@1373916203814490194> m.unrank @user
        """
        # Log the command
        if staff_logger:
            await staff_logger.log_command("m", "unrank", message.author, args=args)

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

            await self.reply_with_tracking(message, view)

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
        # Log the command
        if staff_logger:
            await staff_logger.log_command("m", "setstaff", message.author, args=args)

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

        # Create the permissions management view (Components V2)
        perm_view = StaffPermissionsManagementView(self.bot, target_user, message.author, staff_permissions, None)
        await perm_view.initialize()

        # Send the interactive Components V2 view
        reply_message = await self.reply_with_tracking(message, perm_view)

        # Update the view with the message reference
        perm_view.initial_message = reply_message

    async def handle_stafflist_command(self, message: discord.Message, args: str):
        """
        Handle m.stafflist command - List all staff members
        Usage: <@1373916203814490194> m.stafflist
        """
        # Log the command
        if staff_logger:
            await staff_logger.log_command("m", "stafflist", message.author)

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

        await self.reply_with_tracking(message, view)

    async def handle_staffinfo_command(self, message: discord.Message, args: str):
        """
        Handle m.staffinfo command - Show info about a staff member
        Usage: <@1373916203814490194> m.staffinfo @user
        """
        # Log the command
        if staff_logger:
            await staff_logger.log_command("m", "staffinfo", message.author, args=args if args else "self")

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

        await self.reply_with_tracking(message, view)

    async def handle_badge_command(self, message: discord.Message, args: str):
        """
        Handle m.badge command — Manage user verification badges.

        Usage:
          m.badge @user v/org/member [org_name]   — assign badge
          m.badge @user rm <v|org|member>          — remove badge

        Short aliases: v=verified, org=verified_org, member=verified_org_member
        """
        BADGE_ALIASES = {
            "v": "VERIFIED", "verified": "VERIFIED",
            "org": "VERIFIED_ORG", "vo": "VERIFIED_ORG", "verified_org": "VERIFIED_ORG",
            "member": "VERIFIED_ORG_MEMBER", "m": "VERIFIED_ORG_MEMBER",
            "vom": "VERIFIED_ORG_MEMBER", "org_member": "VERIFIED_ORG_MEMBER",
            "verified_org_member": "VERIFIED_ORG_MEMBER",
        }
        REMOVE_ALIASES = {"rm", "remove", "del", "r", "delete"}

        USAGE = (
            "**Usage:**\n"
            "• `m.badge @user v` — Standard verified\n"
            "• `m.badge @user org` — Verified organisation\n"
            "• `m.badge @user member [org_name]` — Org-member badge\n"
            "• `m.badge @user rm <v|org|member>` — Remove a badge"
        )

        if staff_logger:
            await staff_logger.log_command("m", "badge", message.author, args=args)

        tokens = args.strip().split() if args else []

        # --- Parse target user ---
        # First try non-bot mentions
        target_user = None
        for mention in message.mentions:
            if mention.id != self.bot.user.id:
                target_user = mention
                break

        # Fallback: extract user from token (supports <@ID> and plain ID, including the bot itself)
        if not target_user and tokens:
            raw = tokens[0]
            match = re.match(r'<@!?(\d+)>', raw)
            raw_id = int(match.group(1)) if match else None
            if raw_id is None:
                try:
                    raw_id = int(raw)
                except ValueError:
                    pass
            if raw_id:
                tokens = tokens[1:]
                try:
                    target_user = await self.bot.fetch_user(raw_id)
                except Exception:
                    # fetch_user can fail (deleted account, network, etc.)
                    # Use a minimal placeholder — only .id and .mention are needed
                    from types import SimpleNamespace
                    target_user = SimpleNamespace(id=raw_id, mention=f"<@{raw_id}>")

        # Strip leading mention token if still present
        if tokens and re.match(r'<@!?(\d+)>', tokens[0]):
            tokens = tokens[1:]

        if not target_user or not tokens:
            view = create_error_message("Invalid Usage", USAGE)
            await message.reply(view=view, mention_author=False)
            return

        action = tokens[0].lower()
        extra = tokens[1:]

        # --- REMOVE ---
        if action in REMOVE_ALIASES:
            if not extra or extra[0].lower() not in BADGE_ALIASES:
                view = create_error_message(
                    "Invalid Usage",
                    f"Specify badge to remove: `v`, `org`, or `member`.\n\n{USAGE}"
                )
                await message.reply(view=view, mention_author=False)
                return

            attr_key = BADGE_ALIASES[extra[0].lower()]
            try:
                await db.set_attribute("user", target_user.id, attr_key, False, message.author.id, f"Badge {attr_key} removed via m.badge")
                await db.set_attribute("user", target_user.id, f"{attr_key}_DATE", None, message.author.id, "Badge date cleared via m.badge")
                if attr_key == "VERIFIED_ORG_MEMBER":
                    await db.set_attribute("user", target_user.id, "VERIFIED_ORG_MEMBER_ORG", None, message.author.id, "Org cleared via m.badge")

                view = create_success_message("Badge Removed", f"Removed `{attr_key}` badge from {target_user.mention}.")
                await self.reply_with_tracking(message, view)
            except Exception as e:
                logger.error(f"Error removing badge: {e}")
                view = create_error_message("Error", f"Failed to remove badge: {e}")
                await message.reply(view=view, mention_author=False)
            return

        # --- SET ---
        if action not in BADGE_ALIASES:
            view = create_error_message("Invalid Badge Type", f"Valid types: `v`, `org`, `member`.\n\n{USAGE}")
            await message.reply(view=view, mention_author=False)
            return

        attr_key = BADGE_ALIASES[action]
        timestamp = str(int(datetime.now(timezone.utc).timestamp()))

        try:
            await db.set_attribute("user", target_user.id, attr_key, True, message.author.id, f"Badge {attr_key} set via m.badge")
            await db.set_attribute("user", target_user.id, f"{attr_key}_DATE", timestamp, message.author.id, "Badge date set via m.badge")

            details_lines = [f"Assigned `{attr_key}` badge to {target_user.mention}."]

            if attr_key == "VERIFIED_ORG_MEMBER":
                if extra:
                    # Split by comma to support multiple orgs: m.badge @user member Orga1, Orga2
                    raw_orgs = " ".join(extra)
                    orgs = [o.strip() for o in raw_orgs.split(",") if o.strip()]
                    org_value = json.dumps(orgs)
                    await db.set_attribute("user", target_user.id, "VERIFIED_ORG_MEMBER_ORG", org_value, message.author.id, "Orgs set via m.badge")
                    orgs_display = ", ".join(f"**{o}**" for o in orgs)
                    details_lines.append(f"Organisation(s): {orgs_display}")
                else:
                    await db.set_attribute("user", target_user.id, "VERIFIED_ORG_MEMBER_ORG", None, message.author.id, "Org cleared via m.badge")

            view = create_success_message("Badge Assigned", "\n".join(details_lines))
            await self.reply_with_tracking(message, view)

        except Exception as e:
            logger.error(f"Error assigning badge: {e}")
            view = create_error_message("Error", f"Failed to assign badge: {e}")
            await message.reply(view=view, mention_author=False)


async def setup(bot):
    await bot.add_cog(StaffManagement(bot))
