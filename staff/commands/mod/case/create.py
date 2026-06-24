"""`/mod case create` — open the case-creation flow for a user or guild."""

import discord

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType
from staff.commands.mod.case._shared import resolve_subject, load_case, build_case_panel
from utils.i18n import t
from utils.moderation_cases import SubjectType
from utils.case_management_views import CaseCreationView


@staff_command
class CaseCreateCommand(StaffCommand):
    command_type = CommandType.MODERATOR
    group = "case"
    group_description = "Moderation case management"
    name = "create"
    permission = "case_create"
    description = "Open a moderation case for a user or guild."
    options = [
        SlashOption("user", "user", "Target user.", required=False),
        SlashOption("guild_id", "string", "Target guild id (instead of a user).", required=False),
    ]

    async def execute(self, ctx):
        subject_type, subject_id, subject_name, error = await resolve_subject(ctx)
        if error:
            await ctx.send(view=error)
            return

        # Never open a case against Moddy staff / developers.
        if subject_type == SubjectType.DISCORD_USER:
            user_data = await ctx.bot.db.get_user(subject_id)
            if user_data["attributes"].get("TEAM") or ctx.bot.is_developer(subject_id):
                await ctx.send(view=design.error(
                    t("staff.mod.case.staff_target_title", locale=ctx.locale),
                    t("staff.mod.case.staff_target", locale=ctx.locale),
                ))
                return

        locale = ctx.locale

        async def _on_created(interaction: discord.Interaction, result):
            case = await load_case(ctx.bot, result["reference"])
            if case:
                await interaction.followup.send(view=build_case_panel(ctx, case), ephemeral=True)
            else:
                await interaction.followup.send(view=design.success(
                    t("staff.mod.case.create_done_title", locale=locale),
                    t("staff.mod.case.create_done", locale=locale, id=f"`{result['reference']}`"),
                ), ephemeral=True)

        view = CaseCreationView(
            bot=ctx.bot, staff_id=ctx.author.id, subject_type=subject_type,
            subject_id=subject_id, subject_name=subject_name, locale=locale,
            on_created=_on_created,
        )
        await ctx.send(view=view)
