"""`/mod case view` — show a moderation case (sidebar + timeline) by reference."""

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType
from staff.commands.mod.case._shared import validate_reference, load_case, build_case_panel
from utils.i18n import t


@staff_command
class CaseViewCommand(StaffCommand):
    command_type = CommandType.MODERATOR
    group = "case"
    group_description = "Moderation case management"
    name = "view"
    permission = "case_view"
    description = "View a moderation case by reference."
    options = [
        SlashOption("reference", "string", "The public case reference (e.g. A7F2K9).", required=True),
    ]

    async def execute(self, ctx):
        reference, error = validate_reference(ctx.opt("reference"), ctx.locale)
        if error:
            await ctx.send(view=error)
            return

        case = await load_case(ctx.bot, reference)
        if not case:
            await ctx.send(view=design.error(
                t("staff.mod.case.notfound_title", locale=ctx.locale),
                t("staff.mod.case.notfound", locale=ctx.locale, id=f"`{reference}`"),
            ))
            return

        await ctx.send(view=build_case_panel(ctx, case))
