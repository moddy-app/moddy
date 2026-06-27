"""`/mod case close` — manually close (or reopen) a moderation case.

Manual status changes lock the case status: an expiring/revoked sanction will
not auto-reopen or auto-close a case a moderator has set by hand.
"""

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType
from staff.commands.mod.case._shared import validate_reference, load_case, build_case_panel
from utils.i18n import t
from utils.moderation_cases import CaseStatus


@staff_command
class CaseCloseCommand(StaffCommand):
    command_type = CommandType.MODERATOR
    group = "case"
    group_description = "Moderation case management"
    name = "close"
    permission = "case_close"
    description = "Close a case (or reopen it if already closed)."
    options = [
        SlashOption("reference", "string", "The public case reference.", required=True),
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

        new_status = CaseStatus.OPEN if case.status == CaseStatus.CLOSED else CaseStatus.CLOSED
        await ctx.bot.db.set_status_manual(
            case.id, new_status.value, "moddy_staff", ctx.author.id,
        )

        updated = await load_case(ctx.bot, reference)
        await ctx.send(view=build_case_panel(ctx, updated))
