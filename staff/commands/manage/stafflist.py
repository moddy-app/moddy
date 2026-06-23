"""`/manage list` — list all staff members grouped by role."""

from staff.framework import StaffCommand, staff_command, design, CommandType
from staff.framework import badges
from utils import emojis
from utils.i18n import t
from utils.staff_role_permissions import get_role_display_name

ROLE_ORDER = ["Manager", "Supervisor_Mod", "Supervisor_Com", "Supervisor_Sup",
              "Moderator", "Communication", "Support"]


@staff_command
class StaffListCommand(StaffCommand):
    command_type = CommandType.MANAGEMENT
    name = "list"
    aliases = ("stafflist",)
    description = "List all staff members grouped by role."

    async def execute(self, ctx):
        locale = ctx.locale
        members = await ctx.bot.db.get_all_staff_members()
        if not members:
            await ctx.send(view=design.info(
                t("staff.manage.list.title", locale=locale),
                t("staff.manage.list.empty", locale=locale),
            ))
            return

        by_role = {}
        for member in members:
            for role in member["roles"]:
                by_role.setdefault(role, []).append(member["user_id"])

        fields = []
        for role in ROLE_ORDER:
            if role in by_role:
                ids = by_role[role]
                fields.append({
                    "name": f"{badges.role_badge(role)} {get_role_display_name(role)} ({len(ids)})",
                    "value": ", ".join(f"<@{uid}>" for uid in ids),
                })

        await ctx.send(view=design.panel(
            "info",
            t("staff.manage.list.title", locale=locale),
            f"**{t('staff.manage.list.total', locale=locale, count=len(members))}**",
            fields=fields,
            emoji=emojis.MODDYTEAM_BADGE,
            accent="primary",
        ))
