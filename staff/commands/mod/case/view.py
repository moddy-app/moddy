"""`/mod case view` — show a moderation case by id."""

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType
from staff.commands.mod.case._shared import validate_case_id, build_case_panel
from utils.i18n import t
from utils.moderation_cases import ModerationCase


@staff_command
class CaseViewCommand(StaffCommand):
    command_type = CommandType.MODERATOR
    group = "case"
    group_description = "Moderation case management"
    name = "view"
    permission = "case_view"
    description = "View a moderation case by id."
    options = [
        SlashOption("case_id", "string", "The 8-character case id.", required=True),
    ]

    async def execute(self, ctx):
        case_id, error = validate_case_id(ctx.opt("case_id"), ctx.locale)
        if error:
            await ctx.send(view=error)
            return

        case_dict = await ctx.bot.db.get_moderation_case(case_id)
        if not case_dict:
            await ctx.send(view=design.error(
                t("staff.mod.case.notfound_title", locale=ctx.locale),
                t("staff.mod.case.notfound", locale=ctx.locale, id=f"`{case_id}`"),
            ))
            return

        await ctx.send(view=await build_case_panel(ctx, ModerationCase.from_db(case_dict)))
