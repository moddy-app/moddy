"""`/mod case note` — add a comment or internal note to a case's timeline."""

import discord

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType
from staff.commands.mod.case._shared import validate_reference, load_case
from utils import emojis
from utils.i18n import t
from utils.case_management_views import CaseCommentModal, CaseNoteModal


@staff_command
class CaseNoteCommand(StaffCommand):
    command_type = CommandType.MODERATOR
    group = "case"
    group_description = "Moderation case management"
    name = "note"
    permission = "case_note"
    description = "Add a comment or internal note to a case timeline."
    options = [
        SlashOption("reference", "string", "The public case reference.", required=True),
        SlashOption(
            "internal", "boolean",
            "Internal staff note (hidden from the subject). Default: a public comment.",
            required=False,
        ),
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
        internal = bool(ctx.opt("internal"))
        modal_cls = CaseNoteModal if internal else CaseCommentModal

        async def _on_done(interaction: discord.Interaction):
            title = "staff.mod.case.note_done_title" if internal else "staff.mod.case.comment_done_title"
            body = "staff.mod.case.note_done" if internal else "staff.mod.case.comment_done"
            await interaction.followup.send(view=design.success(
                t(title, locale=locale), t(body, locale=locale, id=f"`{reference}`"),
            ), ephemeral=True)

        def factory():
            return modal_cls(
                bot=ctx.bot, staff_id=ctx.author.id, case_id=case.id,
                reference=reference, locale=locale, on_done=_on_done,
            )

        label = t("staff.mod.case.note_label" if internal else "staff.mod.case.comment_label", locale=locale)
        await ctx.open_modal(
            factory, label=label, emoji=emojis.NOTE, prompt_title=label,
        )
