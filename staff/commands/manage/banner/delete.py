"""`/manage banner delete` — delete a banner (confirmed)."""

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType, ConfirmView
from utils import emojis
from utils.i18n import t


@staff_command
class BannerDeleteCommand(StaffCommand):
    command_type = CommandType.MANAGEMENT
    group = "banner"
    group_description = "Manage site/dashboard banners"
    name = "delete"
    permission = "banner_manage"
    description = "Delete a banner."
    options = [SlashOption("id", "integer", "Banner id.", required=True)]

    def parse_message(self, raw: str) -> dict:
        try:
            return {"id": int((raw or "").strip())}
        except ValueError:
            return {"id": None}

    async def execute(self, ctx):
        bid = ctx.opt("id")
        if bid is None:
            await ctx.send(view=design.invalid_usage(ctx.locale, "m.banner delete <id>"))
            return
        row = await ctx.bot.db.get_banner(int(bid))
        if not row:
            await ctx.send(view=design.error(
                t("staff.manage.banner.notfound_title", locale=ctx.locale),
                t("staff.manage.banner.notfound", locale=ctx.locale, id=f"`{bid}`"),
            ))
            return

        locale = ctx.locale

        async def _do(interaction):
            await ctx.bot.db.delete_banner(int(bid))
            return design.success(
                t("staff.manage.banner.deleted_title", locale=locale),
                t("staff.manage.banner.deleted", locale=locale, id=f"`{bid}`"),
            )

        await ctx.send(view=ConfirmView(
            bot=ctx.bot, author_id=ctx.author.id, locale=locale,
            title=t("staff.manage.banner.confirm_title", locale=locale),
            description=t("staff.manage.banner.confirm", locale=locale, id=f"`{bid}`"),
            on_confirm=_do, emoji=emojis.DELETE,
        ))
