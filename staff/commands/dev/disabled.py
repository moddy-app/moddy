"""`/dev disabled` — list cogs currently disabled at runtime."""

from staff.framework import StaffCommand, staff_command, design, CommandType
from utils import emojis
from utils.i18n import t


@staff_command
class DisabledCommand(StaffCommand):
    command_type = CommandType.DEV
    name = "disabled"
    description = "List cogs currently disabled at runtime."

    async def execute(self, ctx):
        cog_manager = ctx.bot.get_cog("CogManager")
        if not cog_manager:
            await ctx.send(view=design.error(
                t("staff.common.error.title", locale=ctx.locale),
                t("staff.dev.cogmanager_missing", locale=ctx.locale),
            ))
            return

        disabled = cog_manager.get_disabled_cogs()
        if not disabled:
            await ctx.send(view=design.success(
                t("staff.dev.disabled.none_title", locale=ctx.locale),
                t("staff.dev.disabled.none_description", locale=ctx.locale),
            ))
            return

        listing = "\n".join(f"`{name}`" for name in disabled)
        await ctx.send(view=design.panel(
            "developer",
            t("staff.dev.disabled.title", locale=ctx.locale),
            f"**{t('staff.dev.disabled.count', locale=ctx.locale, count=len(disabled))}**\n\n{listing}",
            emoji=emojis.UNDONE,
        ))
