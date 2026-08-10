"""`/mod global view` — the status of a global sanction group.

Takes a group id **or** any case reference belonging to it, and shows the whole
infraction: level, every case, and the enforcement countdown (with a live
*Halt countdown* button when one is still running).
"""

from staff.framework import StaffCommand, SlashOption, staff_command, CommandType
from staff.commands.mod.global_sanction._shared import (
    GROUP, GROUP_DESCRIPTION, resolve_group, group_level, not_found,
)
from utils.global_sanction_views import build_group_panel


@staff_command
class GlobalViewCommand(StaffCommand):
    command_type = CommandType.MODERATOR
    group = GROUP
    group_description = GROUP_DESCRIPTION
    name = "view"
    permission = "case_view"
    description = "View a global sanction group and its enforcement countdown."
    options = [
        SlashOption("target", "string",
                    "Group id, or any case reference inside it.", required=True),
    ]

    async def execute(self, ctx):
        group_id, cases = await resolve_group(ctx.bot, ctx.opt("target"))
        if not group_id:
            await ctx.send(view=not_found(ctx.locale))
            return

        enforcement = await ctx.bot.db.get_enforcement(group_id)
        await ctx.send(view=build_group_panel(
            bot=ctx.bot,
            level=group_level(cases),
            group_id=group_id,
            cases=[{
                "id": str(c["id"]), "reference": c["reference"],
                "subject_type": str(c["subject_type"]), "subject_id": str(c["subject_id"]),
            } for c in cases],
            enforcement=enforcement,
            notified=bool(enforcement.get("notified")) if enforcement else None,
            reason=cases[0]["reason"] if cases else None,
            locale=ctx.locale,
        ))
