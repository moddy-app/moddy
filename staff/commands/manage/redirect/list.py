"""`/manage redirect list` — list all redirect links."""

from staff.framework import StaffCommand, staff_command, design, CommandType
from utils import emojis
from utils.i18n import t


@staff_command
class RedirectListCommand(StaffCommand):
    command_type = CommandType.MANAGEMENT
    group = "redirect"
    group_description = "Manage redirect links"
    name = "list"
    permission = "redirect_manage"
    description = "List all redirect links."

    async def execute(self, ctx):
        rows = await ctx.bot.db.list_redirects()
        if not rows:
            await ctx.send(view=design.info(
                t("staff.manage.redirect.list_title", locale=ctx.locale),
                t("staff.manage.redirect.empty", locale=ctx.locale),
            ))
            return
        lines = [f"`{r['id']}` **{r['domain']}{r['path']}** → `{r['target']}`\n-# {r['description']}" for r in rows[:20]]
        await ctx.send(view=design.panel(
            "info",
            t("staff.manage.redirect.list_title", locale=ctx.locale),
            f"**{t('staff.manage.redirect.total', locale=ctx.locale, count=len(rows))}**\n\n" + "\n\n".join(lines),
            emoji=emojis.WEB, accent="primary",
        ))
