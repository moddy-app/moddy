"""`/dev disable` — disable a cog at runtime."""

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType
from utils.i18n import t


@staff_command
class DisableCommand(StaffCommand):
    command_type = CommandType.DEV
    name = "disable"
    description = "Disable a cog at runtime."
    options = [
        SlashOption("cog", "string", "Cog class name (e.g. Invite, Reminder).", required=True),
    ]

    async def execute(self, ctx):
        cog_name = (ctx.opt("cog") or "").strip()
        if not cog_name:
            await ctx.send(view=design.invalid_usage(ctx.locale, "d.disable <CogName>"))
            return

        cog_manager = ctx.bot.get_cog("CogManager")
        if not cog_manager:
            await ctx.send(view=design.error(
                t("staff.common.error.title", locale=ctx.locale),
                t("staff.dev.cogmanager_missing", locale=ctx.locale),
            ))
            return

        ok, result = await cog_manager.disable_cog(cog_name)
        if ok:
            await ctx.send(view=design.success(t("staff.dev.disable.title", locale=ctx.locale), result))
        else:
            await ctx.send(view=design.error(t("staff.dev.disable.fail_title", locale=ctx.locale), result))
