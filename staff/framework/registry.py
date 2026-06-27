"""
Registry — discovers staff command modules and wires them to both transports.

- :func:`discover_commands` imports every module under ``staff/commands`` so the
  ``@staff_command`` decorators populate the registry.
- :func:`build` instantiates each command, builds the message routing index and
  one ``app_commands.Group`` per command type (``/dev``, ``/team`` …), and
  dynamically generates a slash callback for every command (with the shared
  ``incognito`` option appended automatically).
"""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from typing import Any, Callable, Dict, List, Optional, Tuple

import discord
from discord import app_commands

from utils.staff_permissions import CommandType
from staff.framework.command import StaffCommand, SlashOption, get_registered_commands

logger = logging.getLogger("moddy.staff.registry")


# Command type -> slash group name + English group description.
SLASH_GROUPS: Dict[CommandType, Tuple[str, str]] = {
    CommandType.DEV: ("dev", "Developer commands"),
    CommandType.TEAM: ("team", "Team commands available to all staff"),
    CommandType.MANAGEMENT: ("manage", "Staff management commands"),
    CommandType.MODERATOR: ("mod", "Moderation commands"),
    CommandType.SUPPORT: ("support", "Support commands"),
    CommandType.COMMUNICATION: ("com", "Communication commands"),
}

_ANNOTATIONS = {
    "string": str,
    "integer": int,
    "boolean": bool,
    "number": float,
    "user": discord.User,
    "member": discord.Member,
    "channel": discord.abc.GuildChannel,
    "role": discord.Role,
    "attachment": discord.Attachment,
}


def discover_commands() -> None:
    """Import all modules under ``staff.commands`` to populate the registry."""
    import staff.commands as commands_pkg

    for mod in pkgutil.walk_packages(commands_pkg.__path__, commands_pkg.__name__ + "."):
        if mod.ispkg:
            continue
        try:
            importlib.import_module(mod.name)
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("Failed to import staff command module %s: %s", mod.name, exc, exc_info=True)


def _option_annotation(opt: SlashOption):
    if opt.choices:
        from typing import Literal
        ann = Literal[tuple(opt.choices)]  # type: ignore[valid-type]
    else:
        ann = _ANNOTATIONS.get(opt.type, str)
    if not opt.required:
        ann = Optional[ann]
    return ann


def _build_app_command(command: StaffCommand, runner: Callable) -> app_commands.Command:
    """Dynamically build an ``app_commands.Command`` for a staff command.

    The generated callback has a synthetic signature (``interaction`` + the
    declared options + a trailing ``incognito`` boolean) so discord.py can
    extract the slash options, while the body simply forwards to ``runner``.
    """

    async def _callback(interaction: discord.Interaction, **kwargs):
        incognito = kwargs.pop("incognito", True)
        await runner(command, interaction, kwargs, incognito)

    params = [inspect.Parameter("interaction", inspect.Parameter.POSITIONAL_OR_KEYWORD,
                                annotation=discord.Interaction)]
    descriptions: Dict[str, str] = {}

    for opt in command.options:
        annotation = _option_annotation(opt)
        default = inspect.Parameter.empty if opt.required else opt.default
        params.append(inspect.Parameter(
            opt.name, inspect.Parameter.KEYWORD_ONLY,
            annotation=annotation, default=default,
        ))
        descriptions[opt.name] = (opt.description or "…")[:100]

    # Shared incognito option (default True => ephemeral by default).
    params.append(inspect.Parameter(
        "incognito", inspect.Parameter.KEYWORD_ONLY, annotation=bool, default=True,
    ))
    descriptions["incognito"] = "Keep the response private (ephemeral). Default: true."

    _callback.__signature__ = inspect.Signature(params)
    _callback.__annotations__ = {
        p.name: p.annotation for p in params if p.annotation is not inspect.Parameter.empty
    }
    app_commands.describe(**descriptions)(_callback)

    return app_commands.Command(
        name=command.name,
        description=(command.description or "…")[:100],
        callback=_callback,
    )


def build(bot, runner: Callable):
    """Instantiate commands and build the message indexes + slash groups.

    Returns ``(flat_index, subgroup_index, groups)``:
    - ``flat_index``    keyed by ``(type_value, name_or_alias)``
    - ``subgroup_index`` keyed by ``(type_value, group_name)`` -> ``{sub: cmd}``
    """
    instances: List[StaffCommand] = []
    for cls in get_registered_commands():
        try:
            instances.append(cls(bot))
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("Failed to instantiate staff command %s: %s", cls, exc, exc_info=True)

    flat_index: Dict[Tuple[str, str], StaffCommand] = {}
    subgroup_index: Dict[Tuple[str, str], Dict[str, StaffCommand]] = {}
    groups: List[app_commands.Group] = []

    for ctype, members in _group_by_type(instances).items():
        name, desc = SLASH_GROUPS[ctype]
        group = app_commands.Group(name=name, description=desc, guild_only=True)
        subgroups: Dict[str, app_commands.Group] = {}

        for cmd in members:
            try:
                app_cmd = _build_app_command(cmd, runner)
            except Exception as exc:  # pragma: no cover - defensive
                logger.error("Failed to build slash command /%s %s: %s", name, cmd.name, exc, exc_info=True)
                continue

            if cmd.group:
                sub = subgroups.get(cmd.group)
                if sub is None:
                    sub = app_commands.Group(name=cmd.group, description=(cmd.group_description or "…")[:100])
                    subgroups[cmd.group] = sub
                    group.add_command(sub)
                sub.add_command(app_cmd)
                bucket = subgroup_index.setdefault((ctype.value, cmd.group), {})
                bucket[cmd.name] = cmd
                for alias in getattr(cmd, "aliases", ()):
                    bucket[alias] = cmd
            else:
                group.add_command(app_cmd)
                flat_index[(ctype.value, cmd.name)] = cmd
                for alias in getattr(cmd, "aliases", ()):
                    flat_index[(ctype.value, alias)] = cmd

        groups.append(group)

    return flat_index, subgroup_index, groups


def _group_by_type(instances: List[StaffCommand]) -> Dict[CommandType, List[StaffCommand]]:
    grouped: Dict[CommandType, List[StaffCommand]] = {}
    for cmd in instances:
        grouped.setdefault(cmd.command_type, []).append(cmd)
    # Stable ordering by name within each group.
    for members in grouped.values():
        members.sort(key=lambda c: c.name)
    return grouped
