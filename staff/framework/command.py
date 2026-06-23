"""
StaffCommand — the base class every staff command inherits from.

One file per command (see ``staff/commands/<type>/<name>.py``). A command
declares lightweight metadata (type, name, slash options, required permission)
and implements a single :meth:`execute` coroutine that runs identically for the
message and slash transports via :class:`StaffContext`.

Registration is decorator-based: ``@staff_command`` appends the class to the
module-level registry, which the dispatcher cog discovers at load time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional

from utils.staff_permissions import CommandType


# --- Slash option declaration ---------------------------------------------

# Logical option type -> used by the registry to build the slash signature.
OPTION_TYPES = ("string", "integer", "boolean", "number", "user", "member", "channel", "role")


@dataclass
class SlashOption:
    """Declarative slash option. Drives both the slash signature and the
    default message-argument parsing."""
    name: str
    type: str = "string"
    description: str = "…"
    required: bool = False
    default: Any = None
    choices: Optional[List[str]] = None  # turns the option into a fixed choice list


# --- Registry --------------------------------------------------------------

_REGISTRY: List[type] = []


def staff_command(cls: type) -> type:
    """Class decorator: register a :class:`StaffCommand` subclass."""
    _REGISTRY.append(cls)
    return cls


def get_registered_commands() -> List[type]:
    return list(_REGISTRY)


# --- Base class ------------------------------------------------------------

class StaffCommand:
    """Base class for all staff commands.

    Subclasses set the class attributes and implement :meth:`execute`.
    """

    #: Command type (drives the message prefix and the slash group).
    command_type: CommandType = CommandType.DEV
    #: Command name (slash subcommand + message ``<type>.<name>``).
    name: str = ""
    #: Short English description shown in the Discord command list.
    description: str = "…"
    #: Optional extra message aliases.
    aliases: tuple = ()
    #: Declarative slash options.
    options: List[SlashOption] = []
    #: Fine-grained permission node (checked against role_permissions). When
    #: ``None`` the command-type role requirement is used as-is. Setting this
    #: lets a command live in a department without being gated only by role.
    permission: Optional[str] = None
    #: Redact arguments in the staff audit log (for ``sql`` / ``jsk``).
    sensitive: bool = False

    def __init__(self, bot):
        self.bot = bot

    # --- message argument parsing -----------------------------------------

    def parse_message(self, raw: str) -> dict:
        """Parse raw message arguments into an options dict.

        Default behaviour: a single declared option receives the whole raw
        string. Commands with several positional options override this.
        """
        raw = (raw or "").strip()
        opts: dict = {}
        if self.options:
            first = self.options[0]
            opts[first.name] = raw if raw else first.default
        return opts

    # --- execution ---------------------------------------------------------

    async def execute(self, ctx) -> None:  # noqa: D401 - implemented by subclasses
        raise NotImplementedError

    # --- audit-log argument preview ---------------------------------------

    def log_args(self, ctx) -> str:
        """Return a short, log-safe preview of the command arguments."""
        if self.is_message_like(ctx):
            raw = ctx.raw_args or ""
        else:
            raw = " ".join(f"{k}={v}" for k, v in ctx.options.items() if v is not None)
        raw = raw.strip()
        if self.sensitive and raw:
            return raw[:30] + ("…" if len(raw) > 30 else "")
        return raw[:100] + ("…" if len(raw) > 100 else "")

    @staticmethod
    def is_message_like(ctx) -> bool:
        return not ctx.is_slash
