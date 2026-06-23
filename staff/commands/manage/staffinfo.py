"""`/manage staffinfo` — information about a staff member (defaults to self)."""

import discord

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType, parse_user_id
from staff.framework import badges
from utils import emojis
from utils.i18n import t
from utils.staff_permissions import StaffRole
from utils.staff_role_permissions import get_role_display_name


@staff_command
class StaffInfoCommand(StaffCommand):
    command_type = CommandType.MANAGEMENT
    name = "staffinfo"
    description = "Show information about a staff member (defaults to you)."
    options = [
        SlashOption("user", "user", "Staff member (optional).", required=False),
        SlashOption("user_id", "string", "Staff member id (optional).", required=False),
    ]

    def parse_message(self, raw: str) -> dict:
        return {"user_id": (raw or "").strip()}

    async def execute(self, ctx):
        bot = ctx.bot
        locale = ctx.locale

        target = ctx.opt("user")
        uid = target.id if target else parse_user_id(ctx.opt("user_id") or "")
        if not uid:
            uid = ctx.author.id

        try:
            user = await bot.fetch_user(uid)
        except discord.NotFound:
            await ctx.send(view=design.error(
                t("staff.team.user_notfound_title", locale=locale),
                t("staff.team.user_notfound", locale=locale, id=f"`{uid}`"),
            ))
            return

        perms = await bot.db.get_staff_permissions(uid)
        is_dev = bot.is_developer(uid)
        if not perms["roles"] and not is_dev:
            await ctx.send(view=design.error(
                t("staff.manage.not_staff_title", locale=locale),
                t("staff.manage.not_staff", locale=locale, user=user.mention),
            ))
            return

        role_lines = []
        roles = [StaffRole(r) for r in perms["roles"]] if perms["roles"] else []
        if is_dev:
            if StaffRole.DEV not in roles:
                role_lines.append(f"{badges.role_badge('Dev')} {get_role_display_name('Dev')} *(auto)*")
            if StaffRole.MANAGER not in roles:
                role_lines.append(f"{badges.role_badge('Manager')} {get_role_display_name('Manager')} *(auto)*")
        for role in roles:
            role_lines.append(f"{badges.role_badge(role.value)} {get_role_display_name(role.value)}")

        fields = [{
            "name": f"{emojis.STAFF} {t('staff.manage.info.roles', locale=locale)}",
            "value": "\n".join(role_lines) if role_lines else f"-# {t('staff.manage.info.no_roles', locale=locale)}",
        }]

        role_perms = perms.get("role_permissions", {}) or {}
        granted = sorted({p for plist in role_perms.values() for p in plist})
        if granted:
            fields.append({
                "name": f"{emojis.SETTINGS} {t('staff.manage.info.permissions', locale=locale)}",
                "value": " ".join(f"`{p}`" for p in granted[:40]),
            })

        if perms.get("created_at"):
            fields.append({"name": f"{emojis.TIME} {t('staff.manage.info.joined', locale=locale)}",
                           "value": f"<t:{int(perms['created_at'].timestamp())}:R>"})
        if perms.get("updated_at"):
            fields.append({"name": f"{emojis.TIME} {t('staff.manage.info.updated', locale=locale)}",
                           "value": f"<t:{int(perms['updated_at'].timestamp())}:R>"})

        rendered, _, _ = await badges.render_user(bot, user)
        await ctx.send(view=design.panel(
            "info",
            t("staff.manage.info.title", locale=locale),
            f"{rendered} (`{user.id}`)",
            fields=fields,
            emoji=emojis.STAFF,
            accent="primary",
        ))
