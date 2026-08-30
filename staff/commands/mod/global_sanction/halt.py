"""`/mod global halt` — stop a group's enforcement countdown (an appeal came in).

The deferred consequences of a global sanction — cancelling a premium
subscription, leaving the sanctioned servers, dropping their data — run 48h
after the notice **unless the subject appeals**. This is what a staff member
runs the moment an appeal lands: the sanction itself stays in force, only the
irreversible part is put on hold while the appeal is reviewed.
"""

import discord

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType
from staff.commands.mod.global_sanction._shared import (
    GROUP, GROUP_DESCRIPTION, resolve_group, group_level, not_found,
)
from utils.global_sanction_views import GlobalSanctionHaltModal, build_group_panel
from utils.i18n import t


@staff_command
class GlobalHaltCommand(StaffCommand):
    command_type = CommandType.MODERATOR
    group = GROUP
    group_description = GROUP_DESCRIPTION
    name = "halt"
    permission = "global_enforcement"
    description = "Stop the enforcement countdown of a group (appeal received)."
    # Answers with a Modal: Discord refuses one on a deferred interaction.
    opens_modal = True
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
        if not enforcement or enforcement.get("status") != "pending":
            await ctx.send(view=design.warning(
                t("global_sanctions.staff.halt_failed_title", locale=ctx.locale),
                t("global_sanctions.staff.halt_failed", locale=ctx.locale),
            ))
            return

        locale = ctx.locale
        bot = ctx.bot
        summary = [{
            "id": str(c["id"]), "reference": c["reference"],
            "subject_type": str(c["subject_type"]), "subject_id": str(c["subject_id"]),
        } for c in cases]
        level = group_level(cases)

        async def _on_done(interaction: discord.Interaction, row):
            await interaction.followup.send(view=build_group_panel(
                bot=bot, level=level, group_id=group_id, cases=summary,
                enforcement=row, notified=bool(row.get("notified")),
                locale=locale,
            ), ephemeral=True)

        await ctx.open_modal(
            lambda: GlobalSanctionHaltModal(
                bot=bot, group_id=group_id, locale=locale, on_done=_on_done,
            ),
            label=t("global_sanctions.staff.halt_button", locale=locale),
            prompt_title=t("global_sanctions.staff.halt_title", locale=locale),
            prompt_description=t("global_sanctions.staff.halt_prompt", locale=locale),
        )
