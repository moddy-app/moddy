"""
Staff Permissions System for MODDY
Handles role hierarchy, permissions, and command access control
"""

from enum import Enum
from typing import List, Optional, Set
import logging

logger = logging.getLogger('moddy.staff_permissions')


class StaffRole(Enum):
    """Staff role hierarchy"""
    # Developer role (apart from hierarchy)
    DEV = "Dev"

    # Management hierarchy
    MANAGER = "Manager"

    # Supervisor roles
    SUPERVISOR_MOD = "Supervisor_Mod"
    SUPERVISOR_COM = "Supervisor_Com"
    SUPERVISOR_SUP = "Supervisor_Sup"

    # Staff roles
    MODERATOR = "Moderator"
    COMMUNICATION = "Communication"
    SUPPORT = "Support"


class CommandType(Enum):
    """Staff command types (prefixes)"""
    TEAM = "t"      # Common to all staff
    DEV = "d"       # Developer commands
    MANAGEMENT = "m"  # Management commands
    MODERATOR = "mod"  # Moderator commands
    SUPPORT = "sup"    # Support commands
    COMMUNICATION = "com"  # Communication commands


# Role hierarchy levels (higher = more permissions)
ROLE_HIERARCHY = {
    StaffRole.MANAGER: 100,
    StaffRole.SUPERVISOR_MOD: 50,
    StaffRole.SUPERVISOR_COM: 50,
    StaffRole.SUPERVISOR_SUP: 50,
    StaffRole.MODERATOR: 10,
    StaffRole.COMMUNICATION: 10,
    StaffRole.SUPPORT: 10,
    StaffRole.DEV: 1000  # Dev is apart from hierarchy
}


# Command type to required roles mapping
COMMAND_TYPE_ROLES = {
    CommandType.TEAM: [
        StaffRole.MANAGER,
        StaffRole.SUPERVISOR_MOD,
        StaffRole.SUPERVISOR_COM,
        StaffRole.SUPERVISOR_SUP,
        StaffRole.MODERATOR,
        StaffRole.COMMUNICATION,
        StaffRole.SUPPORT,
        StaffRole.DEV
    ],
    CommandType.DEV: [StaffRole.DEV],
    CommandType.MANAGEMENT: [StaffRole.MANAGER],
    CommandType.MODERATOR: [
        StaffRole.MANAGER,
        StaffRole.SUPERVISOR_MOD,
        StaffRole.MODERATOR
    ],
    CommandType.SUPPORT: [
        StaffRole.MANAGER,
        StaffRole.SUPERVISOR_SUP,
        StaffRole.SUPPORT
    ],
    CommandType.COMMUNICATION: [
        StaffRole.MANAGER,
        StaffRole.SUPERVISOR_COM,
        StaffRole.COMMUNICATION
    ]
}


class StaffPermissionManager:
    """Manager for staff permissions and role hierarchy"""

    # Staff command prefix (bot mention)
    STAFF_PREFIX = "<@1373916203814490194>"

    # Super admin user ID (bypasses all permission checks)
    SUPER_ADMIN_ID = 1164597199594852395

    def __init__(self, bot):
        self.bot = bot

    async def get_user_roles(self, user_id: int) -> List[StaffRole]:
        """Get all roles for a user"""
        # Super admin has all roles
        if user_id == self.SUPER_ADMIN_ID:
            return [StaffRole.MANAGER, StaffRole.DEV]

        if not self.bot.db:
            return []

        # Check if user is in Discord dev team - auto Manager + Dev
        if self.bot.is_developer(user_id):
            return [StaffRole.MANAGER, StaffRole.DEV]

        # Get from database
        perms = await self.bot.db.get_staff_permissions(user_id)
        roles = []

        for role_str in perms['roles']:
            try:
                # Convert string to StaffRole enum
                role = StaffRole(role_str)
                roles.append(role)
            except ValueError:
                logger.warning(f"Invalid role in database: {role_str}")

        return roles

    async def has_role(self, user_id: int, role: StaffRole) -> bool:
        """Check if user has a specific role"""
        user_roles = await self.get_user_roles(user_id)
        return role in user_roles

    async def get_denied_commands(self, user_id: int) -> List[str]:
        """Get list of denied commands for a user"""
        if not self.bot.db:
            return []

        perms = await self.bot.db.get_staff_permissions(user_id)
        return perms['denied_commands']

    async def is_command_denied(self, user_id: int, command_name: str) -> bool:
        """Check if a specific command is denied for the user"""
        denied = await self.get_denied_commands(user_id)
        return command_name in denied

    async def can_use_command_type(self, user_id: int, command_type: CommandType) -> bool:
        """Check if user can use a command type based on their roles"""
        user_roles = await self.get_user_roles(user_id)
        required_roles = COMMAND_TYPE_ROLES.get(command_type, [])

        # Check if user has any of the required roles
        for role in user_roles:
            if role in required_roles:
                return True

        return False

    async def can_use_command(self, user_id: int, command_type: CommandType, command_name: str) -> bool:
        """Check if user can use a specific command"""
        # Check if command type is allowed
        if not await self.can_use_command_type(user_id, command_type):
            return False

        # Check if command is specifically denied
        full_command = f"{command_type.value}.{command_name}"
        if await self.is_command_denied(user_id, full_command):
            return False

        return True

    def get_role_level(self, role: StaffRole) -> int:
        """Get hierarchy level of a role"""
        return ROLE_HIERARCHY.get(role, 0)

    async def can_modify_user(self, modifier_id: int, target_id: int) -> bool:
        """Check if modifier can modify target's permissions"""
        # Super admin can modify anyone
        if modifier_id == self.SUPER_ADMIN_ID:
            return True

        # Devs can modify anyone (except they're always dev+manager)
        if self.bot.is_developer(modifier_id):
            return True

        # Can't modify yourself
        if modifier_id == target_id:
            return False

        # Get roles
        modifier_roles = await self.get_user_roles(modifier_id)
        target_roles = await self.get_user_roles(target_id)

        # Can't modify dev team members
        if self.bot.is_developer(target_id):
            return False

        # Get highest level of modifier
        modifier_level = max([self.get_role_level(r) for r in modifier_roles], default=0)

        # Get highest level of target
        target_level = max([self.get_role_level(r) for r in target_roles], default=0)

        # Can only modify if modifier level is strictly higher
        return modifier_level > target_level

    async def can_assign_role(self, modifier_id: int, role: StaffRole) -> bool:
        """Check if modifier can assign a specific role"""
        # Super admin can assign any role
        if modifier_id == self.SUPER_ADMIN_ID:
            return True

        # Devs can assign any role
        if self.bot.is_developer(modifier_id):
            return True

        # Get modifier roles
        modifier_roles = await self.get_user_roles(modifier_id)

        # Can't assign dev role
        if role == StaffRole.DEV:
            return False

        # Managers can assign any non-dev role
        if StaffRole.MANAGER in modifier_roles:
            return True

        # Supervisors can only assign their department's staff role
        if role == StaffRole.MODERATOR and StaffRole.SUPERVISOR_MOD in modifier_roles:
            return True

        if role == StaffRole.COMMUNICATION and StaffRole.SUPERVISOR_COM in modifier_roles:
            return True

        if role == StaffRole.SUPPORT and StaffRole.SUPERVISOR_SUP in modifier_roles:
            return True

        return False

    def parse_staff_command(self, content: str) -> Optional[tuple]:
        """
        Parse staff command syntax: <@1373916203814490194> [type].[command] [args]
        Returns: (command_type: CommandType, command_name: str, args: str) or None
        """
        # Check if message starts with staff prefix
        if not content.startswith(self.STAFF_PREFIX):
            return None

        # Remove prefix and strip
        content = content[len(self.STAFF_PREFIX):].strip()

        # Split into parts
        parts = content.split(maxsplit=1)
        if not parts:
            logger.debug(f"❌ Parse failed: No command after prefix")
            return None

        # Parse type.command
        command_part = parts[0]
        if '.' not in command_part:
            logger.debug(f"❌ Parse failed: No '.' in command part: {command_part}")
            return None

        type_str, command_name = command_part.split('.', 1)

        # Get args if present
        args = parts[1] if len(parts) > 1 else ""

        # Convert type string to CommandType
        try:
            command_type = CommandType(type_str)
        except ValueError:
            logger.debug(f"❌ Parse failed: Invalid command type: {type_str}")
            return None

        logger.debug(f"✅ Parsed: {command_type.value}.{command_name} with args: {args[:50]}")
        return (command_type, command_name, args)

    async def check_command_permission(self, user_id: int, command_type: CommandType, command_name: str) -> tuple:
        """
        Check if user has permission to use a command
        Returns: (allowed: bool, reason: str)
        """
        # Super admin bypasses all checks
        if user_id == self.SUPER_ADMIN_ID:
            logger.debug(f"✅ Super admin {user_id} granted access to {command_type.value}.{command_name}")
            return (True, "")

        # Developers bypass most checks
        is_dev = self.bot.is_developer(user_id)

        # Check if user has TEAM attribute (is staff)
        if not self.bot.db:
            # If db is not available, only allow super admin and devs
            if is_dev:
                logger.debug(f"✅ Developer {user_id} granted access (database unavailable)")
                return (True, "")
            logger.error(f"❌ Permission check failed: Database not available")
            return (False, "Database not available")

        user_data = await self.bot.db.get_user(user_id)
        has_team_attr = user_data['attributes'].get('TEAM')

        logger.debug(f"🔍 Checking permissions for user {user_id}:")
        logger.debug(f"   TEAM attribute: {has_team_attr}")
        logger.debug(f"   Is developer: {is_dev}")

        if not has_team_attr and not is_dev:
            logger.warning(f"❌ User {user_id} is not a staff member (no TEAM attribute and not in dev team)")
            return (False, "You are not a staff member")

        # Get user roles for debugging
        user_roles = await self.get_user_roles(user_id)
        logger.debug(f"   User roles: {[r.value for r in user_roles]}")

        # Check command type permission
        if not await self.can_use_command_type(user_id, command_type):
            logger.warning(f"❌ User {user_id} cannot use {command_type.value} commands (missing required role)")
            return (False, f"You don't have permission to use {command_type.value}. commands")

        # Check if command is specifically denied
        full_command = f"{command_type.value}.{command_name}"
        if await self.is_command_denied(user_id, full_command):
            logger.warning(f"❌ Command {full_command} is specifically denied for user {user_id}")
            return (False, f"You don't have permission to use this specific command")

        logger.debug(f"✅ Permission granted for user {user_id} to use {full_command}")
        return (True, "")


# Global instance (will be initialized in bot.py)
staff_permissions: Optional[StaffPermissionManager] = None


def setup_staff_permissions(bot):
    """Initialize staff permissions system"""
    global staff_permissions
    staff_permissions = StaffPermissionManager(bot)
    logger.info("✅ Staff permissions system initialized")
    return staff_permissions
