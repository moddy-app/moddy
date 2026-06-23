"""`/dev shutdown` — gracefully shut the bot down."""

import logging

from staff.framework import StaffCommand, staff_command, design, CommandType
from utils.i18n import t

logger = logging.getLogger("moddy.staff.dev.shutdown")


@staff_command
class ShutdownCommand(StaffCommand):
    command_type = CommandType.DEV
    name = "shutdown"
    description = "Shut the bot down."

    async def execute(self, ctx):
        await ctx.send(view=design.warning(
            t("staff.dev.shutdown.title", locale=ctx.locale),
            t("staff.dev.shutdown.description", locale=ctx.locale),
        ))
        logger.info("Bot shutdown requested by %s (%s)", ctx.author, ctx.author.id)
        await ctx.bot.close()
