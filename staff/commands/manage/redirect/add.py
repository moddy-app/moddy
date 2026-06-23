"""`/manage redirect add` — create a redirect link (opens a modal)."""

from staff.framework import StaffCommand, staff_command, CommandType
from staff.commands.manage.redirect._modal import RedirectModal
from utils import emojis
from utils.i18n import t


@staff_command
class RedirectAddCommand(StaffCommand):
    command_type = CommandType.MANAGEMENT
    group = "redirect"
    group_description = "Manage redirect links"
    name = "add"
    permission = "redirect_manage"
    description = "Create a redirect link."

    async def execute(self, ctx):
        await ctx.open_modal(
            lambda: RedirectModal(ctx.bot, ctx.locale),
            label=t("staff.manage.redirect.modal_add", locale=ctx.locale), emoji=emojis.ADD,
            prompt_title=t("staff.manage.redirect.modal_add", locale=ctx.locale),
        )
