"""`/manage redirect delete` — delete a redirect link (confirmed)."""

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType, ConfirmView
from utils import emojis
from utils.i18n import t


@staff_command
class RedirectDeleteCommand(StaffCommand):
    command_type = CommandType.MANAGEMENT
    group = "redirect"
    group_description = "Manage redirect links"
    name = "delete"
    permission = "redirect_manage"
    description = "Delete a redirect link."
    options = [
        SlashOption("id", "integer", "Redirect id.", required=True),
    ]

    def parse_message(self, raw: str) -> dict:
        try:
            return {"id": int((raw or "").strip())}
        except ValueError:
            return {"id": None}

    async def execute(self, ctx):
        rid = ctx.opt("id")
        if rid is None:
            await ctx.send(view=design.invalid_usage(ctx.locale, "m.redirect delete <id>"))
            return
        row = await ctx.bot.db.get_redirect(int(rid))
        if not row:
            await ctx.send(view=design.error(
                t("staff.manage.redirect.notfound_title", locale=ctx.locale),
                t("staff.manage.redirect.notfound", locale=ctx.locale, id=f"`{rid}`"),
            ))
            return

        locale = ctx.locale

        async def _do(interaction):
            await ctx.bot.db.delete_redirect(int(rid))
            return design.success(
                t("staff.manage.redirect.deleted_title", locale=locale),
                t("staff.manage.redirect.deleted", locale=locale, id=f"`{rid}`", src=f"`{row['domain']}{row['path']}`"),
            )

        await ctx.send(view=ConfirmView(
            bot=ctx.bot, author_id=ctx.author.id, locale=locale,
            title=t("staff.manage.redirect.confirm_title", locale=locale),
            description=t("staff.manage.redirect.confirm", locale=locale, src=f"`{row['domain']}{row['path']}`"),
            on_confirm=_do, emoji=emojis.DELETE,
        ))
