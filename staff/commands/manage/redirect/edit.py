"""`/manage redirect edit` — edit a redirect link (opens a modal)."""

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType
from staff.commands.manage.redirect._modal import RedirectModal
from utils import emojis
from utils.i18n import t


@staff_command
class RedirectEditCommand(StaffCommand):
    command_type = CommandType.MANAGEMENT
    group = "redirect"
    group_description = "Manage redirect links"
    name = "edit"
    permission = "redirect_manage"
    description = "Edit a redirect link."
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
            await ctx.send(view=design.invalid_usage(ctx.locale, "m.redirect edit <id>"))
            return
        row = await ctx.bot.db.get_redirect(int(rid))
        if not row:
            await ctx.send(view=design.error(
                t("staff.manage.redirect.notfound_title", locale=ctx.locale),
                t("staff.manage.redirect.notfound", locale=ctx.locale, id=f"`{rid}`"),
            ))
            return
        await ctx.open_modal(
            lambda: RedirectModal(ctx.bot, ctx.locale, redirect_id=int(rid), prefill=row),
            label=t("staff.manage.redirect.modal_edit", locale=ctx.locale), emoji=emojis.EDIT,
            prompt_title=t("staff.manage.redirect.modal_edit", locale=ctx.locale),
        )
