"""`/manage banner edit` — edit a banner (pick typed or custom, then a modal)."""

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType
from staff.commands.manage.banner._modals import BannerTypeSelectView
from utils.i18n import t


@staff_command
class BannerEditCommand(StaffCommand):
    command_type = CommandType.MANAGEMENT
    group = "banner"
    group_description = "Manage site/dashboard banners"
    name = "edit"
    permission = "banner_manage"
    description = "Edit a banner."
    options = [SlashOption("id", "integer", "Banner id.", required=True)]

    def parse_message(self, raw: str) -> dict:
        try:
            return {"id": int((raw or "").strip())}
        except ValueError:
            return {"id": None}

    async def execute(self, ctx):
        bid = ctx.opt("id")
        if bid is None:
            await ctx.send(view=design.invalid_usage(ctx.locale, "m.banner edit <id>"))
            return
        row = await ctx.bot.db.get_banner(int(bid))
        if not row:
            await ctx.send(view=design.error(
                t("staff.manage.banner.notfound_title", locale=ctx.locale),
                t("staff.manage.banner.notfound", locale=ctx.locale, id=f"`{bid}`"),
            ))
            return
        await ctx.send(view=BannerTypeSelectView(ctx.bot, ctx.author.id, ctx.locale, banner_id=int(bid), prefill=row))
