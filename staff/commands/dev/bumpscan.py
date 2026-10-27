"""`/dev bumpscan` — rescan every server for bump prospects.

Queues every server Moddy is in; the service works through them at its usual
pace and posts a summary in the prospect channel when the run is over. Servers
that already have a card keep it — a rescan never posts a second one.
See docs/features/BUMP_PROSPECTS.md.
"""

import logging

from staff.framework import StaffCommand, staff_command, design, CommandType
from utils import emojis
from utils.i18n import t

logger = logging.getLogger("moddy.staff.dev.bumpscan")


@staff_command
class BumpScanCommand(StaffCommand):
    command_type = CommandType.DEV
    name = "bumpscan"
    description = "Rescan every server for bump prospects."

    async def execute(self, ctx):
        locale = ctx.locale
        service = getattr(ctx.bot, "bump_prospects", None)
        if service is None or not service.enabled:
            await ctx.send(view=design.error(
                t("bump_prospects.scan.disabled.title", locale=locale),
                t("bump_prospects.scan.disabled.description", locale=locale)))
            return

        total = service.scan_all(requested_by=ctx.author.id)
        minutes = max(1, round(service.eta_seconds() / 60))
        await ctx.send(view=design.info(
            t("bump_prospects.scan.started.title", locale=locale),
            t("bump_prospects.scan.started.description", locale=locale,
              count=f"`{total}`", minutes=f"`{minutes}`"),
            emoji=emojis.REFRESH))
        logger.info("Bump prospect rescan of %s servers requested by %s", total, ctx.author.id)
