"""`/mod global lift` — lift a whole global sanction group.

Revokes every active sanction of every case in the group (user *and* servers)
and cancels the enforcement countdown. This is the "the appeal was accepted"
button: one command undoes the whole infraction rather than making a moderator
revoke case by case.
"""

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType
from staff.commands.mod.global_sanction._shared import (
    GROUP, GROUP_DESCRIPTION, resolve_group, group_level, not_found,
)
from utils.global_sanction_views import build_group_panel
from utils.i18n import t


@staff_command
class GlobalLiftCommand(StaffCommand):
    command_type = CommandType.MODERATOR
    group = GROUP
    group_description = GROUP_DESCRIPTION
    name = "lift"
    permission = "global_sanction"
    description = "Lift every sanction of a global sanction group."
    options = [
        SlashOption("target", "string",
                    "Group id, or any case reference inside it.", required=True),
        SlashOption("reason", "string", "Why it is lifted.", required=False),
    ]

    def parse_message(self, raw: str) -> dict:
        """``mod.global lift <group|reference> [reason…]``."""
        parts = (raw or "").split(maxsplit=1)
        return {
            "target": parts[0] if parts else None,
            "reason": parts[1] if len(parts) > 1 else None,
        }

    async def execute(self, ctx):
        group_id, cases = await resolve_group(ctx.bot, ctx.opt("target"))
        if not group_id:
            await ctx.send(view=not_found(ctx.locale))
            return

        level = group_level(cases)
        result = await ctx.bot.global_sanctions.lift(
            group_id, by_id=ctx.author.id, reason=ctx.opt("reason"),
        )

        if not result["revoked"]:
            await ctx.send(view=design.warning(
                t("global_sanctions.staff.lift_nothing_title", locale=ctx.locale),
                t("global_sanctions.staff.lift_nothing", locale=ctx.locale),
            ))
            return

        enforcement = await ctx.bot.db.get_enforcement(group_id)
        await ctx.send(view=design.success(
            t("global_sanctions.staff.lift_done_title", locale=ctx.locale),
            t("global_sanctions.staff.lift_done", locale=ctx.locale,
              count=result["revoked"], cases=result["cases"]),
        ))
        await ctx.send(view=build_group_panel(
            bot=ctx.bot, level=level, group_id=group_id,
            cases=[{
                "id": str(c["id"]), "reference": c["reference"],
                "subject_type": str(c["subject_type"]), "subject_id": str(c["subject_id"]),
            } for c in cases],
            enforcement=enforcement, locale=ctx.locale, actions=False,
        ))
