"""`/manage banner activate` / `deactivate` — toggle the live banner."""

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType
from utils import emojis
from utils.i18n import t


@staff_command
class BannerActivateCommand(StaffCommand):
    command_type = CommandType.MANAGEMENT
    group = "banner"
    group_description = "Manage site/dashboard banners"
    name = "activate"
    permission = "banner_manage"
    description = "Activate a banner (deactivates all others)."
    options = [SlashOption("id", "integer", "Banner id.", required=True)]

    def parse_message(self, raw: str) -> dict:
        try:
            return {"id": int((raw or "").strip())}
        except ValueError:
            return {"id": None}

    async def execute(self, ctx):
        bid = ctx.opt("id")
        if bid is None:
            await ctx.send(view=design.invalid_usage(ctx.locale, "m.banner activate <id>"))
            return
        ok = await ctx.bot.db.activate_banner(int(bid))
        if ok:
            await ctx.send(view=design.success(
                t("staff.manage.banner.activated_title", locale=ctx.locale),
                t("staff.manage.banner.activated", locale=ctx.locale, id=f"`{bid}`"),
                emoji=emojis.DONE,
            ))
        else:
            await ctx.send(view=design.error(
                t("staff.manage.banner.notfound_title", locale=ctx.locale),
                t("staff.manage.banner.notfound", locale=ctx.locale, id=f"`{bid}`"),
            ))


@staff_command
class BannerDeactivateCommand(StaffCommand):
    command_type = CommandType.MANAGEMENT
    group = "banner"
    group_description = "Manage site/dashboard banners"
    name = "deactivate"
    permission = "banner_manage"
    description = "Deactivate the currently active banner."

    async def execute(self, ctx):
        count = await ctx.bot.db.deactivate_banner()
        if count:
            await ctx.send(view=design.success(
                t("staff.manage.banner.deactivated_title", locale=ctx.locale),
                t("staff.manage.banner.deactivated", locale=ctx.locale),
            ))
        else:
            await ctx.send(view=design.info(
                t("staff.manage.banner.none_active_title", locale=ctx.locale),
                t("staff.manage.banner.none_active", locale=ctx.locale),
            ))
