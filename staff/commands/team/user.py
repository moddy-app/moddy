"""`/team user` — detailed information about a user (Discord + Moddy data)."""

import discord

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType, parse_user_id
from staff.framework import badges
from utils import emojis
from utils.i18n import t


@staff_command
class UserCommand(StaffCommand):
    command_type = CommandType.TEAM
    name = "user"
    description = "Detailed info about a user (Discord + Moddy data)."
    options = [
        SlashOption("user", "user", "Target user.", required=True),
    ]

    def parse_message(self, raw: str) -> dict:
        return {"user_id": (raw or "").strip()}

    async def execute(self, ctx):
        bot = ctx.bot
        locale = ctx.locale

        target = ctx.opt("user")
        user_id = target.id if target else parse_user_id(ctx.opt("user_id") or "")
        if not user_id:
            await ctx.send(view=design.invalid_usage(locale, "t.user <user_id>"))
            return

        try:
            user = await bot.fetch_user(user_id)
        except discord.NotFound:
            await ctx.send(view=design.error(
                t("staff.team.user_notfound_title", locale=locale),
                t("staff.team.user_notfound", locale=locale, id=f"`{user_id}`"),
            ))
            return

        attributes, verification = await badges.fetch_verification(bot, user_id)
        rendered, orgs, tier = badges.render_name(user, attributes, verification)

        fields = [{
            "name": f"{emojis.USER} {t('staff.team.user.basic', locale=locale)}",
            "value": (
                f"**ID:** `{user.id}`\n"
                f"**{t('staff.team.user.username', locale=locale)}:** `@{user.name}`\n"
                f"**{t('staff.team.user.bot', locale=locale)}:** `{'yes' if user.bot else 'no'}`\n"
                f"**{t('staff.team.user.created', locale=locale)}:** <t:{int(user.created_at.timestamp())}:R>"
            ),
        }]

        if attributes:
            attr_lines = [f"`{k}`" + (f": `{v}`" if v is not True else "") for k, v in attributes.items()]
            fields.append({"name": f"{emojis.SETTINGS} {t('staff.team.attributes', locale=locale)}",
                           "value": " • ".join(attr_lines)})
        else:
            fields.append({"name": f"{emojis.SETTINGS} {t('staff.team.attributes', locale=locale)}",
                           "value": f"-# {t('staff.team.none', locale=locale)}"})

        shared = sum(1 for g in bot.guilds if g.get_member(user_id))
        fields.append({"name": f"{emojis.WEB} {t('staff.team.user.shared', locale=locale)}",
                       "value": f"`{shared}`"})

        if bot.db:
            try:
                db_data = await bot.db.get_user(user_id)
                if db_data and db_data.get("created_at"):
                    fields.append({"name": f"{emojis.TIME} {t('staff.team.user.first_seen', locale=locale)}",
                                   "value": f"<t:{int(db_data['created_at'].timestamp())}:R>"})
            except Exception:
                pass

        description = rendered
        if tier == "org_member" and orgs:
            description += f"\n-# {t('staff.common.affiliation', locale=locale, orgs=', '.join(orgs))}"

        await ctx.send(view=design.panel(
            "info",
            t("staff.team.user.title", locale=locale),
            description,
            fields=fields,
            emoji=emojis.USER,
            accent="primary",
        ))
