"""`/mod case revoke` — revoke an active sanction of a case."""

import discord

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType
from staff.commands.mod.case._shared import validate_reference, load_case, build_case_panel
from utils.i18n import t
from utils.moderation_cases import SanctionStatus
from utils.case_management_views import RevokeSanctionView


@staff_command
class CaseRevokeCommand(StaffCommand):
    command_type = CommandType.MODERATOR
    group = "case"
    group_description = "Moderation case management"
    name = "revoke"
    permission = "case_sanction"
    description = "Revoke an active sanction of a case."
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

        active = [
            {"id": s.id, "action": s.action.value}
            for s in case.sanctions if s.status == SanctionStatus.ACTIVE
        ]
        if not active:
            await ctx.send(view=design.warning(
                t("staff.mod.case.no_active_title", locale=ctx.locale),
                t("staff.mod.case.no_active", locale=ctx.locale, id=f"`{reference}`"),
            ))
            return

        locale = ctx.locale

        async def _on_done(interaction: discord.Interaction):
            updated = await load_case(ctx.bot, reference)
            await interaction.followup.send(view=build_case_panel(ctx, updated), ephemeral=True)

        view = RevokeSanctionView(
            bot=ctx.bot, staff_id=ctx.author.id, reference=reference,
            sanctions=active, locale=locale, on_done=_on_done,
        )
        await ctx.send(view=view)
