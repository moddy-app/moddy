"""
Staff Help View - Interactive help system with category selection
"""

import discord
from discord.ui import Container, TextDisplay, ActionRow, Select
from typing import List, Dict, Optional
import logging

from cogs.error_handler import BaseView
from utils.staff_permissions import staff_permissions, StaffRole, CommandType
from utils.emojis import EMOJIS

logger = logging.getLogger('moddy.staff_help')


# Define command categories with their commands
COMMAND_CATEGORIES = {
    "team": {
        "name": "Team Commands",
        "emoji": EMOJIS['commands'],
        "description": "Commands available to all staff members",
        "commands": [
            ("t.help", "Show this interactive help menu"),
            ("t.invite [server_id]", "Get an invite link to a server"),
            ("t.serverinfo [server_id]", "Get detailed information about a server"),
            ("t.mutualserver [user_id]", "View mutual servers with a user and their permissions"),
            ("t.user [user_id]", "Get detailed information about a user"),
            ("t.server [server_id]", "Get detailed information about a server (alias)"),
            ("t.flex", "Prove you are a member of the Moddy team")
        ],
        "roles": [StaffRole.MANAGER, StaffRole.SUPERVISOR_MOD, StaffRole.SUPERVISOR_COM, StaffRole.SUPERVISOR_SUP,
                  StaffRole.MODERATOR, StaffRole.COMMUNICATION, StaffRole.SUPPORT, StaffRole.DEV]
    },
    "moderator": {
        "name": "Moderator Commands",
        "emoji": "🛡️",
        "description": "Moderation and case management commands",
        "commands": [
            ("mod.case create @user", "Create a new moderation case"),
            ("mod.case view [case_id]", "View detailed information about a case"),
            ("mod.case list @user", "List all cases for a user/guild"),
            ("mod.case edit [case_id]", "Edit an existing case"),
            ("mod.case close [case_id]", "Close/revoke a case"),
            ("mod.case note [case_id]", "Add internal staff note to a case"),
            ("mod.interserver_info [moddy_id]", "Get inter-server message information"),
            ("mod.interserver_delete [moddy_id]", "Delete an inter-server message")
        ],
        "roles": [StaffRole.MANAGER, StaffRole.SUPERVISOR_MOD, StaffRole.MODERATOR]
    },
    "support": {
        "name": "Support Commands",
        "emoji": "🎧",
        "description": "Support and subscription management commands",
        "commands": [
            ("sup.help", "Show available support commands"),
            ("sup.subscription @user", "View user's subscription details (requires subscription_view)"),
            ("sup.invoices @user [limit]", "View user's payment invoices (requires subscription_view)"),
            ("sup.refund @user [amount] [reason]", "Process payment refund (requires subscription_manage)")
        ],
        "roles": [StaffRole.MANAGER, StaffRole.SUPERVISOR_SUP, StaffRole.SUPPORT]
    },
    "communication": {
        "name": "Communication Commands",
        "emoji": "💬",
        "description": "Communication and announcement commands",
        "commands": [
            ("Communication commands", "In development")
        ],
        "roles": [StaffRole.MANAGER, StaffRole.SUPERVISOR_COM, StaffRole.COMMUNICATION]
    },
    "management": {
        "name": "Management Commands",
        "emoji": "👑",
        "description": "Staff management and administration",
        "commands": [
            ("m.rank @user [role]", "Add a user to the staff team with a role"),
            ("m.unrank @user", "Remove a user from the staff team"),
            ("m.setstaff @user", "Manage staff member permissions"),
            ("m.stafflist", "List all staff members"),
            ("m.staffinfo [@user]", "Show detailed staff member information"),
            ("m.badge @user <verified|verified_org|verified_org_member> [org]", "Assign a verification badge to a user"),
            ("m.badge @user remove <verified|verified_org|verified_org_member>", "Remove a verification badge from a user")
        ],
        "roles": [StaffRole.MANAGER]
    },
    "developer": {
        "name": "Developer Commands",
        "emoji": EMOJIS['dev'],
        "description": "Developer and system commands",
        "commands": [
            ("d.reload [extension]", "Reload bot extensions"),
            ("d.shutdown", "Shutdown the bot"),
            ("d.stats", "Show detailed bot statistics"),
            ("d.sql [query]", "Execute SQL query on the database"),
            ("d.jsk [code]", "Execute Python code (Jishaku)"),
            ("d.error [error_code]", "Get detailed error information"),
            ("d.sync", "Sync slash commands globally")
        ],
        "roles": [StaffRole.DEV]
    }
}


class StaffHelpView(BaseView):
    """Interactive help view with category selection"""

    def __init__(self, bot, user_id: int, user_roles: List[StaffRole]):
        super().__init__(timeout=180)
        self.bot = bot
        self.user_id = user_id
        self.user_roles = user_roles
        self.selected_category: Optional[str] = "team"  # Default to team commands

        self._build_view()

    def _build_view(self):
        """Build the view with category selector and command list"""
        self.clear_items()

        container = Container()

        # Title
        container.add_item(TextDisplay(
            f"### {EMOJIS['book']} MODDY Staff Help\n"
            f"-# Select a command category below"
        ))

        # Category selector
        select_row = ActionRow()

        # Build options - only show categories the user has access to
        options = []
        for category_id, category_data in COMMAND_CATEGORIES.items():
            # Check if user has any of the required roles for this category
            has_access = any(role in category_data["roles"] for role in self.user_roles)

            if has_access:
                options.append(discord.SelectOption(
                    label=category_data["name"],
                    value=category_id,
                    description=category_data["description"][:100],
                    emoji=category_data["emoji"],
                    default=(category_id == self.selected_category)
                ))

        if not options:
            # User has no staff roles - shouldn't happen but handle it
            container.add_item(TextDisplay(
                f"{EMOJIS['error']} You don't have access to any staff commands."
            ))
            self.add_item(container)
            return

        category_select = Select(
            placeholder="Select a command category...",
            options=options,
            max_values=1
        )
        category_select.callback = self.on_category_select
        select_row.add_item(category_select)
        container.add_item(select_row)

        # Show commands for selected category
        if self.selected_category:
            category_data = COMMAND_CATEGORIES[self.selected_category]

            # Category title and description
            container.add_item(TextDisplay(
                f"\n**{category_data['emoji']} {category_data['name']}**\n"
                f"{category_data['description']}"
            ))

            # Commands list
            commands_text = []
            for cmd, desc in category_data["commands"]:
                # Add bot mention to command if it doesn't already have one
                if not cmd.startswith("Support") and not cmd.startswith("Communication"):
                    commands_text.append(f"`<@1373916203814490194> {cmd}`\n-# {desc}")
                else:
                    commands_text.append(f"*{cmd}*: {desc}")

            container.add_item(TextDisplay("\n\n".join(commands_text)))

        self.add_item(container)

    async def on_category_select(self, interaction: discord.Interaction):
        """Handle category selection"""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                f"{EMOJIS['error']} This help menu is not for you.",
                ephemeral=True
            )
            return

        value = interaction.data['values'][0]
        self.selected_category = value

        self._build_view()
        await interaction.response.edit_message(view=self)


async def create_help_view(bot, user_id: int) -> StaffHelpView:
    """
    Create a help view for a staff member

    Args:
        bot: Bot instance
        user_id: User ID to create help for

    Returns:
        StaffHelpView instance
    """
    # Get user roles
    user_roles = await staff_permissions.get_user_roles(user_id)

    if not user_roles:
        # Not a staff member - return None
        return None

    return StaffHelpView(bot, user_id, user_roles)
