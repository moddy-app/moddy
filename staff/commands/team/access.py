"""`/team access` — ask a server's administrator for permissions, in front of them.

Run it in the channel where the conversation is happening (a ticket, usually).
The staffer picks what they need from a fixed catalogue; the administrator gets
a card with *Accept* and *Refuse*; accepting adds those permissions to the
server's **Moddy Team** role — never to anything else, and never to a staff
member's account.

The flow lives in ``utils/team_access_views.py``; this file is the entry point
and the pre-flight checks that make the card worth showing at all: a Moddy Team
role to grant to, and a bot that actually holds what is being asked for.
"""

import logging

from staff.framework import StaffCommand, staff_command, design, CommandType
from utils.i18n import t
from utils.moddy_team_role import TEAM_ROLE_NAME, can_manage, find_team_role
from utils.team_access_views import TeamAccessPickerView

logger = logging.getLogger("moddy.staff.team.access")


@staff_command
class TeamAccessCommand(StaffCommand):
    command_type = CommandType.TEAM
    name = "access"
    description = "Ask this server's administrators for permissions on the Moddy Team role."

    async def execute(self, ctx):
        locale = ctx.locale

        # Deliberately guild-scoped, with no guild_id option: the whole point is
        # that an administrator is in the room to answer it.
        if not ctx.guild:
            await ctx.send(view=design.error(
                t("staff.team.access.failed_title", locale=locale),
                t("staff.team.access.guild_only", locale=locale),
            ))
            return

        role = await find_team_role(ctx.bot, ctx.guild)
        if not role:
            await ctx.send(view=design.error(
                t("staff.team.access.no_role_title", locale=locale),
                t("staff.team.access.no_role", locale=locale,
                  name=f"**{TEAM_ROLE_NAME}**"),
            ))
            return

        if not can_manage(ctx.guild, role):
            await ctx.send(view=design.error(
                t("staff.team.access.failed_title", locale=locale),
                t("staff.team.access.cannot_edit", locale=locale, role=role.mention),
            ))
            return

        await ctx.send(view=TeamAccessPickerView(ctx.author.id, 0, locale=locale))
