"""`/manage banner list` — list all banners."""

from staff.framework import StaffCommand, staff_command, design, CommandType
from utils import emojis
from utils.i18n import t


@staff_command
class BannerListCommand(StaffCommand):
    command_type = CommandType.MANAGEMENT
    group = "banner"
    group_description = "Manage site/dashboard banners"
    name = "list"
    permission = "banner_manage"
    description = "List all banners."

    async def execute(self, ctx):
        rows = await ctx.bot.db.list_banners()
        if not rows:
            await ctx.send(view=design.info(
                t("staff.manage.banner.list_title", locale=ctx.locale),
                t("staff.manage.banner.empty", locale=ctx.locale),
            ))
            return
        lines = []
        for r in rows[:20]:
            active = "**[ACTIVE]** " if r["is_active"] else ""
            kind = r["type"] or "custom"
            msg = r["message"][:60] + ("…" if len(r["message"]) > 60 else "")
            lines.append(f"`{r['id']}` {active}**{kind}** — {msg}")
        await ctx.send(view=design.panel(
            "info",
            t("staff.manage.banner.list_title", locale=ctx.locale),
            f"**{t('staff.manage.banner.total', locale=ctx.locale, count=len(rows))}**\n\n" + "\n".join(lines),
            emoji=emojis.BANNER, accent="primary",
        ))
