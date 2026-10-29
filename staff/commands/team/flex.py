"""`/team flex` — post a public message proving Moddy team membership.

The card itself is built by ``utils/staff_flex.py``, shared with the Flex
button ``/user`` shows a staffer looking themselves up in a DM.
"""

import logging

import discord

from staff.framework import StaffCommand, staff_command, design, CommandType
from utils.i18n import t
from utils.staff_flex import build_flex_view

logger = logging.getLogger("moddy.staff.team.flex")


@staff_command
class FlexCommand(StaffCommand):
    command_type = CommandType.TEAM
    name = "flex"
    description = "Post a public message proving you're a Moddy team member."

    async def execute(self, ctx):
        locale = ctx.locale
        view = await build_flex_view(ctx.author, locale)
        if view is None:
            await ctx.send(view=design.error(
                t("staff.team.flex.not_staff_title", locale=locale),
                t("staff.team.flex.not_staff", locale=locale),
            ))
            return

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
