"""`/manage customization list` — list every server with staff-granted Bot
Customization access (the ``BOT_CUSTOMIZATION`` guild attribute).

Premium-covered servers are not listed here — this shows only the staff
override, not the full set of servers that can currently use the feature.
"""

from staff.framework import StaffCommand, staff_command, design, CommandType
from utils import emojis
from utils.i18n import t


@staff_command
class CustomizationListCommand(StaffCommand):
    command_type = CommandType.MANAGEMENT
    group = "customization"
    group_description = "Manage the Bot Customization feature"
    name = "list"
    permission = "bot_customization_manage"
    description = "List servers with staff-granted Bot Customization access."

    async def execute(self, ctx):
        bot = ctx.bot
        locale = ctx.locale

        if not bot.db:
            await ctx.send(view=design.error(
                t("staff.common.error.title", locale=locale),
                t("staff.dev.db_unavailable", locale=locale),
            ))
            return

        guild_ids = await bot.db.get_guilds_with_attribute("BOT_CUSTOMIZATION")
        if not guild_ids:
            await ctx.send(view=design.info(
                t("staff.manage.customization.list_title", locale=locale),
                t("staff.manage.customization.empty", locale=locale),
            ))
            return

        lines = []
        for gid in guild_ids[:50]:
            guild = bot.get_guild(gid)
            name = guild.name if guild else t("staff.manage.customization.not_present_short", locale=locale)
            lines.append(f"`{gid}` — **{name}**")

        await ctx.send(view=design.panel(
            "info",
            t("staff.manage.customization.list_title", locale=locale),
            f"**{t('staff.manage.customization.total', locale=locale, count=len(guild_ids))}**\n\n"
            + "\n".join(lines),
            emoji=emojis.MODDY_SQUARE, accent="primary",
        ))
