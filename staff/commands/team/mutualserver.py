"""`/team mutualserver` — servers shared between Moddy and a user."""

import discord

from staff.framework import StaffCommand, SlashOption, staff_command, design, CommandType, parse_user_id
from staff.framework import badges
from utils import emojis
from utils.i18n import t


def _key_perms(member: discord.Member, locale: str) -> str:
    perms = member.guild_permissions
    if perms.administrator:
        return "Administrator"
    labels = []
    if perms.manage_guild:
        labels.append("Manage Server")
    if perms.manage_channels:
        labels.append("Manage Channels")
    if perms.manage_roles:
        labels.append("Manage Roles")
    if perms.ban_members:
        labels.append("Ban")
    if perms.kick_members:
        labels.append("Kick")
    if perms.moderate_members:
        labels.append("Timeout")
    return ", ".join(labels) if labels else t("staff.team.mutual.no_perms", locale=locale)


@staff_command
class MutualServerCommand(StaffCommand):
    command_type = CommandType.TEAM
    name = "mutualserver"
    description = "List servers shared between Moddy and a user."
    options = [
        SlashOption("user", "user", "Target user.", required=False),
        SlashOption("user_id", "string", "Target user id.", required=False),
    ]

    def parse_message(self, raw: str) -> dict:
        return {"user_id": (raw or "").strip()}

    async def execute(self, ctx):
        bot = ctx.bot
        locale = ctx.locale

        target = ctx.opt("user")
        user_id = target.id if target else parse_user_id(ctx.opt("user_id") or "")
        if not user_id:
            await ctx.send(view=design.invalid_usage(locale, "t.mutualserver <user_id>"))
            return

        try:
            user = await bot.fetch_user(user_id)
        except discord.NotFound:
            await ctx.send(view=design.error(
                t("staff.team.user_notfound_title", locale=locale),
                t("staff.team.user_notfound", locale=locale, id=f"`{user_id}`"),
            ))
            return

        rendered, _, _ = await badges.render_user(bot, user)
        mutual = [g for g in bot.guilds if g.get_member(user_id)]

        if not mutual:
            await ctx.send(view=design.info(
                t("staff.team.mutual.none_title", locale=locale),
                t("staff.team.mutual.none", locale=locale, user=rendered),
            ))
            return

        fields = []
        for guild in mutual[:10]:
            member = guild.get_member(user_id)
            if not member:
                continue
            top_role = member.top_role.name if member.top_role.name != "@everyone" else "—"
            fields.append({
                "name": f"{emojis.WEB} {guild.name}",
                "value": (
                    f"**ID:** `{guild.id}`\n"
                    f"**{t('staff.team.mutual.top_role', locale=locale)}:** `{top_role}`\n"
                    f"**{t('staff.team.mutual.perms', locale=locale)}:** {_key_perms(member, locale)}"
                ),
            })
        if len(mutual) > 10:
            fields.append({"name": "—", "value": f"-# +{len(mutual) - 10}"})

        await ctx.send(view=design.panel(
            "info",
            t("staff.team.mutual.title", locale=locale, count=len(mutual)),
            rendered,
            fields=fields,
            emoji=emojis.GROUPS,
            accent="primary",
        ))
