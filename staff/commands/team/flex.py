"""`/team flex` — post a public message proving Moddy team membership."""

import logging

import discord
from discord import ui

from staff.framework import StaffCommand, staff_command, design, CommandType
from utils import emojis
from utils.i18n import t
from utils.staff_permissions import staff_permissions
from cogs.error_handler import BaseView

logger = logging.getLogger("moddy.staff.team.flex")

ROLE_KEY = {
    "Dev": "developer",
    "Manager": "manager",
    "Supervisor_Mod": "mod_supervisor",
    "Supervisor_Com": "member",
    "Supervisor_Sup": "support",
    "Moderator": "moderator",
    "Communication": "member",
    "Support": "support",
}


@staff_command
class FlexCommand(StaffCommand):
    command_type = CommandType.TEAM
    name = "flex"
    description = "Post a public message proving you're a Moddy team member."

    async def execute(self, ctx):
        locale = ctx.locale
        roles = await staff_permissions.get_user_roles(ctx.author.id)
        if not roles:
            await ctx.send(view=design.error(
                t("staff.team.flex.not_staff_title", locale=locale),
                t("staff.team.flex.not_staff", locale=locale),
            ))
            return

        role_key = ROLE_KEY.get(roles[0].value, "member")
        role_display = t(f"staff.team.flex.roles.{role_key}", locale=locale)

        view = BaseView()
        container = design.make_container("success")
        container.add_item(ui.TextDisplay(
            f"{emojis.VERIFIED} {ctx.author.mention} "
            f"{t('staff.team.flex.message', locale=locale, role=role_display)}"
        ))
        container.add_item(ui.TextDisplay(
            f"-# {t('staff.team.flex.disclaimer', locale=locale)}\n"
            "-# [Report Staff](https://moddy.app/report-staff) • "
            "[Support](https://moddy.app/support) • [Documentation](https://docs.moddy.app/)"
        ))
        view.add_item(container)

        if ctx.is_slash:
            # The whole point is a public proof — never ephemeral.
            ctx.incognito = False
            await ctx.send(view=view)
        else:
            try:
                await ctx.channel.send(view=view)
                await ctx.message.delete()
            except discord.Forbidden:
                await ctx.send(view=design.error(
                    t("staff.common.error.title", locale=locale),
                    t("staff.team.flex.no_perm", locale=locale),
                ))
        logger.info("Staff %s used flex", ctx.author.id)
