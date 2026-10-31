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
    name = "mutual_servers"
    aliases = ("mutualserver",)
    description = "List servers shared between Moddy and a user."
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
        # Every mini-bot answers too (minibots/queries.py): Moddy has no
        # mini-bot cache, the mini-bots process does.
        from minibots.queries import ask
        minibots = await ask(bot, "mutual_guilds", {"user_id": str(user_id)})
        minibot_count = sum(len((r or {}).get("guilds", [])) for r in (minibots or {}).values()
                            if isinstance(r, dict))

        if not mutual and not minibot_count:
            description = t("staff.team.mutual.none", locale=locale, user=rendered)
            if minibots is None:
                description += "\n-# " + t("staff.team.mutual.minibots_unreachable", locale=locale)
            await ctx.send(view=design.info(
                t("staff.team.mutual.none_title", locale=locale), description,
            ))
            return

        fields = []
        if mutual:
            fields.append({"name": f"{emojis.MODDY} Moddy", "value": f"`{len(mutual)}`"})
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

        fields.extend(minibot_fields(minibots, locale))

        description = rendered
        if minibots is None:
            description += "\n-# " + t("staff.team.mutual.minibots_unreachable", locale=locale)
        await ctx.send(view=design.panel(
            "info",
            t("staff.team.mutual.title", locale=locale, count=len(mutual) + minibot_count),
            description,
            fields=fields[:24],
            emoji=emojis.GROUPS,
            accent="primary",
        ))


def minibot_fields(results, locale: str) -> list:
    """One field per mini-bot sharing a server with the user."""
    from minibots.spec import app_display_name
    fields = []
    for app, result in sorted((results or {}).items()):
        if not isinstance(result, dict) or not result.get("guilds"):
            continue
        lines = []
        for row in result["guilds"][:8]:
            perms = ", ".join(_PERM_LABELS.get(p, p) for p in row.get("perms") or []) \
                or t("staff.team.mutual.no_perms", locale=locale)
            lines.append(f"**{row['name']}** `{row['id']}` • `{row.get('top_role') or '—'}` • {perms}")
        if len(result["guilds"]) > 8:
            lines.append(f"-# +{len(result['guilds']) - 8}")
        if result.get("truncated"):
            lines.append("-# " + t("staff.team.mutual.minibot_truncated", locale=locale,
                                   scanned=result.get("scanned"), total=result.get("total")))
        fields.append({"name": f"{emojis.MODDY} {app_display_name(app)} ({len(result['guilds'])})",
                       "value": "\n".join(lines)[:1000]})
    return fields


_PERM_LABELS = {
    "administrator": "Administrator", "manage_guild": "Manage Server",
    "manage_channels": "Manage Channels", "manage_roles": "Manage Roles",
    "ban_members": "Ban", "kick_members": "Kick", "moderate_members": "Timeout",
}
