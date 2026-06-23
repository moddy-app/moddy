"""`/mod case edit` — edit an open moderation case (opens a modal)."""

import discord

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType
from staff.commands.mod.case._shared import validate_case_id
from utils import emojis
from utils.i18n import t
from utils.moderation_cases import ModerationCase, CaseStatus
from utils.case_management_views import EditCaseModal


@staff_command
class CaseEditCommand(StaffCommand):
    command_type = CommandType.MODERATOR
    group = "case"
    group_description = "Moderation case management"
    name = "edit"
    permission = "case_edit"
    description = "Edit an open moderation case."
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

        case = ModerationCase.from_db(case_dict)
        if case.status == CaseStatus.CLOSED:
            await ctx.send(view=design.warning(
                t("staff.mod.case.closed_title", locale=ctx.locale),
                t("staff.mod.case.closed_edit", locale=ctx.locale, id=f"`{case_id}`"),
            ))
            return

        locale = ctx.locale

        async def _on_done(interaction: discord.Interaction):
            await interaction.followup.send(view=design.success(
                t("staff.mod.case.edit_done_title", locale=locale),
                t("staff.mod.case.edit_done", locale=locale, id=f"`{case_id}`"),
            ), ephemeral=True)

        def factory():
            modal = EditCaseModal(
                case_id=case.case_id, current_reason=case.reason,
                current_evidence=case.evidence, current_duration=case.duration,
                sanction_type=case.sanction_type, staff_id=ctx.author.id,
                callback_func=_on_done,
            )
            modal.bot = ctx.bot
            return modal

        await ctx.open_modal(
            factory, label=t("staff.mod.case.edit_button", locale=locale), emoji=emojis.EDIT,
            prompt_title=t("staff.mod.case.edit_button", locale=locale),
        )
