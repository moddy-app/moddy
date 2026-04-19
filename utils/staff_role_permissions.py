"""
Staff Role Permissions Configuration
Defines available permissions for each role
"""

from enum import Enum
from typing import List, Dict

# Common permissions available to all staff members
COMMON_PERMISSIONS = [
    "flex",          # Use t.flex command
    "invite",        # Create server invites
    "serverinfo",    # View server information
]

# Permissions specific to Moderator role
MODERATOR_PERMISSIONS = [
    "case_create",            # Create moderation cases
    "case_view",              # View moderation cases
    "case_list",              # List moderation cases
    "case_edit",              # Edit moderation cases
    "case_close",             # Close moderation cases
    "case_note",              # Add notes to cases
    "interserver_info",       # View inter-server message info
    "interserver_delete",     # Delete inter-server messages
]

# Permissions specific to Support role
SUPPORT_PERMISSIONS = [
    "ticket_view",         # View support tickets
    "ticket_close",        # Close support tickets
    "ticket_create",       # Create support tickets
    "subscription_view",   # View user subscription information
    "subscription_manage", # Manage user subscriptions (refunds, modifications)
]

# Permissions specific to Communication role
COMMUNICATION_PERMISSIONS = [
    "announce",      # Send announcements
    "broadcast",     # Broadcast messages
]

# Permissions specific to Supervisor_Mod role
SUPERVISOR_MOD_PERMISSIONS = MODERATOR_PERMISSIONS + [
    "manage_mod",    # Manage moderators
]

# Permissions specific to Supervisor_Sup role
SUPERVISOR_SUP_PERMISSIONS = SUPPORT_PERMISSIONS + [
    "manage_sup",    # Manage support agents
]

# Permissions specific to Supervisor_Com role
SUPERVISOR_COM_PERMISSIONS = COMMUNICATION_PERMISSIONS + [
    "manage_com",    # Manage communication team
]

# Permissions specific to Manager role
MANAGER_PERMISSIONS = [
    "rank",          # Add staff members
    "unrank",        # Remove staff members
    "setstaff",      # Manage staff permissions
    "stafflist",     # View staff list
    "staffinfo",     # View staff information
    "badge_manage",  # Manage user verification badges
]

# Map role names to their available permissions
ROLE_PERMISSIONS_MAP: Dict[str, List[str]] = {
    "Moderator": MODERATOR_PERMISSIONS,
    "Support": SUPPORT_PERMISSIONS,
    "Communication": COMMUNICATION_PERMISSIONS,
    "Supervisor_Mod": SUPERVISOR_MOD_PERMISSIONS,
    "Supervisor_Sup": SUPERVISOR_SUP_PERMISSIONS,
    "Supervisor_Com": SUPERVISOR_COM_PERMISSIONS,
    "Manager": MANAGER_PERMISSIONS,
}

def get_permission_label(permission: str) -> str:
    """Get human-readable label for a permission"""
    labels = {
        # Common
        "flex": "Flex (Team Verification)",
        "invite": "Server Invites",
        "serverinfo": "Server Information",

        # Moderator (Case Management)
        "case_create": "Create Moderation Cases",
        "case_view": "View Moderation Cases",
        "case_list": "List Moderation Cases",
        "case_edit": "Edit Moderation Cases",
        "case_close": "Close Moderation Cases",
        "case_note": "Add Notes to Cases",
        "interserver_info": "Inter-Server Message Info",
        "interserver_delete": "Delete Inter-Server Messages",

        # Support
        "ticket_view": "View Tickets",
        "ticket_close": "Close Tickets",
        "ticket_create": "Create Tickets",
        "subscription_view": "View User Subscriptions",
        "subscription_manage": "Manage Subscriptions (Refunds)",

        # Communication
        "announce": "Send Announcements",
        "broadcast": "Broadcast Messages",

        # Supervisor specific
        "manage_mod": "Manage Moderators",
        "manage_sup": "Manage Support Agents",
        "manage_com": "Manage Communication Team",

        # Manager
        "rank": "Add Staff Members",
        "unrank": "Remove Staff Members",
        "setstaff": "Manage Staff Permissions",
        "stafflist": "View Staff List",
        "staffinfo": "View Staff Information",
        "badge_manage": "Manage User Verification Badges",
    }
    return labels.get(permission, permission.replace("_", " ").title())

def get_role_display_name(role: str) -> str:
    """Get human-readable display name for a role"""
    names = {
        "Moderator": "Moderator",
        "Support": "Support Agent",
        "Communication": "Communication",
        "Supervisor_Mod": "Moderation Supervisor",
        "Supervisor_Sup": "Support Supervisor",
        "Supervisor_Com": "Communication Supervisor",
        "Manager": "Manager",
        "Dev": "Developer",
    }
    return names.get(role, role)
