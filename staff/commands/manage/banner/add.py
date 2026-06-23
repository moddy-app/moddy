"""`/manage banner add` — create a banner (pick typed or custom, then a modal)."""

from staff.framework import StaffCommand, staff_command, CommandType
from staff.commands.manage.banner._modals import BannerTypeSelectView


@staff_command
class BannerAddCommand(StaffCommand):
    command_type = CommandType.MANAGEMENT
    group = "banner"
    group_description = "Manage site/dashboard banners"
    name = "add"
    permission = "banner_manage"
    description = "Create a site/dashboard banner."

    async def execute(self, ctx):
        await ctx.send(view=BannerTypeSelectView(ctx.bot, ctx.author.id, ctx.locale))
