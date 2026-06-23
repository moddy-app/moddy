"""`/manage unrank` — remove a member from the staff team."""

import logging

import discord

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType, parse_user_id, ConfirmView
from utils import emojis
from utils.i18n import t
from utils.staff_permissions import staff_permissions

logger = logging.getLogger("moddy.staff.manage.unrank")


@staff_command
class UnrankCommand(StaffCommand):
    command_type = CommandType.MANAGEMENT
    name = "unrank"
    description = "Remove a member from the staff team."
    options = [
        SlashOption("user", "user", "Member to remove.", required=False),
        SlashOption("user_id", "string", "Member id (optional).", required=False),
    ]

    def parse_message(self, raw: str) -> dict:
        return {"user_id": (raw or "").strip()}

    async def execute(self, ctx):
        bot = ctx.bot
        locale = ctx.locale

        target = ctx.opt("user")
        uid = target.id if target else parse_user_id(ctx.opt("user_id") or "")
        if not uid:
            await ctx.send(view=design.invalid_usage(locale, "m.unrank <@user|user_id>"))
            return

        try:
            user = await bot.fetch_user(uid)
        except discord.NotFound:
            await ctx.send(view=design.error(
                t("staff.team.user_notfound_title", locale=locale),
                t("staff.team.user_notfound", locale=locale, id=f"`{uid}`"),
            ))
            return

        user_data = await bot.db.get_user(uid)
        if not user_data["attributes"].get("TEAM"):
            await ctx.send(view=design.error(
                t("staff.manage.not_staff_title", locale=locale),
                t("staff.manage.not_staff", locale=locale, user=user.mention),
            ))
            return

        if not await staff_permissions.can_modify_user(ctx.author.id, uid):
            await ctx.send(view=design.permission_denied(locale, t("staff.manage.hierarchy", locale=locale)))
            return

        async def _do_unrank(interaction):
            await bot.db.remove_staff_permissions(uid)
            await bot.db.set_attribute("user", uid, "TEAM", False, ctx.author.id, "Removed from staff via unrank")
            logger.info("Staff %s removed %s from staff", ctx.author.id, uid)
            return design.success(
                t("staff.manage.unrank.done_title", locale=locale),
                t("staff.manage.unrank.done", locale=locale, user=user.mention),
            )

        await ctx.send(view=ConfirmView(
            bot=bot, author_id=ctx.author.id, locale=locale,
            title=t("staff.manage.unrank.confirm_title", locale=locale),
            description=t("staff.manage.unrank.confirm", locale=locale, user=user.mention),
            on_confirm=_do_unrank, emoji=emojis.LOGOUT,
        ))
