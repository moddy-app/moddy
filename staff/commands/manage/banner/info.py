"""`/manage banner info` — show a banner's details."""

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType
from utils import emojis
from utils.i18n import t


@staff_command
class BannerInfoCommand(StaffCommand):
    command_type = CommandType.MANAGEMENT
    group = "banner"
    group_description = "Manage site/dashboard banners"
    name = "info"
    permission = "banner_manage"
    description = "Show a banner's details."
    options = [SlashOption("id", "integer", "Banner id.", required=True)]

    def parse_message(self, raw: str) -> dict:
        try:
            return {"id": int((raw or "").strip())}
        except ValueError:
            return {"id": None}

    async def execute(self, ctx):
        bid = ctx.opt("id")
        if bid is None:
            await ctx.send(view=design.invalid_usage(ctx.locale, "m.banner info <id>"))
            return
        row = await ctx.bot.db.get_banner(int(bid))
        if not row:
            await ctx.send(view=design.error(
                t("staff.manage.banner.notfound_title", locale=ctx.locale),
                t("staff.manage.banner.notfound", locale=ctx.locale, id=f"`{bid}`"),
            ))
            return

        locale = ctx.locale
        kind = row["type"] or "custom"
        fields = [
            {"name": t("staff.manage.banner.active", locale=locale), "value": emojis.DONE if row["is_active"] else emojis.UNDONE},
            {"name": t("staff.manage.banner.kind", locale=locale), "value": f"`{kind}`"},
            {"name": t("staff.manage.banner.visibility", locale=locale),
             "value": f"Dashboard: {emojis.DONE if row['show_dashboard'] else emojis.UNDONE} • Website: {emojis.DONE if row['show_website'] else emojis.UNDONE}"},
            {"name": t("staff.manage.banner.message", locale=locale), "value": row["message"][:600]},
        ]
        if row["type"] is None:
            fields.append({"name": t("staff.manage.banner.color", locale=locale), "value": f"`{row['color']}`"})
        await ctx.send(view=design.panel(
            "info", t("staff.manage.banner.info_title", locale=locale, id=row["id"]), "",
            fields=fields, emoji=emojis.BANNER, accent="primary",
        ))
