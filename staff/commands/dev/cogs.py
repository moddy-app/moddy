"""`/dev cogs` — list all loaded cogs and their enabled/disabled state."""

from staff.framework import StaffCommand, staff_command, design, CommandType
from utils import emojis
from utils.i18n import t


@staff_command
class CogsCommand(StaffCommand):
    command_type = CommandType.DEV
    name = "cogs"
    description = "List all loaded cogs and their status."

    async def execute(self, ctx):
        bot = ctx.bot
        cog_manager = bot.get_cog("CogManager")
        disabled = cog_manager.disabled_cogs if cog_manager else set()

        names = sorted(bot.cogs.keys())
        lines = []
        for name in names:
            state = (
                f"{emojis.UNDONE} {t('staff.dev.cogs.disabled', locale=ctx.locale)}"
                if name in disabled
                else f"{emojis.DONE} {t('staff.dev.cogs.enabled', locale=ctx.locale)}"
            )
            lines.append(f"`{name}` — {state}")

        body = "\n".join(lines) if lines else f"-# {t('staff.dev.cogs.none', locale=ctx.locale)}"
        await ctx.send(view=design.panel(
            "developer",
            t("staff.dev.cogs.title", locale=ctx.locale),
            f"**{t('staff.dev.cogs.total', locale=ctx.locale, count=len(names))}**\n\n{body}",
            emoji=emojis.COMMANDS,
        ))
