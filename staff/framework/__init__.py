"""
Staff command framework.

Scalable, transport-agnostic engine behind the staff command system. Each
command lives in its own file under ``staff/commands/<type>/`` and is exposed
both as a message command (``@Moddy d.jsk``) everywhere Moddy is, and as a slash
command (``/dev jsk``) on OFFICIAL Moddy servers only.

Public API used by command modules:

    from staff.framework import (
        StaffCommand, SlashOption, staff_command, StaffContext, design,
        parse_user_id, parse_guild_id, CommandType,
    )
"""

from utils.staff_permissions import CommandType  # re-export for command modules

from staff.framework.command import StaffCommand, SlashOption, staff_command, get_registered_commands
from staff.framework.context import StaffContext
from staff.framework.parsing import parse_user_id, parse_guild_id
from staff.framework import design

__all__ = [
    "StaffCommand",
    "SlashOption",
    "staff_command",
    "get_registered_commands",
    "StaffContext",
    "design",
    "parse_user_id",
    "parse_guild_id",
    "CommandType",
]
