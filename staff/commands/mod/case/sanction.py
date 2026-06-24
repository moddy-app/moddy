"""`/mod case sanction` — add a new sanction to an existing case."""

import discord

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType
from staff.commands.mod.case._shared import validate_reference, load_case, build_case_panel
from utils.i18n import t
from utils.case_management_views import AddSanctionView


@staff_command
class CaseSanctionCommand(StaffCommand):
    command_type = CommandType.MODERATOR
    group = "case"
    group_description = "Moderation case management"
    name = "sanction"
    permission = "case_sanction"
    description = "Add a new sanction to an existing case."
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

        locale = ctx.locale

        async def _on_done(interaction: discord.Interaction):
            updated = await load_case(ctx.bot, reference)
            await interaction.followup.send(view=build_case_panel(ctx, updated), ephemeral=True)

        view = AddSanctionView(
            bot=ctx.bot, staff_id=ctx.author.id, case_id=case.id, reference=reference,
            case_type=case.type, locale=locale, on_done=_on_done,
        )
        await ctx.send(view=view)
