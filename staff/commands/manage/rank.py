"""`/manage rank` — give a member a single staff role.

The full editor lives in ``/manage staff`` (roles + granular permissions).
This command is the fast path: one member, one role, no panel — useful when
onboarding someone or adding a second role to an existing staff member.
Removing everything is ``/manage unrank``.
"""

import logging
from typing import Optional

import discord

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType, parse_user_id
from staff.framework import badges
from utils.i18n import t
from utils.staff_permissions import staff_permissions, StaffRole
from utils.staff_role_permissions import get_role_display_name
from staff.commands.manage.staff import ASSIGNABLE_ROLES

logger = logging.getLogger("moddy.staff.manage.rank")


def resolve_role(raw: str) -> Optional[StaffRole]:
    """Resolve a user-typed role name to an assignable :class:`StaffRole`.

    Accepts the stored value (``Supervisor_Mod``), the display name
    (``Moderation Supervisor``) and loose spacing/case variants of either,
    so ``m.rank @user moderation supervisor`` works as well as the slash
    choice list does.
    """
    key = " ".join((raw or "").split()).lower().replace("_", " ")
    if not key:
        return None
    for role in ASSIGNABLE_ROLES:
        candidates = {
            role.value.lower().replace("_", " "),
            get_role_display_name(role.value).lower(),
        }
        if key in candidates:
            return role
    return None


@staff_command
class RankCommand(StaffCommand):
    command_type = CommandType.MANAGEMENT
    name = "rank"
    description = "Give a member a staff role."
    options = [
        SlashOption("user", "user", "Member to promote.", required=True),
        SlashOption("role", "string", "Role to assign.", required=True,
                    choices=[r.value for r in ASSIGNABLE_ROLES]),
    ]

    def parse_message(self, raw: str) -> dict:
        """``m.rank <@user|user_id> <role>`` — the role is the remainder."""
        parts = (raw or "").strip().split(None, 1)
        return {
            "user_id": parts[0] if parts else "",
            "role": parts[1].strip() if len(parts) > 1 else "",
        }

    async def execute(self, ctx):
        bot = ctx.bot
        locale = ctx.locale
        usage = "m.rank <@user|user_id> <role>"

        target = ctx.opt("user")
        uid = target.id if target else parse_user_id(ctx.opt("user_id") or "")
        role = resolve_role(ctx.opt("role") or "")

        if not uid:
            await ctx.send(view=design.invalid_usage(locale, usage))
            return

        if role is None:
            await ctx.send(view=design.error(
                t("staff.manage.rank.unknown_role_title", locale=locale),
                t("staff.manage.rank.unknown_role", locale=locale,
                  roles=", ".join(f"`{r.value}`" for r in ASSIGNABLE_ROLES)),
            ))
            return

        if target and target.bot:
            await ctx.send(view=design.error(
                t("staff.manage.staff.bot_title", locale=locale),
                t("staff.manage.staff.bot", locale=locale),
            ))
            return

        try:
            user = await bot.fetch_user(uid)
        except discord.NotFound:
            await ctx.send(view=design.error(
                t("staff.team.user_notfound_title", locale=locale),
                t("staff.team.user_notfound", locale=locale, id=f"`{uid}`"),
            ))
            return

        if user.bot:
            await ctx.send(view=design.error(
                t("staff.manage.staff.bot_title", locale=locale),
                t("staff.manage.staff.bot", locale=locale),
            ))
            return

        user_data = await bot.db.get_user(uid)
        is_staff = bool(user_data["attributes"].get("TEAM"))

        # Same hierarchy gate as /manage staff: an existing staff member (or a
        # dev) may only be edited by someone above them.
        if (is_staff or bot.is_developer(uid)) and not await staff_permissions.can_modify_user(ctx.author.id, uid):
            await ctx.send(view=design.permission_denied(locale, t("staff.manage.hierarchy", locale=locale)))
            return

        if not await staff_permissions.can_assign_role(ctx.author.id, role):
            await ctx.send(view=design.permission_denied(
                locale,
                t("staff.manage.staff.cannot_assign", locale=locale,
                  roles=get_role_display_name(role.value)),
            ))
            return

        perms = await bot.db.get_staff_permissions(uid)
        if role.value in perms["roles"]:
            await ctx.send(view=design.info(
                t("staff.manage.rank.already_title", locale=locale),
                t("staff.manage.rank.already", locale=locale, user=user.mention,
                  role=f"{badges.role_badge(role.value)} {get_role_display_name(role.value)}"),
            ))
            return

        # Adds the role and sets the TEAM attribute (see
        # db/repositories/staff.py::set_staff_roles).
        await bot.db.add_staff_role(uid, role.value, ctx.author.id)
        logger.info("Staff %s granted role %s to %s", ctx.author.id, role.value, uid)

        roles = [StaffRole(r) for r in (await bot.db.get_staff_permissions(uid))["roles"]]
        await ctx.send(view=design.success(
            t("staff.manage.rank.done_title", locale=locale),
            t("staff.manage.rank.done", locale=locale, user=user.mention,
              role=f"{badges.role_badge(role.value)} {get_role_display_name(role.value)}"),
            fields=[{"name": t("staff.manage.staff.roles", locale=locale),
                     "value": " ".join(f"{badges.role_badge(r.value)} {get_role_display_name(r.value)}" for r in roles)}],
            footer=t("staff.manage.rank.hint", locale=locale),
        ))
