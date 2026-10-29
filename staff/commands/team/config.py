"""`/team config` — this server's /config panel, for a staffer stepping in.

The panel is the real ``/config`` one and is only ever shown privately, so
nobody but Moddy staff can interact with it — see
``utils/staff_config_views.py``. A message command cannot reply ephemerally:
``t.config`` posts a launcher card whose button (staff-only, re-checked on
every click) opens the panel for whoever clicks it.
"""

from staff.framework import StaffCommand, staff_command, design, CommandType
from utils.i18n import t
from utils.staff_config_views import StaffConfigLauncherView, build_staff_config_panel


@staff_command
class TeamConfigCommand(StaffCommand):
    command_type = CommandType.TEAM
    name = "config"
    description = "Open this server's /config panel (only staff can use it)."

    async def execute(self, ctx):
        locale = ctx.locale
        if not ctx.guild or ctx.guild.me is None:
            await ctx.send(view=design.error(
                t("staff.team.config.failed_title", locale=locale),
                t("staff.team.config.guild_only", locale=locale),
            ))
            return

        if not ctx.is_slash:
            await ctx.send(view=StaffConfigLauncherView(ctx.bot, locale))
            return

        # Slash: answer with the panel itself, always privately — `incognito:
        # false` would let anyone in the channel click it.
        ctx.incognito = True
        await ctx.defer()
        view = await build_staff_config_panel(ctx.bot, ctx.guild.id, ctx.author.id, locale)
        await ctx.send(view=view)
